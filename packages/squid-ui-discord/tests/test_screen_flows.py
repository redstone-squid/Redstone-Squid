"""Screen flows commit results under fresh authority and own child shutdown."""

from typing import Any, override

import anyio
import pytest
from anyio.abc import TaskStatus

import squid_ui as sl
import squid_ui_discord as sd
from squid_ui.runtime import ManagedService, transaction
from squid_ui_discord.screen_flow import Cancelled, Completed, ScreenCancelReason, ScreenResultEvent
from squid_ui_discord.testing import StagedUI


class Choice(sd.Screen[str]):
    policy = sd.ResponseSpec(session=sd.SessionSpec("choice"))

    def __init__(self) -> None:
        self.fail = False
        self.closed: list[Completed[str] | Cancelled] = []

    @override
    def render(self) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request]:
        return sl.action_controls(
            sl.action_control("Choose", self.choose, key="choose"),
            sl.action_control("Cancel", self.cancel_choice, key="cancel"),
            key="actions",
        )

    async def choose(self, event: sd.PressEvent) -> None:
        self.complete(event, "chosen")
        assert self.outcome is None
        if self.fail:
            raise ValueError("rollback choice")

    async def cancel_choice(self, event: sd.PressEvent) -> None:
        self.cancel(event)

    @override
    async def on_close(self, outcome: Completed[str] | Cancelled) -> None:
        self.closed.append(outcome)


class Parent(sd.Screen[str]):
    policy = sd.ResponseSpec(session=sd.SessionSpec("parent"))
    value: str = sl.state("")

    def __init__(self) -> None:
        self.child = Choice()
        self.presentations: list[sd.Presented[sd.Screen[str]]] = []
        self.cancelled: list[Cancelled] = []
        self.fail_callback = False
        self.finish_in_callback = False
        self.fail_open = False
        self.events: list[ScreenResultEvent[str]] = []

    @override
    def render(self) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request]:
        return sl.stack(
            sl.paragraph(self.value or "Waiting"),
            sl.action_controls(sl.action_control("Open", self.open, key="open"), key="actions"),
        )

    @property
    def presented(self) -> sd.Presented[sd.Screen[str]] | None:
        return self.presentations[-1] if self.presentations else None

    async def open(self, event: sd.PressEvent) -> None:
        result = await self.open_child(event, self.child, on_result=self.accept, on_cancel=self.cancelled_child)
        assert isinstance(result, sd.Presented)
        self.presentations.append(result)
        if self.fail_open:
            raise ValueError("opening action rolled back")

    async def accept(self, event: ScreenResultEvent[str]) -> None:
        assert event.context.root is self._screen_require_root()
        assert event.context.scope is self._screen_flow.scope
        self.value = event.value
        if self.fail_callback:
            raise ValueError("result not accepted")
        self.events.append(event)
        if self.finish_in_callback:
            self.complete(event, event.value)

    async def cancelled_child(self, cancellation: Cancelled) -> None:
        self.cancelled.append(cancellation)


def _child_stage(parent: Parent, ui: StagedUI[Any]) -> StagedUI[sd.Screen[str]]:
    assert parent.presented is not None
    return StagedUI(parent.presented, client=ui._client, user_id=1, interaction=ui.interaction)


async def test_child_result_uses_fresh_parent_authority_and_independent_session() -> None:
    parent = Parent()
    async with sd.testing.stage(parent, owner=object()) as ui:
        await ui.press("open")
        child = _child_stage(parent, ui)
        assert parent.child.parent is parent
        assert parent.session is not parent.child.session
        await child.press("choose")
        assert parent.value == "chosen"
        assert parent.child.outcome == Completed("chosen")
        assert await parent.child.wait() == Completed("chosen")
        assert parent.child.closed == [Completed("chosen")]
        assert not ui.root.finished
        assert child.root.finished


