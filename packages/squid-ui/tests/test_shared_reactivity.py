"""How a render finds the shared cells it read, and how a change reaches the bus."""

from dataclasses import dataclass
from typing import override

import pytest

from squid_reactivity.topics import LocalTopicBus, SubscriptionReconciler, Topic
from squid_ui import Component, DiscordTarget, Reactive, computed, state
from squid_ui.errors import LayoutInvariantError
from squid_ui.primitives import Boundary, Text
from squid_ui.runtime import CellAddress, ReactiveWriteError, addresses, transaction
from squid_ui.runtime.component import render_component_tree


@dataclass(frozen=True, slots=True)
class Member:
    user_id: int


class Preferences(Reactive):
    theme: str = state("system")
    locale: str = state("en")


@pytest.fixture
def bus() -> LocalTopicBus:
    return LocalTopicBus()


@pytest.fixture
def preferences(bus: LocalTopicBus) -> Preferences:
    return Preferences()


def address(preferences: Preferences, name: str) -> CellAddress:
    return CellAddress(preferences, name)


# --- Render observation -------------------------------------------------------------------


class Panel(Component[DiscordTarget]):
    show_locale: bool = state(default=False)

    def __init__(self, preferences: Preferences) -> None:
        self.preferences = preferences

    @override
    def render(self) -> Text:
        if self.show_locale:
            return Text(f"{self.preferences.theme} {self.preferences.locale}")
        return Text(self.preferences.theme)


def test_a_render_reports_the_shared_cells_it_read(preferences: Preferences) -> None:
    panel = Panel(preferences)
    tree = render_component_tree(panel)
    assert tree.observations == (CellAddress(panel, "show_locale"), address(preferences, "theme"))


def test_a_dropped_conditional_read_stops_being_observed(preferences: Preferences) -> None:
    panel = Panel(preferences)
    panel.show_locale = True
    assert set(render_component_tree(panel).observations) == {
        CellAddress(panel, "show_locale"),
        address(preferences, "theme"),
        address(preferences, "locale"),
    }
    panel.show_locale = False
    assert render_component_tree(panel).observations == (
        CellAddress(panel, "show_locale"),
        address(preferences, "theme"),
    )


def test_repeated_reads_are_one_observation(preferences: Preferences) -> None:
    class Repeats(Component[DiscordTarget]):
        @override
        def render(self) -> Text:
            return Text(f"{preferences.theme}{preferences.theme}{preferences.theme}")

    assert render_component_tree(Repeats()).observations == (address(preferences, "theme"),)


def test_a_render_observes_component_state(preferences: Preferences) -> None:
    class Local(Component[DiscordTarget]):
        count: int = state(0)

        @override
        def render(self) -> Text:
            return Text(str(self.count))

    local = Local()
    assert render_component_tree(local).observations == (CellAddress(local, "count"),)


def test_a_cached_computed_still_reports_its_shared_sources(preferences: Preferences) -> None:
    runs: list[int] = []

    class Derived(Component[DiscordTarget]):
        @computed
        def label(self) -> str:
            runs.append(1)
            return preferences.theme.upper()

        @override
        def render(self) -> Text:
            return Text(self.label)

    component = Derived()
    assert render_component_tree(component).observations == (address(preferences, "theme"),)
    # The second render does not re-run the computed, so the dependency has to come from what
    # the node recorded rather than from the read happening again.
    assert render_component_tree(component).observations == (address(preferences, "theme"),)
    assert len(runs) == 1


def test_a_read_outside_a_render_records_no_dependency(preferences: Preferences) -> None:
    assert preferences.theme == "system"
    panel = Panel(preferences)
    assert render_component_tree(panel).observations == (
        CellAddress(panel, "show_locale"),
        address(preferences, "theme"),
    )


def test_a_write_during_a_render_raises(preferences: Preferences) -> None:
    class Writes(Component[DiscordTarget]):
        @override
        def render(self) -> Text:
            preferences.theme = "dark"
            return Text("never")

    with pytest.raises(RuntimeError, match="factory="):
        render_component_tree(Writes())
    assert preferences.theme == "system"


