"""Transport-neutral worker event tests."""

from unittest.mock import AsyncMock

from whenever import Instant

from squid.events import DomainEvent, DomainEventDelivery, UnsupportedEventVersionError
from squid.voting.domain import VoteSessionResultLiteral, VoteSessionSnapshot, VoteTarget
from squid.worker.events import ApplyBuildVoteOutcomeHandler, CoreDomainEventRunner


def _event() -> DomainEvent:
    return DomainEvent(
        id=1,
        event_type="vote_session.closed",
        aggregate_kind="vote_session",
        aggregate_id=7,
        occurred_at=Instant.now(),
    )


def _snapshot(result: VoteSessionResultLiteral) -> VoteSessionSnapshot:
    return VoteSessionSnapshot(
        id=7,
        author_id=1,
        kind="build",
        status="closed",
        result=result,
        pass_threshold=1,
        fail_threshold=1,
        votes={},
        messages=(),
        options=(),
        target=VoteTarget(build_id=42),
    )


async def test_apply_build_vote_outcome_is_transport_neutral() -> None:
    votes = AsyncMock()
    votes.get_session_by_id.return_value = _snapshot("approved")
    builds = AsyncMock()
    handler = ApplyBuildVoteOutcomeHandler(votes, builds)

    await handler.handle(_event())

    builds.confirm.assert_awaited_once_with(42)
    builds.deny.assert_not_awaited()


async def test_core_runner_acknowledges_unhandled_events() -> None:
    delivery = DomainEventDelivery(event=_event(), consumer="core", attempts=0, claimed_at=Instant.now())
    events = AsyncMock()
    events.claim.return_value = (delivery,)
    runner = CoreDomainEventRunner(events, {})

    await runner.process_batch()

    events.complete.assert_awaited_once_with(delivery)
    events.fail.assert_not_awaited()


async def test_core_runner_retries_handler_failure() -> None:
    delivery = DomainEventDelivery(event=_event(), consumer="core", attempts=0, claimed_at=Instant.now())
    events = AsyncMock()
    events.claim.return_value = (delivery,)
    handler = AsyncMock()
    handler.handle.side_effect = RuntimeError("boom")
    runner = CoreDomainEventRunner(events, {"vote_session.closed": (handler,)})

    await runner.process_batch()

    events.fail.assert_awaited_once()
    events.complete.assert_not_awaited()


async def test_core_runner_rejects_unsupported_event_versions_without_retry() -> None:
    delivery = DomainEventDelivery(event=_event(), consumer="core", attempts=0, claimed_at=Instant.now())
    events = AsyncMock()
    events.claim.return_value = (delivery,)
    handler = AsyncMock()
    error = UnsupportedEventVersionError("future")
    handler.handle.side_effect = error
    runner = CoreDomainEventRunner(events, {"vote_session.closed": (handler,)})

    await runner.process_batch()

    events.reject.assert_awaited_once_with(delivery, error)
    events.fail.assert_not_awaited()
    events.complete.assert_not_awaited()