async def test_rollback_and_failed_callback_leave_child_retryable() -> None:
    parent = Parent()
    async with sd.testing.stage(parent, owner=object()) as ui:
        await ui.press("open")
        child = _child_stage(parent, ui)
        parent.child.fail = True
        await child.press("choose")
        assert parent.child.outcome is None
        assert not child.root.finished
        parent.child.fail = False
        parent.fail_callback = True
        await child.press("choose")
        assert parent.value == ""
        assert parent.child.outcome is None
        assert not child.root.finished
        parent.fail_callback = False
        await child.press("choose")
        assert await parent.child.wait() == Completed("chosen")


async def test_parent_can_complete_from_child_callback_without_deadlock() -> None:
    parent = Parent()
    parent.finish_in_callback = True
    async with sd.testing.stage(parent, owner=object()) as ui:
        await ui.press("open")
        child = _child_stage(parent, ui)
        with anyio.fail_after(2):
            await child.press("choose")
            assert await parent.wait() == Completed("chosen")
            assert await parent.child.wait() == Completed("chosen")


@pytest.mark.parametrize("explicit", [True, False])
async def test_child_cancellation_notifies_once(explicit: bool) -> None:
    parent = Parent()
    async with sd.testing.stage(parent, owner=object()) as ui:
        await ui.press("open")
        child = _child_stage(parent, ui)
        if explicit:
            await child.press("cancel")
            expected = Cancelled(ScreenCancelReason.USER)
        else:
            await ui.root.finish()
            expected = Cancelled(ScreenCancelReason.PARENT_CLOSED)
        assert await parent.child.wait() == expected
        assert parent.cancelled == [expected]
        assert parent.child.closed == [expected]
        assert child.root.finished


async def test_opening_rollback_closes_delivered_child() -> None:
    parent = Parent()
    parent.fail_open = True
    async with sd.testing.stage(parent, owner=object()) as ui:
        await ui.press("open")
        with anyio.fail_after(2):
            assert await parent.child.wait() == Cancelled(ScreenCancelReason.CLOSED)
        assert parent.presented is not None and parent.presented.root.finished
        assert not ui.root.finished


async def test_wait_rejects_action_transaction_even_after_completion() -> None:
    screen = Choice()
    async with sd.testing.stage(screen, owner=object()) as ui:
        await ui.press("choose")
        with transaction(), pytest.raises(RuntimeError, match="outside the action transaction"):
            await screen.wait()


async def test_closure_hook_cannot_wait_for_itself() -> None:
    class Observed(Choice):
        @override
        async def on_close(self, outcome: Completed[str] | Cancelled) -> None:
            with pytest.raises(RuntimeError, match="own completion"):
                await self.wait()
            await super().on_close(outcome)

    screen = Observed()
    async with sd.testing.stage(screen, owner=object()) as ui:
        with anyio.fail_after(2):
            await ui.press("choose")
            assert await screen.wait() == Completed("chosen")


@pytest.mark.parametrize(
    "reason", [ScreenCancelReason.TIMEOUT, ScreenCancelReason.SCOPE_CLOSED, ScreenCancelReason.SHUTDOWN]
)
async def test_external_cancellation_records_reason(reason: ScreenCancelReason) -> None:
    screen = Choice()
    async with sd.testing.stage(screen, owner=object()) as ui:
        if reason is ScreenCancelReason.TIMEOUT:
            await ui.root.handle_timeout()
        else:
            assert screen.opening is not None
            if reason is ScreenCancelReason.SHUTDOWN:
                await screen.opening.runtime.close()
            else:
                await screen.opening.scope.close()
        assert await screen.wait() == Cancelled(reason)
        assert screen.closed == [Cancelled(reason)]
        assert screen.close_reason is sd.CloseReason(reason)


async def test_stale_and_foreign_action_events_cannot_complete_screen() -> None:
    captured: list[sd.ActionEvent] = []

    class Capture(Parent):
        @override
        async def open(self, event: sd.PressEvent) -> None:
            captured.append(event)
            await super().open(event)
            with pytest.raises(RuntimeError, match="current action"):
                self.child.complete(event, "invalid")

    screen = Capture()
    async with sd.testing.stage(screen, owner=object()) as ui:
        await ui.press("open")
        with pytest.raises(RuntimeError, match="current action"):
            screen.complete(captured[0], "stale")
        assert screen.outcome is None and screen.child.outcome is None


