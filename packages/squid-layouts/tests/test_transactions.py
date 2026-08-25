"""What a transaction actually covers, and what it says about what it does not."""

import asyncio
import logging
from dataclasses import dataclass

import pytest

from squid_layouts import Component, computed, state
from squid_layouts.primitives import Text
from squid_layouts.runtime import ComponentRuntime, ReactiveWriteError, UndeclaredStateError, join_action, transaction
from squid_layouts.runtime.reactivity import (
    ActionCommit,
    block_writes,
    export_state,
    on_action_commit,
    readonly_transaction,
    restore_state,
)


class Uncopyable:
    """Stands in for a service, guild, or session: real, useful, and not deep-copyable."""

    def __deepcopy__(self, memo: dict[int, object]) -> Uncopyable:
        message = "a reference-copied field was deep-copied"
        raise AssertionError(message)


class Panel(Component):
    declared: int = state(0)
    service: Uncopyable = state(opaque=True)
    handles: list[Uncopyable] = state(opaque=True)

    def __init__(self, service: Uncopyable) -> None:
        self.service = service
        self.handles = [service]
        self.undeclared = "before"

    def render(self):
        return Text(str(self.declared))


def attached[ComponentT: Component](component: ComponentT) -> ComponentT:
    """Give a component a runtime, which is what makes its writes state changes."""
    ComponentRuntime(component)
    return component


class TestUndeclaredWrites:
    def test_read_only_actions_reject_them(self):
        panel = attached(Panel(Uncopyable()))
        with pytest.raises(ReactiveWriteError, match=r"Panel\.undeclared"), readonly_transaction():
            panel.undeclared = "after"
        assert panel.undeclared == "before"

    def test_a_writable_action_rejects_them_too(self):
        panel = attached(Panel(Uncopyable()))
        with pytest.raises(UndeclaredStateError, match=r"Panel\.undeclared"), transaction():
            panel.undeclared = "after"

    def test_the_attribute_is_left_unwritten(self):
        """Raising before the write lands is the point: a landed write is never rolled back."""
        panel = attached(Panel(Uncopyable()))
        with pytest.raises(UndeclaredStateError), transaction():
            panel.undeclared = "after"
        assert panel.undeclared == "before"

    def test_declared_writes_say_nothing(self):
        panel = attached(Panel(Uncopyable()))
        with transaction():
            panel.declared = 1
        assert panel.declared == 1

    def test_constructing_a_component_is_not_a_write(self):
        """A handler may build a child; its __init__ is not a mutation of anything mounted."""
        service = Uncopyable()
        with readonly_transaction():
            fresh = Panel(service)
        assert fresh.undeclared == "before"

    def test_a_component_born_earlier_is_covered_even_when_out_of_the_tree(self):
        """Being unmounted is not being new: such a component may be about to go back in."""
        panel = Panel(Uncopyable())
        with pytest.raises(UndeclaredStateError, match=r"Panel\.undeclared"), transaction():
            panel.undeclared = "after"

    def test_a_component_born_earlier_cannot_be_mutated_by_a_read_only_action(self):
        panel = Panel(Uncopyable())
        with pytest.raises(ReactiveWriteError), readonly_transaction():
            panel.declared = 1
        assert panel.declared == 0

    def test_a_component_born_mid_action_stays_exempt_after_construction(self):
        with readonly_transaction():
            fresh = Panel(Uncopyable())
            fresh.declared = 5
            fresh.undeclared = "after"
        assert fresh.declared == 5
        assert fresh.undeclared == "after"

    def test_the_tree_walker_may_write_its_own_bookkeeping(self):
        """Rendering assigns _runtime and _parent; that is not an author's undeclared write."""
        runtime = ComponentRuntime(Panel(Uncopyable()))
        with transaction():
            runtime.commit(runtime.render())


