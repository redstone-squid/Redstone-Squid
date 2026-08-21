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

    def test_an_unattached_component_is_not_reported(self):
        panel = Panel(Uncopyable())
        with transaction():
            panel.undeclared = "after"
        assert panel.undeclared == "after"

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
    def test_reading_before_assignment_is_an_error(self):
        class Late(Component):
            value: int = state()

            def render(self):
                return Text(str(self.value))

        with pytest.raises(AttributeError, match=r"Late\.value was never assigned"):
            _ = Late().value

    def test_it_is_omitted_from_snapshots_until_assigned(self):
        class Late(Component):
            value: int = state()

            def render(self):
                return Text("")

        component = Late()
        assert export_state(component) == {}
        component.value = 3
        assert export_state(component) == {"value": 3}
        restored = Late()
        restore_state(restored, {"value": 3})
        assert restored.value == 3

    def test_it_still_rolls_back(self):
        class Late(Component):
            value: int = state()

            def render(self):
                return Text("")

        component = attached(Late())
        component.value = 1
        with pytest.raises(RuntimeError, match="abort"), transaction():
            component.value = 2
            message = "abort"
            raise RuntimeError(message)
        assert component.value == 1