async def test_waiter_cancellation_does_not_cancel_screen() -> None:
    screen = Choice()
    async with sd.testing.stage(screen, owner=object()) as ui:
        with anyio.move_on_after(0):
            await screen.wait()
        assert screen.outcome is None
        await ui.press("choose")
        assert await screen.wait() == Completed("chosen")


async def test_parent_closure_during_child_load_fails_closed() -> None:
    loading, release = anyio.Event(), anyio.Event()

    class SlowChoice(Choice):
        @override
        async def on_load(self) -> None:
            loading.set()
            await release.wait()

    parent = Parent()
    parent.child = SlowChoice()
    async with sd.testing.stage(parent, owner=object()) as ui:
        with anyio.fail_after(2):
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(ui.press, "open")
                await loading.wait()
                tasks.start_soon(ui.root.finish)
                while not ui.root.finished:
                    await anyio.sleep(0)
                release.set()
            assert isinstance(await parent.child.wait(), Cancelled)
        assert parent.presented is None
        assert parent.cancelled == []
        assert parent.child._screen_require_root().finished


@pytest.mark.parametrize("sessionless", [False, True])
async def test_child_cleanup_precedes_parent_service_cleanup(sessionless: bool) -> None:
    order: list[str] = []

    class Service(ManagedService):
        @override
        async def run(self, *, task_status: TaskStatus[None]) -> None:
            task_status.started()
            try:
                await anyio.sleep_forever()
            finally:
                order.append("service")

    class Borrower(Choice):
        policy = sd.ResponseSpec(session=None) if sessionless else Choice.policy

        @override
        async def on_close(self, outcome: Completed[str] | Cancelled) -> None:
            order.append("child")
            await super().on_close(outcome)

    class ServiceOwner(Parent):
        policy = sd.ResponseSpec(session=None) if sessionless else Parent.policy

    parent = ServiceOwner()
    parent.child = Borrower()
    parent.own(Service())
    async with sd.testing.stage(parent, owner=object()) as ui:
        await ui.press("open")
        if sessionless:
            assert parent.session is None and parent.child.session is None
        await ui.root.finish()
        assert order == ["child", "service"]


async def test_rejected_opening_does_not_invoke_child_cancellation() -> None:
    class Exclusive(Choice):
        policy = sd.ResponseSpec(session=sd.SessionSpec("choice", admission=sd.AdmissionSpec(collision=sd.Reject())))

    class RejectedParent(Parent):
        @override
        async def open(self, event: sd.PressEvent) -> None:
            result = await self.open_child(event, self.child, on_result=self.accept, on_cancel=self.cancelled_child)
            assert isinstance(result, sd.Rejected)

    parent = RejectedParent()
    parent.child = Exclusive()
    async with sd.testing.stage(parent, owner=object()) as ui:
        assert parent.opening is not None
        existing = await parent.opening.respond(Choice())
        assert isinstance(existing, sd.Presented)
        await ui.press("open")
        assert parent.cancelled == []
        assert parent.child.closed == [Cancelled(ScreenCancelReason.CLOSED)]
        assert parent._screen_flow.children == set()
        assert not existing.root.finished


async def test_parent_render_failure_after_commit_still_closes_completed_child() -> None:
    class BrokenDisplay(Parent):
        @override
        def render(self) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request]:
            if self.value:
                message = "cannot draw accepted value"
                raise RuntimeError(message)
            return super().render()

    parent = BrokenDisplay()
    async with sd.testing.stage(parent, owner=object()) as ui:
        await ui.press("open")
        child = _child_stage(parent, ui)
        with pytest.raises(RuntimeError, match="cannot draw accepted value"):
            await child.press("choose")
        assert parent.value == "chosen"
        assert await parent.child.wait() == Completed("chosen")
        assert child.root.finished
        parent.value = ""
