"""Worker event tests: the worker serves no request and knows no chat client."""

from unittest.mock import AsyncMock

from whenever import Instant

from squid.events import DomainEvent, DomainEventDelivery, UnsupportedEventVersionError
from squid.voting.domain import BuildVoteTarget, VoteSessionResult, VoteSessionSnapshot, VoteStatus
from squid.worker.events import ApplyBuildVoteOutcomeHandler, CoreDomainEventRunner, MaterializeNotificationHandler
from tests.helpers.voting import build_snapshot


def _event() -> DomainEvent:
    return DomainEvent(
        id=1,
        event_type="vote_session.closed",
        aggregate_kind="vote_session",
        aggregate_id=7,
        occurred_at=Instant.now(),
    )


def _snapshot(result: VoteSessionResult) -> VoteSessionSnapshot:
    return build_snapshot(
        id=7,
        author_account_id=1,
        status=VoteStatus.CLOSED,
        result=result,
        pass_threshold=1,
        fail_threshold=-1,
        messages=(),
        target=BuildVoteTarget(42),
    )


async def test_apply_build_vote_outcome_names_no_chat_client() -> None:
    votes = AsyncMock()
    votes.get_session_by_id.return_value = _snapshot(VoteSessionResult.APPROVED)
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


async def test_notification_handler_accepts_current_account_keyed_build_event_versions() -> None:
    notifications = AsyncMock()
    handler = MaterializeNotificationHandler(notifications)

    events = tuple(
        DomainEvent(
            id=index,
            event_type=event_type,
            aggregate_kind="build",
            aggregate_id=42,
            occurred_at=Instant.now(),
            schema_version=schema_version,
        )
        for index, (event_type, schema_version) in enumerate(
            (("build.submitted", 2), ("build.confirmed", 3), ("build.denied", 3)),
            start=1,
        )
    )

    for event in events:
        await handler.handle(event)

    assert notifications.materialize.await_count == len(events)
    assert [call.args for call in notifications.materialize.await_args_list] == [(event,) for event in events]