def test_an_unaddressed_write_during_a_render_raises_and_tears_no_further() -> None:
    """Component state has no address, but the same render still may not write it back."""

    class Torn(Component[DiscordTarget]):
        n: int = state(0)

        @override
        def render(self) -> Text:
            self.n = 1
            return Text(str(self.n))

    torn = Torn()
    with pytest.raises(ReactiveWriteError) as excinfo:
        render_component_tree(torn)
    assert "factory=" in str(excinfo.value)
    assert "computed" in str(excinfo.value)
    assert torn.n == 0


def test_component_construction_during_render_is_rejected() -> None:
    """Component lifetimes cannot depend on whether a render is cached."""

    class Child(Component[DiscordTarget]):
        label: str = state("")

        def __init__(self, label: str) -> None:
            self.label = label

        @override
        def render(self) -> Text:
            return Text(self.label)

    class Parent(Component[DiscordTarget]):
        @override
        def render(self) -> Boundary[DiscordTarget]:
            return self.boundary(Child("hi"), key="child")

    with pytest.raises(LayoutInvariantError, match="constructed during rendering"):
        render_component_tree(Parent())


def test_retained_child_cannot_write_during_its_render() -> None:
    """Child rendering has the same write protection as parent rendering."""

    class TornChild(Component[DiscordTarget]):
        n: int = state(0)

        @override
        def render(self) -> Text:
            self.n = 1
            return Text(str(self.n))

    class Parent(Component[DiscordTarget]):
        def __init__(self) -> None:
            self.child = TornChild()

        @override
        def render(self) -> Boundary[DiscordTarget]:
            return self.boundary(self.child, key="child")

    with pytest.raises(ReactiveWriteError, match="factory="):
        render_component_tree(Parent())


# --- Naming an address by hand --------------------------------------------------------------


def test_addresses_names_a_cell_by_reading_it(preferences: Preferences) -> None:
    assert addresses(lambda: preferences.theme) == (address(preferences, "theme"),)


def test_addresses_collects_every_cell_the_thunk_reads(preferences: Preferences) -> None:
    found = addresses(lambda: (preferences.theme, preferences.locale))
    assert set(found) == {address(preferences, "theme"), address(preferences, "locale")}


def test_addresses_sees_through_a_computed(preferences: Preferences) -> None:
    class Derived(Component[DiscordTarget]):
        @computed
        def label(self) -> str:
            return preferences.locale.upper()

        @override
        def render(self):
            return Text(self.label)

    component = Derived()
    assert addresses(lambda: component.label) == (address(preferences, "locale"),)


def test_addresses_refuses_a_thunk_that_reaches_no_shared_cell() -> None:
    class Local(Component[DiscordTarget]):
        count: int = state(0)

        @override
        def render(self) -> Text:
            return Text(str(self.count))

    local = Local()
    assert addresses(lambda: local.count) == (CellAddress(local, "count"),)
    with pytest.raises(ValueError, match="read no reactive state"):
        addresses(lambda: 42)


def test_describe_names_the_namespace_scope_and_cell(preferences: Preferences) -> None:
    assert str(address(preferences, "theme")) == f"{preferences!r}.theme"
    assert str(Topic("build", "7")) == "build:7"


# --- Publication ----------------------------------------------------------------------------


async def test_a_commit_publishes_once_to_every_subscriber(bus: LocalTopicBus, preferences: Preferences) -> None:
    seen: list[object] = []

    def subscriber(topic: object) -> None:
        seen.append(topic)

    subscriptions = [SubscriptionReconciler(None, subscriber) for _ in range(2)]
    for subscription in subscriptions:
        subscription.stage([address(preferences, "theme")])
        subscription.commit()
    with transaction():
        preferences.theme = "dark"
        preferences.theme = "light"
    assert seen == [address(preferences, "theme")] * 2, "one coalesced delivery per subscriber"


async def test_a_rolled_back_action_publishes_nothing(bus: LocalTopicBus, preferences: Preferences) -> None:
    seen: list[object] = []

    def subscriber(topic: object) -> None:
        seen.append(topic)

    subscription = SubscriptionReconciler(None, subscriber)
    subscription.stage([address(preferences, "theme")])
    subscription.commit()
    with pytest.raises(RuntimeError, match="no"), transaction():
        preferences.theme = "dark"
        message = "no"
        raise RuntimeError(message)
    assert seen == []