class TestOpaqueState:
    def test_it_is_snapshotted_without_copying(self):
        original, replacement = Uncopyable(), Uncopyable()
        panel = attached(Panel(original))
        with pytest.raises(RuntimeError, match="abort"), transaction():
            panel.service = replacement
            message = "abort"
            raise RuntimeError(message)
        assert panel.service is original

    def test_it_holds_a_value_the_immutability_check_would_refuse(self):
        """The escape hatch is the whole point: a collaborator is not a snapshot."""
        panel = attached(Panel(Uncopyable()))
        assert type(panel.handles) is list

    def test_it_cannot_be_persisted(self):
        with pytest.raises(TypeError, match="not serializable"):
            state(opaque=True, persist=True)

    def test_it_stays_out_of_snapshots(self):
        panel = attached(Panel(Uncopyable()))
        assert set(export_state(panel)) == {"declared"}


class TestStaging:
    """Writes are held in the transaction's overlay until it commits."""

    def test_an_action_reads_its_own_writes(self):
        panel = attached(Panel(Uncopyable()))
        with transaction():
            panel.declared = 7
            assert panel.declared == 7
        assert panel.declared == 7

    def test_another_task_does_not(self):
        """The reason writes stage rather than write through: a shared read crossing an
        `await` must not see an action that has not committed."""
        panel = attached(Panel(Uncopyable()))
        seen: dict[str, int] = {}

        async def both() -> None:
            staged, observed = asyncio.Event(), asyncio.Event()

            async def action() -> None:
                with transaction():
                    panel.declared = 7
                    staged.set()
                    await observed.wait()
                    seen["action"] = panel.declared

            async def bystander() -> None:
                await staged.wait()
                seen["bystander"] = panel.declared
                observed.set()

            async with asyncio.TaskGroup() as group:
                group.create_task(action())
                group.create_task(bystander())

        asyncio.run(both())
        assert seen == {"bystander": 0, "action": 7}
        assert panel.declared == 7

    def test_rolling_back_is_dropping_the_overlay(self):
        panel = attached(Panel(Uncopyable()))
        with pytest.raises(RuntimeError, match="abort"), transaction():
            panel.declared = 7
            panel.declared = 9
            message = "abort"
            raise RuntimeError(message)
        assert panel.declared == 0

    def test_the_delta_reports_the_first_value_and_the_last(self):
        panel = attached(Panel(Uncopyable()))
        panel.declared = 1
        seen: list[ActionCommit] = []
        with transaction():
            on_action_commit(lambda commit, aftermath: seen.append(commit))
            panel.declared = 2
            panel.declared = 3
        (commit,) = seen
        (patch,) = commit.patches.patches
        assert (patch.before.value, patch.after.value) == (1, 3)

    def test_a_computed_sees_the_staged_value(self):
        """Read-your-writes has to reach derived values, or an action renders its own past."""

        class Derived(Component):
            count: int = state(0)

            @computed
            def doubled(self) -> int:
                return self.count * 2

            def render(self):
                return Text(str(self.doubled))

        component = attached(Derived())
        with transaction():
            component.count = 4
            assert component.doubled == 8
        assert component.doubled == 8

    def test_a_rolled_back_computed_goes_back_with_its_source(self):
        class Derived(Component):
            count: int = state(0)

            @computed
            def doubled(self) -> int:
                return self.count * 2

            def render(self):
                return Text(str(self.doubled))

        component = attached(Derived())
        assert component.doubled == 0
        with pytest.raises(RuntimeError, match="abort"), transaction():
            component.count = 4
            assert component.doubled == 8
            message = "abort"
            raise RuntimeError(message)
        assert component.doubled == 0


