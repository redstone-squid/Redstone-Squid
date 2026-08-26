"""Challenged admission: a guard that asks the actor, and the press its answer resumes."""

from collections.abc import Awaitable, Callable
from typing import Any, cast

import anyio
import discord
import pytest

import squid_ui as sl
import squid_patterns as sp
from squid_discord import Everyone, Mount, delivery
from squid_discord.challenges import ChallengeRunner, DialogPresenter
from squid_discord.mount import ChallengeRequest
from squid_discord.sessions import SessionRegistry
from squid_discord.testing import commit_render, delivered_to, fake_interaction, fake_message
from squid_ui import ActionEvent, Component, state
from squid_ui import form as sl_form
from squid_ui.forms import FormSpec, TextField
from squid_ui.guards import Challenge, ChallengeResolver, GuardDecision, GuardLedger, approvals
from squid_ui.interactions import ActionMode
from squid_ui.profiling import DispatchDisposition, MemoryProfiler, OperationKind
from squid_ui.runtime.reactivity import readonly_transaction, transaction


class _Panel(Component):
    """One guarded action that counts the presses it was actually allowed to run."""

    count: int = state(0)

    def __init__(self, *, guard: sl.guards.Guard, mode: ActionMode = ActionMode.EXCLUSIVE) -> None:
        self.guard = guard
        self.mode = mode

    def render(self):
        return sl.actions(sl.action("Go", self.go, key="go", guard=self.guard, mode=self.mode), key="panel")

    async def go(self, event: ActionEvent) -> None:
        self.count += 1


class _Recorder:
    """A presenter that keeps the request instead of showing anything."""

    def __init__(self) -> None:
        self.requests: list[ChallengeRequest] = []

    async def present(self, request: ChallengeRequest) -> None:
        self.requests.append(request)

    @property
    def only(self) -> ChallengeRequest:
        assert len(self.requests) == 1, f"expected one challenge, got {len(self.requests)}"
        return self.requests[0]


class _Immediate:
    """A supervisor that runs the press inline — for tests that are not about contexts."""

    def __init__(self) -> None:
        self.resumed: list[Callable[[], Awaitable[None]]] = []

    def resume(self, press: Callable[[], Awaitable[None]]) -> None:
        self.resumed.append(press)


def _panel(guard: sl.guards.Guard, **kwargs: Any) -> tuple[_Panel, Mount, _Recorder]:
    presenter = _Recorder()
    panel = _Panel(guard=guard, **kwargs)
    mount = Mount(panel, access=Everyone(), timeout=None, challenge=presenter)
    commit_render(mount)
    return panel, mount, presenter


def _notice_text(interaction: Any) -> list[str]:
    view = interaction.response.send_message.await_args.kwargs["view"]
    return [item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay)]


def _dispositions(profiler: MemoryProfiler) -> list[DispatchDisposition]:
    snapshot = profiler.snapshot()
    traces = (*snapshot.recent, *snapshot.slow, *snapshot.failed, *snapshot.deadline_misses)
    unique = {trace.trace_id: trace for trace in traces if trace.operation is OperationKind.DISPATCH}
    ordered = sorted(unique.values(), key=lambda trace: trace.started)
    return [trace.result.dispatch.disposition for trace in ordered if trace.result.dispatch is not None]


