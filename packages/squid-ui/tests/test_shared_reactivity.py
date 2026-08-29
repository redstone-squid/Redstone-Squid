"""How a render finds the shared cells it read, and how a change reaches the bus."""

from dataclasses import dataclass

import pytest

from squid_ui import Component, DiscordTarget, computed, state
from squid_ui.primitives import Boundary, Text
from squid_ui.runtime import CellAddress, ReactiveWriteError, SharedState, addresses, transaction
from squid_ui.runtime.component import render_component_tree
from squid_ui.runtime.shared import describe
from squid_ui.runtime.topics import LocalTopicBus, Topic


@dataclass(frozen=True, slots=True)
class Member:
    user_id: int


class Preferences(SharedState[Member]):
    theme: str = state("system")
    locale: str = state("en")


@pytest.fixture
def bus() -> LocalTopicBus:
    return LocalTopicBus()


@pytest.fixture
def preferences(bus: LocalTopicBus) -> Preferences:
    return Preferences(bus, Member(1))


def address(preferences: Preferences, name: str) -> CellAddress:
    return CellAddress(preferences, name)


# --- Render observation -------------------------------------------------------------------


class Panel(Component[DiscordTarget]):
    show_locale: bool = state(default=False)

    def __init__(self, preferences: Preferences) -> None:
        self.preferences = preferences

    def render(self) -> Text:
        if self.show_locale:
            return Text(f"{self.preferences.theme} {self.preferences.locale}")
        return Text(self.preferences.theme)


def test_a_render_reports_the_shared_cells_it_read(preferences: Preferences) -> None:
    tree = render_component_tree(Panel(preferences))
    assert tree.observations == (address(preferences, "theme"),)


def test_a_dropped_conditional_read_stops_being_observed(preferences: Preferences) -> None:
    panel = Panel(preferences)
    panel.show_locale = True
    assert set(render_component_tree(panel).observations) == {
        address(preferences, "theme"),
        address(preferences, "locale"),
    }
    panel.show_locale = False
    assert render_component_tree(panel).observations == (address(preferences, "theme"),)


def test_repeated_reads_are_one_observation(preferences: Preferences) -> None:
    class Repeats(Component[DiscordTarget]):
        def render(self) -> Text:
            return Text(f"{preferences.theme}{preferences.theme}{preferences.theme}")

    assert render_component_tree(Repeats()).observations == (address(preferences, "theme"),)


def test_a_render_observes_component_state_as_nothing(preferences: Preferences) -> None:
    class Local(Component[DiscordTarget]):
        count: int = state(0)

        def render(self) -> Text:
            return Text(str(self.count))

    assert render_component_tree(Local()).observations == ()


def test_a_cached_computed_still_reports_its_shared_sources(preferences: Preferences) -> None:
    runs: list[int] = []

    class Derived(Component[DiscordTarget]):
        @computed
        def label(self) -> str:
            runs.append(1)
            return preferences.theme.upper()

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
    assert render_component_tree(Panel(preferences)).observations == (address(preferences, "theme"),)


def test_a_write_during_a_render_raises(preferences: Preferences) -> None:
    class Writes(Component[DiscordTarget]):
        def render(self) -> Text:
            preferences.theme = "dark"
            return Text("never")

    with pytest.raises(RuntimeError, match="while a render was reading it"):
        render_component_tree(Writes())
    assert preferences.theme == "system"


def test_an_unaddressed_write_during_a_render_raises_and_tears_no_further() -> None:
    """Component state has no address, but the same render still may not write it back."""

    class Torn(Component[DiscordTarget]):
        n: int = state(0)

        def render(self) -> Text:
            self.n = 1
            return Text(str(self.n))

    torn = Torn()
    with pytest.raises(ReactiveWriteError) as excinfo:
        render_component_tree(torn)
    assert "factory=" in str(excinfo.value)
    assert "computed" in str(excinfo.value)
    assert torn.n == 0


def test_a_component_built_inside_a_parent_render_may_assign_its_own_state() -> None:
    """Construction is not mutation: a child's __init__ runs while the parent is rendering."""

    class Child(Component[DiscordTarget]):
        label: str = state("")

        def __init__(self, label: str) -> None:
            self.label = label

        def render(self) -> Text:
            return Text(self.label)

    class Parent(Component[DiscordTarget]):
        def render(self) -> Boundary:
            return self.boundary(Child("hi"), key="child")

    tree = render_component_tree(Parent())
    child = tree.components["child"]
    assert isinstance(child, Child)
    assert child.label == "hi"


def test_a_component_born_this_render_still_raises_once_its_own_render_runs() -> None:
    """The exemption ends at construction: a child tearing its own tree is not excused."""

    class TornChild(Component[DiscordTarget]):
        n: int = state(0)

        def render(self) -> Text:
            self.n = 1
            return Text(str(self.n))

    class Parent(Component[DiscordTarget]):
        def render(self) -> Boundary:
            return self.boundary(TornChild(), key="child")

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

        def render(self):
            return Text(self.label)

    component = Derived()
    assert addresses(lambda: component.label) == (address(preferences, "locale"),)


def test_addresses_refuses_a_thunk_that_reaches_no_shared_cell() -> None:
    class Local(Component[DiscordTarget]):
        count: int = state(0)

        def render(self) -> Text:
            return Text(str(self.count))

    local = Local()
    with pytest.raises(ValueError, match="read no shared state"):
        addresses(lambda: local.count)
    with pytest.raises(ValueError, match="read no shared state"):
        addresses(lambda: 42)


def test_describe_names_the_namespace_scope_and_cell(preferences: Preferences) -> None:
    assert describe(address(preferences, "theme")) == "Preferences(Member(user_id=1)).theme"
    assert describe(Topic("build", "7")) == "build:7"


# --- Publication ----------------------------------------------------------------------------


async def test_a_commit_publishes_once_to_every_subscriber(bus: LocalTopicBus, preferences: Preferences) -> None:
    seen: list[object] = []

    def subscriber(topic: object) -> None:
        seen.append(topic)

    for _ in range(2):
        bus.subscribe(address(preferences, "theme"), subscriber)
    with transaction():
        preferences.theme = "dark"
        preferences.theme = "light"
    assert seen == [address(preferences, "theme")] * 2, "one coalesced delivery per subscriber"


async def test_a_rolled_back_action_publishes_nothing(bus: LocalTopicBus, preferences: Preferences) -> None:
    seen: list[object] = []

    def subscriber(topic: object) -> None:
        seen.append(topic)

    bus.subscribe(address(preferences, "theme"), subscriber)
    with pytest.raises(RuntimeError, match="no"), transaction():
        preferences.theme = "dark"
        message = "no"
        raise RuntimeError(message)
    assert seen == []
