"""State values: what a cell will hold, and when its version moves."""

from dataclasses import dataclass

import pytest

from squid_layouts import Component, MutableStateError, state
from squid_layouts.frozen import FrozenMapping
from squid_layouts.primitives import Text
from squid_layouts.runtime.reactivity import _Cell, _State


@dataclass(frozen=True, slots=True)
class Filters:
    limit: int = 10


@dataclass(frozen=True, slots=True)
class LeakyFilters:
    """Frozen, and still mutable: the shape an annotation check waves through."""

    tags: list[str]


class Service:
    """A collaborator a component holds and never mutates."""


def cell_of(component: Component, name: str) -> _Cell:
    descriptor = next(
        vars(klass)[name] for klass in type(component).__mro__ if isinstance(vars(klass).get(name), _State)
    )
    return descriptor.cell(component)


class Panel(Component):
    rows: tuple[str, ...] = state(())
    filters: Filters = state(Filters())
    service: Service = state(opaque=True)

    def __init__(self, service: Service) -> None:
        self.service = service

    def render(self):
        return Text(str(self.rows))


class TestTheValueCheck:
    def test_a_mutable_container_is_refused(self):
        panel = Panel(Service())
        with pytest.raises(MutableStateError, match=r"Panel\.rows was assigned list"):
            panel.rows = ["a"]  # type: ignore[bad-assignment]

    def test_it_reaches_inside_an_immutable_container(self):
        """The property an annotation check cannot have: `tuple[...]` says nothing about this."""
        panel = Panel(Service())
        with pytest.raises(MutableStateError, match=r"Panel\.rows was assigned tuple"):
            panel.rows = (1, [2])  # type: ignore[bad-assignment]

    def test_it_reaches_inside_a_frozen_dataclass(self):
        panel = Panel(Service())
        with pytest.raises(MutableStateError, match=r"Panel\.filters was assigned LeakyFilters"):
            panel.filters = LeakyFilters(["a"])  # type: ignore[bad-assignment]

    def test_a_mutable_default_fails_at_class_creation(self):
        with pytest.raises(MutableStateError, match=r"Late\.rows was assigned list"):

            class Late(Component):
                rows: tuple[str, ...] = state([])  # type: ignore[bad-argument-type]

    def test_a_mutable_factory_fails_when_it_runs(self):
        class Late(Component):
            rows: tuple[str, ...] = state(factory=list)  # type: ignore[bad-argument-type]

            def render(self):
                return Text("")

        with pytest.raises(MutableStateError, match=r"Late\.rows was assigned list"):
            _ = Late().rows

    def test_the_message_points_at_the_way_out(self):
        panel = Panel(Service())
        with pytest.raises(MutableStateError, match="opaque=True"):
            panel.rows = ["a"]  # type: ignore[bad-assignment]


class TestOpaqueFields:
    def test_they_hold_a_collaborator_the_check_would_refuse(self):
        service = Service()
        panel = Panel(service)
        assert panel.service is service

    def test_they_are_not_persisted_by_default(self):
        from squid_layouts.runtime.reactivity import export_state

        assert set(export_state(Panel(Service()))) == {"rows", "filters"}

    def test_they_cannot_be_persisted_on_request(self):
        with pytest.raises(TypeError, match="not serializable"):
            state(opaque=True, persist=True)

    def test_they_settle_on_identity_rather_than_equality(self):
        """`==` on a collaborator is the author's code, not a cheap settled-value check."""

        class Loud(Service):
            def __eq__(self, other: object) -> bool:
                message = "a collaborator was compared"
                raise AssertionError(message)

            __hash__ = None  # type: ignore[bad-assignment]

        panel = Panel(Loud())
        replacement = Loud()
        panel.service = replacement
        assert panel.service is replacement


class TestVersions:
    def test_a_write_moves_the_version(self):
        panel = Panel(Service())
        before = cell_of(panel, "rows").version
        panel.rows = ("a",)
        assert cell_of(panel, "rows").version == before + 1

    def test_a_write_that_changes_nothing_does_not(self):
        panel = Panel(Service())
        panel.rows = ("a",)
        settled = cell_of(panel, "rows").version
        panel.rows = ("a",)
        assert cell_of(panel, "rows").version == settled

    def test_reading_a_default_is_not_a_write(self):
        panel = Panel(Service())
        assert panel.filters == Filters()
        assert cell_of(panel, "filters").version == 0

    def test_a_default_is_shared_rather_than_copied(self):
        """Nothing copies any more, which an immutable value makes safe."""
        first, second = Panel(Service()), Panel(Service())
        assert first.filters is second.filters


class TestFrozenMapping:
    def test_it_hashes_where_a_proxy_does_not(self):
        from types import MappingProxyType

        assert hash(FrozenMapping({"a": 1})) == hash(FrozenMapping({"a": 1}))
        with pytest.raises(TypeError):
            hash(MappingProxyType({"a": 1}))

    def test_it_compares_as_a_mapping(self):
        assert FrozenMapping({"a": 1, "b": 2}) == {"b": 2, "a": 1}

    def test_it_keeps_insertion_order_for_rendering(self):
        assert list(FrozenMapping({"b": 2, "a": 1})) == ["b", "a"]

    def test_an_unhashable_value_still_fails_the_state_check(self):
        class Holder(Component):
            values: FrozenMapping[str, object] = state(FrozenMapping())

            def render(self):
                return Text("")

        with pytest.raises(MutableStateError):
            Holder().values = FrozenMapping({"a": ["leaky"]})