class TestIssuing:
    async def test_a_challenge_admits_nothing_and_leaves_the_panel_free(self):
        panel, mount, presenter = _panel(sp.guards.confirm("Delete everything?"))
        generation = mount.generation

        await mount.dispatch("go", fake_interaction())

        assert panel.count == 0
        assert mount.generation == generation
        assert not mount._action_lock.locked(), "the dialog must not hold the panel's dispatch lock"
        assert presenter.only.key == "go"

    async def test_a_second_press_does_not_wait_behind_an_outstanding_challenge(self):
        panel, mount, presenter = _panel(sp.guards.confirm("Sure?"))

        await mount.dispatch("go", fake_interaction())
        with anyio.fail_after(2):
            await mount.dispatch("go", fake_interaction())

        assert panel.count == 0
        assert len(presenter.requests) == 2

    async def test_the_question_is_traced_as_its_own_disposition(self):
        profiler = MemoryProfiler()
        mount = Mount(
            _Panel(guard=sp.guards.confirm("Sure?")),
            access=Everyone(),
            timeout=None,
            challenge=_Recorder(),
            profiler=profiler,
        )
        commit_render(mount)

        await mount.dispatch("go", fake_interaction())

        assert _dispositions(profiler) == [DispatchDisposition.CHALLENGE_ISSUED]

    async def test_a_mount_with_no_presenter_refuses_rather_than_admitting(self):
        recorded: list[tuple[Any, Exception, str]] = []

        async def hook(interaction: discord.Interaction, error: Exception, source: str) -> None:
            recorded.append((interaction, error, source))

        profiler = MemoryProfiler()
        panel = _Panel(guard=sp.guards.confirm("Sure?"))
        mount = Mount(panel, access=Everyone(), timeout=None, on_error=hook, profiler=profiler)
        commit_render(mount)

        await mount.dispatch("go", fake_interaction())

        assert panel.count == 0
        assert [source for _, _, source in recorded] == ["guard:go"]
        assert _dispositions(profiler) == [DispatchDisposition.GUARD_FAILED]

    async def test_a_form_trigger_cannot_be_challenged(self):
        recorded: list[str] = []

        async def hook(interaction: discord.Interaction, error: Exception, source: str) -> None:
            recorded.append(str(error))

        class Panel(Component):
            def render(self):
                return sl_form(
                    "Rename",
                    FormSpec("Rename", (TextField(key="name", label="Name"),)),
                    key="rename",
                    on_submit=self.submitted,
                    guard=sp.guards.confirm("Sure?"),
                )

            async def submitted(self, event) -> None:  # pragma: no cover - never reached
                raise AssertionError

        mount = Mount(Panel(), access=Everyone(), timeout=None, on_error=hook, challenge=_Recorder())
        commit_render(mount)

        await mount.dispatch("rename", fake_interaction())

        assert recorded == ["a form trigger cannot be challenged, and the guard on 'rename' did"]


