"""Candidates settle across delivery, cancellation, and terminal view lifetimes."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, override

import anyio
import pytest

from squid_ui import Component, ComponentsV2Target, state
from squid_ui.errors import LayoutInvariantError
from squid_ui.primitives import Text
from squid_ui.profiling import OperationRecorder, PresentationStatus
from squid_ui.runtime.component import ComponentTree
from squid_ui_discord import Everyone, MessageRoot, MessageRootScheduler, RenewEphemeral, delivery
from squid_ui_discord.message_payload import MessagePayload
from squid_ui_discord.message_root_candidates import _Candidate, _LifecycleCandidate
from squid_ui_discord.message_root_contracts import MessageRootStatus
from squid_ui_discord.testing import delivered_to, interaction_harness, message_harness


class Counter(Component[ComponentsV2Target]):
    count: int = state(0)

    @override
    def render(self) -> Text:
        return Text(str(self.count))


def capture_draws(root: MessageRoot, monkeypatch: pytest.MonkeyPatch) -> list[_Candidate[Any]]:
    candidates: list[_Candidate[Any]] = []
    draw = root._draw

    def capture(tree: ComponentTree[Any, Any], **kwargs: Any) -> _Candidate[Any]:
        candidate = draw(tree, **kwargs)
        candidates.append(candidate)
        return candidate

    monkeypatch.setattr(root, "_draw", capture)
    return candidates


def capture_renewals(root: MessageRoot, monkeypatch: pytest.MonkeyPatch) -> list[_LifecycleCandidate]:
    candidates: list[_LifecycleCandidate] = []
    draw = root._draw_renewal

    def capture(*, disabled: bool = False, profile: OperationRecorder | None = None) -> _LifecycleCandidate:
        candidate = draw(disabled=disabled, profile=profile)
        candidates.append(candidate)
        return candidate

    monkeypatch.setattr(root, "_draw_renewal", capture)
    return candidates


async def test_superseding_a_staged_view_settles_its_candidate() -> None:
    root = MessageRoot(Counter(), access=Everyone(), timeout=None)
    root._stage_view()
    first = root._candidates.pending
    assert first is not None

    root._stage_view()
    second = root._candidates.pending

    assert first.settled and first.view.is_finished()
    assert second is not None and not second.settled
    with pytest.raises(LayoutInvariantError, match="already settled"):
        root._rollback(first)

    await root.send(delivered_to(message_harness()))

    assert second.settled and second.view.is_finished()
    assert root.generation == 3
    assert root._candidates.pending is None


@pytest.mark.parametrize("delivered", [False, True])
async def test_teardown_settles_a_staged_view(delivered: bool) -> None:
    root = MessageRoot(Counter(), access=Everyone(), timeout=None)
    if delivered:
        await root.send(delivered_to(message_harness()))
    root._stage_view()
    candidate = root._candidates.pending
    assert candidate is not None

    await root.finish(disable=delivered)

    assert candidate.settled and candidate.view.is_finished()
    assert root._candidates.pending is None
    assert root._subscriptions.staged is None


async def test_failed_commit_releases_stage_only_ownership_before_teardown() -> None:
    class FailingMount(Counter):
        @override
        def on_mount(self) -> None:
            raise RuntimeError("mount failed")

    root = MessageRoot(FailingMount(), access=Everyone(), timeout=None)
    root._stage_view()
    candidate = root._candidates.pending
    assert candidate is not None

    with pytest.raises(ExceptionGroup, match="component lifecycle hooks failed"):
        root._commit(candidate)

    assert candidate.settled
    assert root._candidates.pending is None
    await root.finish(disable=False)


async def test_cancelling_send_discards_the_candidate_and_allows_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    root = MessageRoot(Counter(), access=Everyone(), timeout=None)
    candidates = capture_draws(root, monkeypatch)

    with anyio.CancelScope() as scope:

        async def cancelled_destination(payload: MessagePayload) -> delivery.DeliveryResult:
            assert payload.view is candidates[0].view
            scope.cancel()
            await anyio.lowlevel.checkpoint()
            raise AssertionError("the destination should have been cancelled")

        await root.send(cancelled_destination)

    assert scope.cancelled_caught
    assert candidates[0].settled and candidates[0].view.is_finished()
    assert root._subscriptions.staged is None
    assert root.generation == 0 and root.pending

    await root.send(delivered_to(message_harness()))

    assert root.generation == 2 and not root.pending


async def test_cancelling_refresh_preserves_live_state(monkeypatch: pytest.MonkeyPatch) -> None:
    panel = Counter()
    root = MessageRoot(panel, access=Everyone(), timeout=None)
    await root.send(delivered_to(message_harness()))
    live_view = root._candidates.view
    live_plan = root.plan
    candidates = capture_draws(root, monkeypatch)
    panel.count = 1

    with anyio.CancelScope() as scope:

        async def cancel_delivery(*args: Any, **kwargs: Any) -> delivery.EditHandle | None:
            scope.cancel()
            await anyio.lowlevel.checkpoint()
            raise AssertionError("the edit should have been cancelled")

        with monkeypatch.context() as patch:
            patch.setattr(root, "_deliver", cancel_delivery)
            await root.refresh()

    assert scope.cancelled_caught
    assert candidates[0].settled and candidates[0].view.is_finished()
    assert root._candidates.view is live_view and root.plan is live_plan
    assert live_view is not None and not live_view.is_finished()
    assert root.generation == 1 and root.pending
    assert root._subscriptions.staged is None

    assert await root.refresh() is PresentationStatus.WRITTEN
    assert root.generation == 3


async def renewal_root() -> MessageRoot:
    now = datetime.now(UTC)
    scheduler = MessageRootScheduler(clock=lambda: now)
    interaction = interaction_harness()
    interaction.expires_at = now + timedelta(seconds=30)
    root = MessageRoot(
        Counter(), access=Everyone(), scheduler=scheduler, timeout=None, expiry=RenewEphemeral(warning=60)
    )
    await root.send(delivered_to(message_harness(ephemeral=True), handle=delivery.handle_from(interaction)))
    assert root.handle is not None
    root._queue_expiry_arm(root.handle)
    return root


async def test_cancelling_renewal_discards_only_the_framework_view(monkeypatch: pytest.MonkeyPatch) -> None:
    root = await renewal_root()
    candidates = capture_renewals(root, monkeypatch)
    live_view = root._candidates.view
    committed = dict(root.runtime.components)

    with anyio.CancelScope() as scope:

        async def cancel_delivery(*args: Any, **kwargs: Any) -> delivery.EditHandle | None:
            scope.cancel()
            await anyio.lowlevel.checkpoint()
            raise AssertionError("the renewal edit should have been cancelled")

        with monkeypatch.context() as patch:
            patch.setattr(root, "_deliver", cancel_delivery)
            await root.refresh()

    assert scope.cancelled_caught
    assert candidates[0].settled and candidates[0].view.is_finished()
    assert root._candidates.view is live_view
    assert root.runtime.components == committed
    assert root.snapshot().lifecycle is MessageRootStatus.ACTIVE
    assert root.generation == 1 and root.pending


async def test_renewal_and_its_terminal_view_settle_without_application_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    root = await renewal_root()
    candidates = capture_renewals(root, monkeypatch)
    applications: list[MessageRoot] = []
    root.on_committed(applications.append)

    assert await root.refresh() is PresentationStatus.WRITTEN

    renewed = candidates[0]
    assert renewed.settled and not renewed.view.is_finished()
    assert applications == []
    with pytest.raises(LayoutInvariantError, match="already settled"):
        root._candidates.commit_renewal(renewed)

    await root.finish()

    terminal = candidates[1]
    assert terminal.settled and terminal.view.is_finished()
    assert renewed.view.is_finished()
    assert applications == []


async def test_terminal_application_view_is_discarded_without_mounting_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mounts: list[str] = []

    class Child(Counter):
        @override
        def on_mount(self) -> None:
            mounts.append("child")

    class Parent(Component[ComponentsV2Target]):
        show_child: bool = state(default=False)

        @override
        def render(self) -> Any:
            return self.boundary(Child(), key="child") if self.show_child else Text("parent")

    parent = Parent()
    root = MessageRoot(parent, access=Everyone(), timeout=None)
    await root.send(delivered_to(message_harness()))
    candidates = capture_draws(root, monkeypatch)
    parent.show_child = True

    await root.finish()

    assert len(candidates) == 1
    assert candidates[0].settled and candidates[0].view.is_finished()
    assert mounts == []
    assert root._subscriptions.staged is None
    assert root.generation == 1


async def test_terminal_acknowledgement_error_does_not_settle_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    root = MessageRoot(Counter(), access=Everyone(), timeout=None)
    await root.send(delivered_to(message_harness()))
    candidates = capture_draws(root, monkeypatch)
    failure = RuntimeError("acknowledgement failed")

    async def abandoned(*args: Any, **kwargs: Any) -> delivery.EditHandle | None:
        return None

    async def fail_acknowledgement(*args: Any, **kwargs: Any) -> None:
        raise failure

    monkeypatch.setattr(root, "_deliver", abandoned)
    monkeypatch.setattr(root, "_acknowledge", fail_acknowledgement)

    with pytest.raises(ExceptionGroup) as caught:
        await root.finish_via(interaction_harness())

    assert caught.value.exceptions == (failure,)
    assert candidates[0].settled and candidates[0].view.is_finished()
    assert root._candidates.view is None


async def test_component_mount_failure_after_delivery_does_not_roll_back(monkeypatch: pytest.MonkeyPatch) -> None:
    failure = RuntimeError("mount failed")
    mounts: list[str] = []

    class FailingMount(Counter):
        @override
        def on_mount(self) -> None:
            mounts.append("mounted")
            raise failure

    panel = FailingMount()
    root = MessageRoot(panel, access=Everyone(), timeout=None)
    candidates = capture_draws(root, monkeypatch)

    with pytest.raises(ExceptionGroup) as caught:
        await root.send(delivered_to(message_harness()))

    assert caught.value.exceptions == (failure,)
    assert candidates[0].settled and not candidates[0].view.is_finished()
    assert root.runtime.components == {"$": panel}
    assert root._subscriptions.staged is None
    # Publication follows runtime commit, so a failed lifecycle hook leaves it untouched.
    assert root.generation == 0

    await root.send(delivered_to(message_harness()))

    assert mounts == ["mounted"]
    assert root.generation == 2


@pytest.mark.parametrize("callback", ["_committed", "_presented"])
async def test_error_after_publication_keeps_the_delivered_generation(
    callback: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = MessageRoot(Counter(), access=Everyone(), timeout=None)
    candidates = capture_draws(root, monkeypatch)
    failure = RuntimeError("publication observer failed")

    def fail() -> None:
        raise failure

    monkeypatch.setattr(root._candidates, callback, fail)

    with pytest.raises(RuntimeError) as caught:
        await root.send(delivered_to(message_harness()))

    assert caught.value is failure
    assert candidates[0].settled and not candidates[0].view.is_finished()
    assert root.generation == 1
    assert root._candidates.view is candidates[0].view
    assert root._subscriptions.staged is None


async def test_suppression_observer_error_does_not_roll_back_committed_state(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    root = MessageRoot(Counter(), access=Everyone(), timeout=None)
    candidates = capture_draws(root, monkeypatch)
    await root.send(delivered_to(message_harness()))
    committed = candidates[0]
    view = root._candidates.view

    def fail() -> None:
        raise RuntimeError("suppression observer failed")

    monkeypatch.setattr(root._candidates, "_committed", fail)

    with caplog.at_level("ERROR"):
        assert await root._present_async_update(committed, through=None, profile=None) is None

    assert "suppression observer failed" in caplog.text
    assert "already settled" not in caplog.text
    assert root._candidates.view is view
    assert root._subscriptions.staged is None
    assert not root.pending


async def test_mount_invalidation_remains_pending_after_publication() -> None:
    class InvalidatingMount(Counter):
        @override
        def on_mount(self) -> None:
            self.count += 1

    root = MessageRoot(InvalidatingMount(), access=Everyone(), timeout=None)
    seen: list[tuple[int, bool]] = []
    observe: Callable[[MessageRoot], None] = lambda current: seen.append((current.generation, current.pending))
    root.on_committed(observe)

    await root.send(delivered_to(message_harness()))

    assert seen == [(1, True)]
    assert await root.refresh() is PresentationStatus.WRITTEN
    assert seen == [(1, True), (2, False)]
