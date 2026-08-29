"""What a transaction actually covers, and what it says about what it does not."""

import logging

import pytest

from squid_layouts import (
    Component,
    ReactiveWriteError,
    UndeclaredStateError,
    state,
    strict_state,
    transaction,
)
from squid_layouts.primitives import Text
from squid_layouts.runtime import ComponentRuntime
from squid_layouts.runtime.reactivity import export_state, readonly_transaction, restore_state


class Uncopyable:
    """Stands in for a service, guild, or session: real, useful, and not deep-copyable."""

    def __deepcopy__(self, memo: dict[int, object]) -> Uncopyable:
        message = "a reference-copied field was deep-copied"
        raise AssertionError(message)


class Panel(Component):
    declared: int = state(0)
    service: Uncopyable = state(copy="ref")
    handles: list[Uncopyable] = state(copy="ref")

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

    def test_strict_mode_rejects_them(self):
        panel = attached(Panel(Uncopyable()))
        with pytest.raises(UndeclaredStateError, match=r"Panel\.undeclared"), transaction():
            panel.undeclared = "after"

    def test_otherwise_they_land_and_are_logged(self, caplog: pytest.LogCaptureFixture):
        panel = attached(Panel(Uncopyable()))
        with caplog.at_level(logging.WARNING), strict_state(enabled=False), transaction():
            panel.undeclared = "after"
        assert panel.undeclared == "after"
        assert "Panel.undeclared" in caplog.text
        assert "sl.state()" in caplog.text

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


class TestReferenceCopiedState:
    def test_it_is_snapshotted_without_copying(self):
        original, replacement = Uncopyable(), Uncopyable()
        panel = attached(Panel(original))
        with pytest.raises(RuntimeError, match="abort"), transaction():
            panel.service = replacement
            message = "abort"
            raise RuntimeError(message)
        assert panel.service is original

    def test_its_containers_are_not_proxied(self):
        """Proxying would reintroduce the deep copy that copy="ref" exists to avoid."""
        panel = attached(Panel(Uncopyable()))
        assert type(panel.handles) is list
        with transaction():
            panel.handles.append(Uncopyable())
        assert len(panel.handles) == 2

    def test_it_cannot_be_persisted(self):
        with pytest.raises(TypeError, match="not serializable"):
            state(copy="ref", persist=True)

    def test_it_stays_out_of_snapshots(self):
        panel = attached(Panel(Uncopyable()))
        assert set(export_state(panel)) == {"declared"}


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
        runtime = ComponentRuntime(Panel(Uncopyable()))
        runtime.commit(runtime.render())
        assert runtime.dirty is False

        runtime.root.mutated("service")

        assert runtime.dirty is True

    def test_it_rejects_a_field_that_is_not_declared_state(self):
        """The point of naming the field: the call breaks when the declaration goes away."""
        panel = attached(Panel(Uncopyable()))
        with pytest.raises(TypeError, match=r"Panel\.undeclared is not declared state"):
            panel.mutated("undeclared")


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