class TestResuming:
    async def test_approval_runs_the_press_the_actor_confirmed(self):
        panel, mount, presenter = _panel(sp.guards.confirm("Sure?"))
        await mount.dispatch("go", fake_interaction())

        await presenter.only.approve()

        assert panel.count == 1

    async def test_approval_admits_exactly_one_press(self):
        panel, mount, presenter = _panel(sp.guards.confirm("Sure?"))
        await mount.dispatch("go", fake_interaction())
        request = presenter.only

        await request.approve()
        await request.approve()

        # Two approvals are two presses, and each spends its own: what must not happen is one
        # approval admitting a later, unasked press.
        assert panel.count == 2
        await mount.dispatch("go", fake_interaction())
        assert panel.count == 2

    async def test_an_approval_for_one_action_does_not_admit_another(self):
        class TwoButtons(Component):
            def __init__(self) -> None:
                self.pressed: list[str] = []

            def render(self):
                guard = sp.guards.confirm("Sure?")
                return sl.actions(
                    sl.action("Go", self.go, key="go", guard=guard),
                    sl.action("Stop", self.stop, key="stop", guard=guard),
                    key="panel",
                )

            async def go(self, event: ActionEvent) -> None:
                self.pressed.append("go")

            async def stop(self, event: ActionEvent) -> None:
                self.pressed.append("stop")

        presenter = _Recorder()
        panel = TwoButtons()
        mount = Mount(panel, access=Everyone(), timeout=None, challenge=presenter)
        commit_render(mount)

        await mount.dispatch("go", fake_interaction())
        await presenter.only.approve()
        await mount.dispatch("stop", fake_interaction())

        assert panel.pressed == ["go"]
        assert len(presenter.requests) == 2

    async def test_a_challenge_answered_by_the_wrong_actor_admits_nothing(self):
        panel, mount, presenter = _panel(sp.guards.confirm("Sure?"))
        await mount.dispatch("go", fake_interaction(user_id=11))

        await presenter.only.approve()
        await mount.dispatch("go", fake_interaction(user_id=22))

        assert panel.count == 1
        assert len(presenter.requests) == 2

    async def test_the_whole_funnel_runs_again_on_approval(self):
        allowed = True

        async def check(event) -> bool:
            return allowed

        panel, mount, presenter = _panel(sl.guards.all_of(sl.guards.permission(check), sp.guards.confirm("Sure?")))
        await mount.dispatch("go", fake_interaction())
        allowed = False

        await presenter.only.approve()

        assert panel.count == 0, "permission lost while the dialog was open must still refuse"

    async def test_a_finished_mount_refuses_the_press_it_asked_about(self):
        panel, mount, presenter = _panel(sp.guards.confirm("Sure?"))
        await mount.dispatch("go", fake_interaction())
        await mount.finish(disable=False)

        await presenter.only.approve()

        assert panel.count == 0

    async def test_the_resumed_press_redraws_where_the_mount_already_lives(self):
        panel = _Panel(guard=sp.guards.confirm("Sure?"))
        presenter = _Recorder()
        mount = Mount(panel, access=Everyone(), timeout=None, challenge=presenter)
        # A handle the mount could be tempted to trade away: not the bot's own, so `_renew`
        # would replace it with any fresher one a click carried.
        opening = fake_interaction(message_id=99)
        handle = delivery.handle_from(opening)
        assert handle is not None and not handle.permanent
        await mount.send(delivered_to(fake_message(message_id=99), handle=handle))
        await mount.dispatch("go", fake_interaction(message_id=99))
        address = mount.address

        # Deliberately a click from somewhere else, which is what a dialog's answer would be.
        elsewhere = fake_interaction(message_id=4242)
        await mount._approve_challenge("go", elsewhere, None, "1")

        assert panel.count == 1
        assert mount._handle is handle, "a resumed press must not re-address the mount"
        assert mount.address == address
        elsewhere.response.edit_message.assert_not_awaited()
        elsewhere.edit_original_response.assert_not_awaited()

    async def test_a_resumed_parallel_read_press_keeps_its_own_transaction(self):
        class Reader(Component):
            def __init__(self) -> None:
                # A list, not state: a PARALLEL_READ handler may not write component state.
                self.reads: list[str] = []

            def render(self):
                return sl.actions(
                    sl.action(
                        "Go",
                        self.go,
                        key="go",
                        guard=sp.guards.confirm("Sure?"),
                        mode=ActionMode.PARALLEL_READ,
                    ),
                    key="panel",
                )

            async def go(self, event: ActionEvent) -> None:
                self.reads.append("go")

        presenter = _Recorder()
        panel = Reader()
        mount = Mount(panel, access=Everyone(), timeout=None, challenge=presenter)
        commit_render(mount)
        await mount.dispatch("go", fake_interaction())

        # `readonly_transaction()` raises when nested, so a resumption that had joined the
        # approving press's transaction would fail here rather than merely misbehave.
        await presenter.only.approve()

        assert panel.reads == ["go"]


class TestDeclining:
    async def test_a_decline_runs_nothing_and_leaves_no_approval_behind(self):
        panel, mount, presenter = _panel(sp.guards.confirm("Sure?"))
        await mount.dispatch("go", fake_interaction())

        await presenter.only.decline()
        await mount.dispatch("go", fake_interaction())

        assert panel.count == 0
        assert len(presenter.requests) == 2, "the next press asks again"

    async def test_a_decline_says_so_when_the_challenge_asked_for_wording(self):
        panel, mount, presenter = _panel(sp.guards.confirm("Sure?", on_decline="Nothing was changed."))
        interaction = fake_interaction()
        await mount.dispatch("go", interaction)

        await presenter.only.decline()

        assert _notice_text(interaction) == ["Nothing was changed."]

    async def test_a_decline_is_traced_as_its_own_disposition(self):
        profiler = MemoryProfiler()
        presenter = _Recorder()
        mount = Mount(
            _Panel(guard=sp.guards.confirm("Sure?")),
            access=Everyone(),
            timeout=None,
            challenge=presenter,
            profiler=profiler,
        )
        commit_render(mount)
        await mount.dispatch("go", fake_interaction())

        await presenter.only.decline()

        assert _dispositions(profiler) == [
            DispatchDisposition.CHALLENGE_ISSUED,
            DispatchDisposition.CHALLENGE_DECLINED,
        ]