class TestStateWithoutAnInitialValue:
    def test_leaving_it_unassigned_fails_at_construction(self):
        """Like a dataclass field with no default, not like a bare annotation."""

        class Late(Component):
            value: int = state()

            def __init__(self, *, assign: bool) -> None:
                if assign:
                    self.value = 1

            def render(self):
                return Text("")

        assert Late(assign=True).value == 1
        with pytest.raises(TypeError, match=r"Late\.__init__ left declared state unassigned: value"):
            Late(assign=False)

    def test_a_subclass_may_assign_it_after_calling_super(self):
        """The base's wrapper must not fire before the subclass has finished."""

        class Base(Component):
            value: int = state()

            def __init__(self) -> None:
                self.marker = True

            def render(self):
                return Text("")

        class Derived(Base):
            def __init__(self) -> None:
                super().__init__()
                self.value = 2

        assert Derived().value == 2

    def test_a_subclass_inheriting_a_constructor_is_still_checked(self):
        class Base(Component):
            def __init__(self) -> None:
                self.ready = True

            def render(self):
                return Text("")

        class Derived(Base):
            value: int = state()

        with pytest.raises(TypeError, match=r"Derived\.__init__ left declared state unassigned: value"):
            Derived()

    def test_reading_an_unassigned_field_is_still_guarded(self):
        """A backstop for construction paths that bypass __init__ entirely."""

        class Late(Component):
            value: int = state()

            def render(self):
                return Text("")

        with pytest.raises(AttributeError, match=r"Late\.value was never assigned"):
            _ = Late.__new__(Late).value

    def test_it_round_trips_through_a_snapshot(self):
        class Late(Component):
            value: int = state()

            def __init__(self, value: int) -> None:
                self.value = value

            def render(self):
                return Text("")

        assert export_state(Late(3)) == {"value": 3}
        restored = Late(0)
        restore_state(restored, {"value": 3})
        assert restored.value == 3

    def test_it_still_rolls_back(self):
        class Late(Component):
            value: int = state()

            def __init__(self) -> None:
                self.value = 1

            def render(self):
                return Text("")

        component = attached(Late())
        with pytest.raises(RuntimeError, match="abort"), transaction():
            component.value = 2
            message = "abort"
            raise RuntimeError(message)
        assert component.value == 1


class TestMutatedInPlace:
    def test_it_schedules_a_draw_for_a_change_nothing_observed(self):
        panel = Panel(Uncopyable())
        runtime = ComponentRuntime(panel)
        runtime.commit(runtime.render())
        assert runtime.dirty is False

        panel.mutated(panel.service)

        assert runtime.dirty is True

    def test_it_rejects_an_object_no_opaque_field_holds(self):
        """The point of passing the object: the call breaks when the declaration goes away."""
        panel = attached(Panel(Uncopyable()))
        with pytest.raises(TypeError, match="no opaque state on Panel holds"):
            panel.mutated(Uncopyable())

    def test_it_stages_the_signal_with_the_action_that_made_it(self):
        """The object changed for good; announcing it is still the action's to take back."""
        panel = Panel(Uncopyable())
        runtime = ComponentRuntime(panel)
        runtime.commit(runtime.render())
        assert runtime.dirty is False

        with pytest.raises(RuntimeError, match="abort"), transaction():
            panel.mutated(panel.service)
            assert runtime.dirty is False, "nothing is announced while the action can still fail"
            message = "abort"
            raise RuntimeError(message)

        assert runtime.dirty is False

    def test_it_announces_a_committed_signal_once(self):
        panel = Panel(Uncopyable())
        runtime = ComponentRuntime(panel)
        runtime.commit(runtime.render())

        with transaction():
            panel.mutated(panel.service)
            panel.mutated(panel.service)

        assert runtime.dirty is True


