"""State values: what a cell will hold, and when its version moves."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pytest

from squid_ui import Component, DiscordTarget, state
from squid_ui.primitives import Text
from squid_ui.runtime.reactivity import _Cell, _State


@dataclass(frozen=True, slots=True)
class Filters:
    limit: int = 10


class Service:
    """A collaborator a component holds and never mutates."""


def cell_of(component: Component[Any], name: str) -> _Cell:
    descriptor = next(
        vars(klass)[name] for klass in type(component).__mro__ if isinstance(vars(klass).get(name), _State)
    )
    return descriptor.cell(component)


class Panel(Component[DiscordTarget]):
    rows: Sequence[str] = state([])
    channels: Mapping[str, int | None] = state({"log": None})
    filters: Filters = state(Filters())
    service: Service = state(opaque=True)

    def __init__(self, service: Service) -> None:
        self.service = service

    def render(self):
        return Text(str(self.rows))


class TestReplacement:
    def test_a_builtin_container_is_stored_as_assigned(self):
        """Nothing freezes at the boundary: the type checker holds the line, not the runtime."""
        panel = Panel(Service())
        rows = ["a"]
        panel.rows = rows
        assert panel.rows is rows

    def test_a_default_is_shared_rather_than_copied(self):
        first, second = Panel(Service()), Panel(Service())
        assert first.channels is second.channels

    def test_a_replacement_is_a_write(self):
        panel = Panel(Service())
        before = cell_of(panel, "channels").version
        panel.channels = {**panel.channels, "log": 1}
        assert panel.channels == {"log": 1}
        assert cell_of(panel, "channels").version == before + 1


class TestMutated:
    def test_it_moves_the_version_of_the_field_holding_the_object(self):
        panel = Panel(Service())
        before = cell_of(panel, "service").version
        panel.mutated(panel.service)
        assert cell_of(panel, "service").version == before + 1

    def test_a_replaced_value_is_not_a_collaborator(self):
        """A plain container is replaced, never mutated; identity would find it, so refuse it."""
        panel = Panel(Service())
        with pytest.raises(TypeError, match="no opaque state"):
            panel.mutated(panel.channels)

    def test_an_object_held_twice_must_be_named(self):
        class Twice(Component[DiscordTarget]):
            first: Service = state(opaque=True)
            second: Service = state(opaque=True)

            def __init__(self, service: Service) -> None:
                self.first = self.second = service

            def render(self):
                return Text("")

        shared = Service()
        with pytest.raises(TypeError, match="more than one field"):
            Twice(shared).mutated(shared)


class TestOpaqueFields:
    def test_they_hold_a_collaborator(self):
        service = Service()
        panel = Panel(service)
        assert panel.service is service

    def test_they_are_not_persisted_by_default(self):
        from squid_ui.runtime.reactivity import export_state

        assert set(export_state(Panel(Service()))) == {"rows", "channels", "filters"}

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