class TestLedger:
    async def test_a_challenging_pass_spends_no_earlier_guard(self):
        clock = _Clock()
        panel = _Panel(guard=sl.guards.all_of(sl.guards.cooldown(30), sp.guards.confirm("Sure?")))
        presenter = _Recorder()
        mount = Mount(panel, access=Everyone(), timeout=None, challenge=presenter, clock=clock)
        commit_render(mount)

        await mount.dispatch("go", fake_interaction())
        await presenter.only.decline()

        # The cooldown was never spent, so asking again is possible immediately.
        await mount.dispatch("go", fake_interaction())
        assert len(presenter.requests) == 2
        assert panel.count == 0

    async def test_approving_spends_the_earlier_guard_exactly_once(self):
        clock = _Clock()
        panel = _Panel(guard=sl.guards.all_of(sl.guards.cooldown(30), sp.guards.confirm("Sure?")))
        presenter = _Recorder()
        mount = Mount(panel, access=Everyone(), timeout=None, challenge=presenter, clock=clock)
        commit_render(mount)
        await mount.dispatch("go", fake_interaction())

        await presenter.only.approve()

        assert panel.count == 1
        denied = fake_interaction()
        await mount.dispatch("go", denied)
        assert _notice_text(denied) == ["Try again in 30 seconds."]

    async def test_a_denial_still_spends_what_ran_before_it(self):
        # Pinned deliberately: `all_of` has always let an earlier stateful guard record
        # before a later one denies, and buffering a challenge must not change that.
        clock = _Clock()
        ledger = GuardLedger(now=clock).for_action("go")
        guard = sl.guards.all_of(sl.guards.cooldown(30), sl.guards.when(lambda event: False, reason="No."))
        event = _event()

        assert not cast(GuardDecision, await guard.admit(event, ledger)).allowed
        cooldown_only = sl.guards.cooldown(30)
        assert not cast(GuardDecision, await cooldown_only.admit(event, ledger)).allowed


class TestComposition:
    async def test_all_of_stops_at_a_question_it_would_deny_anyway(self):
        asked = False

        def ask(resolver: ChallengeResolver) -> Component:  # pragma: no cover - never built
            raise AssertionError

        class Asking:
            async def admit(self, event, ledger):
                nonlocal asked
                asked = True
                return Challenge(ask)

        guard = sl.guards.all_of(sl.guards.when(lambda event: False, reason="No."), Asking())

        result = await guard.admit(_event(), GuardLedger().for_action("go"))

        assert isinstance(result, GuardDecision) and not result.allowed
        assert not asked

    async def test_any_of_returns_a_question_rather_than_counting_it_as_a_no(self):
        def ask(resolver: ChallengeResolver) -> Component:  # pragma: no cover - never built
            raise AssertionError

        class Asking:
            async def admit(self, event, ledger):
                return Challenge(ask)

        guard = sl.guards.any_of(sl.guards.when(lambda event: False, reason="No."), Asking())

        result = await guard.admit(_event(), GuardLedger().for_action("go"))

        assert isinstance(result, Challenge)

    async def test_any_of_still_reports_its_last_denial(self):
        guard = sl.guards.any_of(
            sl.guards.when(lambda event: False, reason="First."),
            sl.guards.when(lambda event: False, reason="Second."),
        )

        result = await guard.admit(_event(), GuardLedger().for_action("go"))

        assert isinstance(result, GuardDecision)
        assert result.reason == "Second."

    async def test_two_questions_in_one_chain_converge(self):
        panel, mount, presenter = _panel(sl.guards.all_of(sp.guards.confirm("First?"), sp.guards.confirm("Second?")))

        await mount.dispatch("go", fake_interaction())
        await presenter.requests[0].approve()
        assert panel.count == 0, "the second question has not been answered yet"
        await presenter.requests[1].approve()

        assert panel.count == 1