class TestAbstractBases:
    def test_an_unimplemented_component_may_leave_state_to_its_subclasses(self):
        class BasePanel(Component):
            profile: str = state()

            def __init__(self, name: str) -> None:
                self.name = name

        class Panel(BasePanel):
            def __init__(self, name: str) -> None:
                super().__init__(name)
                self.profile = "loaded"

            def render(self):
                return Text(self.profile)

        assert Panel("x").profile == "loaded"

    def test_an_abc_base_burdens_only_its_concrete_subclass(self):
        from abc import ABC, abstractmethod

        class BasePanel(Component, ABC):
            profile: str = state()

            @abstractmethod
            def title(self) -> str: ...

            def render(self):
                return Text(self.profile)

        class Panel(BasePanel):
            def __init__(self) -> None:
                self.profile = "loaded"

            def title(self) -> str:
                return "t"

        assert Panel().profile == "loaded"

        class Forgetful(BasePanel):
            def title(self) -> str:
                return "t"

        with pytest.raises(TypeError, match=r"Forgetful\.__init__ left declared state unassigned"):
            Forgetful()

    def test_the_concrete_subclass_is_still_checked(self):
        class BasePanel(Component):
            profile: str = state()

        class Panel(BasePanel):
            def render(self):
                return Text(self.profile)

        with pytest.raises(TypeError, match=r"Panel\.__init__ left declared state unassigned: profile"):
            Panel()


class TestActionCommitHooks:
    """The seam an undo entry's state capture comes from (see `sl.history`)."""

    def test_the_delta_carries_both_directions(self):
        panel = attached(Panel(Uncopyable()))
        assert panel.declared == 0
        seen: list[ActionCommit] = []
        with transaction():
            on_action_commit(lambda commit, aftermath: seen.append(commit))
            panel.declared = 7
        (commit,) = seen
        (patch,) = commit.patches.patches
        assert (patch.before.value, patch.after.value) == (0, 7)

    def test_a_field_still_on_its_default_is_recorded_as_unset(self):
        """Its slot is materialized lazily, so restoring it means popping it again."""
        panel = attached(Panel(Uncopyable()))
        seen: list[ActionCommit] = []
        with transaction():
            on_action_commit(lambda commit, aftermath: seen.append(commit))
            panel.declared = 7
        (commit,) = seen
        (patch,) = commit.patches.patches
        assert not patch.before.present

    def test_a_rollback_runs_no_hooks(self):
        panel = attached(Panel(Uncopyable()))
        seen: list[ActionCommit] = []
        with pytest.raises(RuntimeError), transaction():
            on_action_commit(lambda commit, aftermath: seen.append(commit))
            panel.declared = 7
            message = "the action failed"
            raise RuntimeError(message)
        assert seen == []

    def test_reference_copied_state_is_not_copied_on_the_way_out(self):
        service = Uncopyable()
        panel = attached(Panel(service))
        seen: list[ActionCommit] = []
        with transaction():
            on_action_commit(lambda commit, aftermath: seen.append(commit))
            panel.service = Uncopyable()
        (commit,) = seen
        (patch,) = commit.patches.patches
        assert patch.before.value is service

    def test_one_key_registers_once(self):
        key = object()
        with transaction():
            on_action_commit(lambda commit, aftermath: None, key=key)
            with pytest.raises(RuntimeError, match="already registered"):
                on_action_commit(lambda commit, aftermath: None, key=key)

    def test_blocked_writes_name_their_reason(self):
        panel = attached(Panel(Uncopyable()))
        with transaction():
            with pytest.raises(ReactiveWriteError, match="busy reversing"), block_writes("busy reversing"):
                panel.declared = 7
            # The block is scoped, not terminal: the transaction is still writable after it.
            panel.declared = 9
        assert panel.declared == 9


class Watched(Panel):
    """A panel that records the notifications a commit sends it."""

    def __init__(self, service: Uncopyable) -> None:
        # Before the base constructor: assigning declared state notifies straight away.
        self.notified: list[frozenset[str]] = []
        super().__init__(service)

    def _state_changed(self, names: frozenset[str]) -> None:
        self.notified.append(names)
        super()._state_changed(names)


def watched() -> Watched:
    """An attached panel whose notification log starts after construction."""
    panel = attached(Watched(Uncopyable()))
    panel.notified.clear()
    return panel