class TestRunner:
    async def test_a_resumed_press_does_not_inherit_the_approving_transaction(self):
        runner = ChallengeRunner()
        nested: list[bool] = []
        done = anyio.Event()

        async def press() -> None:
            try:
                with readonly_transaction():
                    nested.append(False)
            except RuntimeError:
                nested.append(True)
            done.set()

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(runner.run)
            await anyio.sleep(0)
            # Queued from inside an open transaction, which is exactly where the dialog's
            # own handler queues it.
            with transaction():
                runner.resume(press)
            with anyio.fail_after(2):
                await done.wait()
            tasks.cancel_scope.cancel()

        assert nested == [False], "the resumed press ran inside the approving transaction"

    async def test_a_failing_press_does_not_stop_the_runner(self):
        runner = ChallengeRunner()
        ran: list[str] = []
        done = anyio.Event()

        async def broken() -> None:
            ran.append("broken")
            raise RuntimeError("boom")

        async def fine() -> None:
            ran.append("fine")
            done.set()

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(runner.run)
            await anyio.sleep(0)
            runner.resume(broken)
            runner.resume(fine)
            with anyio.fail_after(2):
                await done.wait()
            tasks.cancel_scope.cancel()

        assert ran == ["broken", "fine"]

    async def test_concurrency_bounds_how_many_approved_presses_run_at_once(self):
        runner = ChallengeRunner(concurrency=2)
        release = anyio.Event()
        started = anyio.Event()
        peak = 0
        finished: list[int] = []

        async def press(index: int) -> None:
            nonlocal peak
            peak = max(peak, runner.active_count)
            if runner.active_count == 2:
                started.set()
            await release.wait()
            finished.append(index)

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(runner.run)
            await anyio.sleep(0)
            for index in range(4):
                runner.resume(lambda index=index: press(index))
            with anyio.fail_after(2):
                await started.wait()
            await anyio.sleep(0)

            assert peak == 2, "the runner started more presses than its concurrency bound"

            release.set()
            with anyio.fail_after(2):
                while len(finished) < 4:
                    await anyio.sleep(0)
            tasks.cancel_scope.cancel()

        assert sorted(finished) == [0, 1, 2, 3]
        assert runner.active_count == 0

    async def test_an_approval_past_capacity_is_dropped_rather_than_awaited(self):
        runner = ChallengeRunner(capacity=1, concurrency=1)
        ran: list[int] = []

        async def press(index: int) -> None:
            ran.append(index)

        for index in range(3):
            runner.resume(lambda index=index: press(index))

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(runner.run)
            with anyio.fail_after(2):
                while not ran:
                    await anyio.sleep(0)
            await anyio.sleep(0)
            tasks.cancel_scope.cancel()

        assert ran == [0], "a dropped approval must not run later"

    async def test_a_non_positive_bound_is_refused(self):
        for kwargs in ({"capacity": 0}, {"concurrency": 0}):
            with pytest.raises(ValueError, match="must be positive"):
                ChallengeRunner(**kwargs)


class TestDialog:
    async def test_a_confirmation_asks_in_a_child_and_runs_on_approval(self):
        registry = SessionRegistry()
        runner = ChallengeRunner()
        registry.defaults = registry.defaults.replace(challenge=DialogPresenter(registry, runner))
        panel = _Panel(guard=sp.guards.confirm("Delete everything?"))
        opening = fake_interaction(message_id=99)
        mount = registry.defaults.mount(panel, access=Everyone(), timeout=None)
        await registry.open(mount, delivered_to(fake_message(message_id=99)), key="panel", actor_id=1)

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(runner.run)
            await anyio.sleep(0)
            await mount.dispatch("go", opening)

            session = registry.session_for(mount)
            assert session is not None
            dialog = next(child for child in session.mounts if child is not mount)
            assert panel.count == 0

            with anyio.fail_after(2):
                await dialog.dispatch("confirm.confirm", fake_interaction(message_id=99))
                while panel.count == 0:
                    await anyio.sleep(0)
            tasks.cancel_scope.cancel()

        assert panel.count == 1


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


class _Responder:
    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"a guard touched the responder ({name})")


def _event(actor: str = "7") -> sl.PressEvent:
    return sl.PressEvent(sl.interactions.Actor(actor), cast(sl.interactions.ActionResponder, _Responder()))


def test_the_approvals_bucket_is_scoped_to_one_action_and_one_actor() -> None:
    ledger = GuardLedger()
    assert approvals(ledger.for_action("go"), "1") != approvals(ledger.for_action("stop"), "1")
    assert approvals(ledger.for_action("go"), "1") != approvals(ledger.for_action("go"), "2")