@dataclass
class Recorder:
    """A participant that logs the protocol calls it receives."""

    log: list[str]
    name: str
    fail: str = ""
    """Which call raises, if any."""
    applied: str = ""
    """What `apply` was handed, if it ran."""

    def _step(self, step: str) -> None:
        self.log.append(f"{self.name}.{step}")
        if self.fail == step:
            message = f"{self.name} rejected the action"
            raise RuntimeError(message)

    def prepare(self, view) -> str:
        self._step("prepare")
        return f"{self.name}.prepared"

    def describe_change(self, prepared: str) -> None:
        return None

    def apply(self, prepared: str) -> None:
        # Recorded, so the ordering tests also prove each participant is handed back its
        # own prepared value rather than another's.
        self.applied = prepared
        self._step("apply")

    def abort(self, prepared: str | None, cause: BaseException) -> None:
        self._step("abort")

    def finalize(self, prepared: str) -> None:
        self._step("finalize")


class TestActionParticipants:
    """The seam a subsystem with its own writes commits through (see plan 40)."""

    def test_there_is_nothing_to_join_outside_an_action(self):
        """The caller's signal that its write has nothing to wait for."""
        assert join_action(object(), lambda: Recorder([], "store")) is None

    def test_one_key_enlists_once(self):
        log: list[str] = []
        key = object()
        with transaction():
            first = join_action(key, lambda: Recorder(log, "a"))
            second = join_action(key, lambda: Recorder(log, "b"))
        assert first is second
        assert log == ["a.prepare", "a.apply", "a.finalize"]

    def test_every_participant_prepares_before_any_applies(self):
        log: list[str] = []
        with transaction():
            first = join_action(object(), lambda: Recorder(log, "a"))
            second = join_action(object(), lambda: Recorder(log, "b"))
        assert log == [
            "a.prepare",
            "b.prepare",
            "a.apply",
            "b.apply",
            "a.finalize",
            "b.finalize",
        ]
        assert first is not None and second is not None
        # Held across the whole prepare pass and handed back to its own author, so a
        # participant never has to find its prepared work on itself.
        assert (first.applied, second.applied) == ("a.prepared", "b.prepared")

    def test_a_participant_that_stages_nothing_still_applies(self):
        """`prepare` may return `None`; `apply` is total either way."""
        log: list[str] = []

        class Silent:
            def prepare(self, view) -> None:
                log.append("prepare")

            def describe_change(self, prepared: None) -> None:
                return None

            def apply(self, prepared: None) -> None:
                assert prepared is None
                log.append("apply")

            def abort(self, prepared: None, cause: BaseException) -> None: ...

            def finalize(self, prepared: None) -> None: ...

        with transaction():
            join_action(object(), Silent)
        assert log == ["prepare", "apply"]

    def test_a_rejected_prepare_applies_nothing_and_rolls_state_back(self):
        log: list[str] = []
        panel = watched()
        with pytest.raises(RuntimeError, match="a rejected the action"), transaction():
            panel.declared = 7
            join_action(object(), lambda: Recorder(log, "a", fail="prepare"))
            join_action(object(), lambda: Recorder(log, "b"))
        assert "a.apply" not in log and "b.apply" not in log
        assert log.count("a.abort") == 1 and log.count("b.abort") == 1
        assert panel.declared == 0
        assert panel.notified == []

    def test_a_later_rejection_aborts_the_participant_that_already_prepared(self):
        log: list[str] = []
        with pytest.raises(RuntimeError, match="b rejected"), transaction():
            join_action(object(), lambda: Recorder(log, "a"))
            join_action(object(), lambda: Recorder(log, "b", fail="prepare"))
        assert log == ["a.prepare", "b.prepare", "b.abort", "a.abort"]

    def test_a_failed_action_aborts_without_preparing(self):
        log: list[str] = []
        with pytest.raises(RuntimeError, match="the action failed"), transaction():
            join_action(object(), lambda: Recorder(log, "a"))
            message = "the action failed"
            raise RuntimeError(message)
        assert log == ["a.abort"]

    def test_an_abort_that_fails_does_not_replace_the_action_s_error(self, caplog: pytest.LogCaptureFixture):
        log: list[str] = []
        with (
            caplog.at_level(logging.ERROR),
            pytest.raises(RuntimeError, match="the action failed"),
            transaction(),
        ):
            join_action(object(), lambda: Recorder(log, "a", fail="abort"))
            join_action(object(), lambda: Recorder(log, "b"))
            message = "the action failed"
            raise RuntimeError(message)
        # The siblings still get their abort, and the swallowed failure is still visible.
        assert log == ["b.abort", "a.abort"]
        assert "failed to abort" in caplog.text

    def test_finalizing_sees_a_fully_published_action(self):
        """Everything a finalize might wake must read the whole action, not half of it."""
        log: list[str] = []
        panel = watched()

        def check(prepared: str) -> None:
            log.append(f"declared={panel.declared}")

        with transaction():
            panel.declared = 7
            recorder = join_action(object(), lambda: Recorder(log, "a"))
            assert recorder is not None
            recorder.finalize = check  # type: ignore[bad-assignment]
        assert log == ["a.prepare", "a.apply", "declared=7"]
        assert panel.notified == [frozenset({"__state_declared"})]

    def test_parallel_read_actions_cannot_stage_writes(self):
        with pytest.raises(ReactiveWriteError, match="parallel-read"), readonly_transaction():
            join_action(object(), lambda: Recorder([], "a"))

    def test_a_blocked_write_reaches_a_participant_already_enlisted(self):
        """An undo inverse may not stage shared writes either; the restore would clobber them."""
        log: list[str] = []
        key = object()
        with transaction():
            join_action(key, lambda: Recorder(log, "a"))
            with pytest.raises(ReactiveWriteError, match="busy reversing"), block_writes("busy reversing"):
                join_action(key, lambda: Recorder(log, "a"))


class TestCommitFailures:
    """A commit is two halves, and only the first one can be taken back."""

    def test_a_raising_hook_leaves_the_action_committed_and_reported(self):
        """The recorder is what failed, not the action; silently un-rendering it is worse."""
        panel = watched()

        def explode(commit: ActionCommit, aftermath) -> None:
            message = "the recorder failed"
            raise RuntimeError(message)

        with transaction():
            on_action_commit(explode)
            panel.declared = 7
        assert panel.declared == 7
        assert panel.notified == [frozenset({"__state_declared"})]

    def test_hooks_run_after_the_action_is_visible(self):
        """A recorder's effect outlives the transaction, so nothing may fail after it."""
        log: list[str] = []
        panel = watched()
        with transaction():
            on_action_commit(lambda commit, aftermath: log.append("hook"))
            join_action(object(), lambda: Recorder(log, "a"))
            panel.declared = 7
        assert log == ["a.prepare", "a.apply", "a.finalize", "hook"]


class TestPrePublicationRollback:
    """What a failed action puts back, and what it must leave alone."""

    def test_a_failed_action_does_not_clobber_a_write_it_never_saw(self):
        """Rollback restores the overlay, and before publication the overlay never left.

        Component state cannot show this -- one owner writes it -- but a shared cell can, and
        restoring `before` over a value another action committed meanwhile would revert a
        write this action never observed.
        """
        component = attached(Panel(Uncopyable()))
        with pytest.raises(RuntimeError, match="abort"), transaction():
            component.declared = 1
            component.__dict__["__state_declared"].write(99)
            message = "abort"
            raise RuntimeError(message)
        assert component.declared == 99

    def test_a_failed_action_still_restores_what_it_published(self):
        """A participant rejecting after publication is the one path that writes cells back."""

        class Rejects:
            def prepare(self, view) -> None:
                message = "rejected"
                raise RuntimeError(message)

            def describe_change(self, prepared: None) -> None:
                return None

            def apply(self, prepared: None) -> None: ...

            def abort(self, prepared: None, cause: BaseException) -> None: ...

            def finalize(self, prepared: None) -> None: ...

        component = attached(Panel(Uncopyable()))
        with pytest.raises(RuntimeError, match="rejected"), transaction():
            component.declared = 1
            join_action(self, Rejects)
        assert component.declared == 0
