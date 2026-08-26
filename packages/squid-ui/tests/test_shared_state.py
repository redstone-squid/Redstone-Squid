"""SharedState namespaces: declaration, lifetime, staging, and the commit precondition."""

import contextvars
import gc
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest

from squid_ui import Component, computed, state
from squid_ui.primitives import Text
from squid_ui.runtime import CellAddress, ReactiveConflictError, SharedState, transaction
from squid_ui.runtime.topics import LocalTopicBus


@dataclass(frozen=True, slots=True)
class Member:
    user_id: int
    guild_id: int


class Preferences(SharedState[Member]):
    theme: str = state("system")
    locale: str = state("en")


class Workspace(SharedState[Member]):
    theme: str = state("unrelated")
    selected: int | None = state(None)
    filters: tuple[str, ...] = state(())


class Anonymous(SharedState):
    flag: bool = state(default=False)


@pytest.fixture
def bus() -> LocalTopicBus:
    return LocalTopicBus()


@pytest.fixture
def here() -> Member:
    return Member(1, 2)


# --- Declaration ------------------------------------------------------------------------


def test_cells_are_read_and_written_as_attributes(bus: LocalTopicBus, here: Member) -> None:
    preferences = Preferences(bus, here)
    assert preferences.theme == "system"
    preferences.theme = "dark"
    assert preferences.theme == "dark"
    assert preferences.locale == "en"


def test_two_namespaces_with_the_same_cell_name_do_not_collide(bus: LocalTopicBus, here: Member) -> None:
    preferences = Preferences(bus, here)
    workspace = Workspace(bus, here)
    preferences.theme = "dark"
    assert workspace.theme == "unrelated"


def test_two_handles_of_one_class_are_separate_state(bus: LocalTopicBus) -> None:
    mine = Preferences(bus, Member(1, 2))
    yours = Preferences(bus, Member(3, 2))
    mine.theme = "dark"
    assert yours.theme == "system"


def test_scope_is_whatever_the_host_gave_it(bus: LocalTopicBus, here: Member) -> None:
    assert Preferences(bus, here).scope == here
    assert Anonymous(bus).scope is None


def test_an_unhashable_or_mutable_scope_is_accepted(bus: LocalTopicBus) -> None:
    mutable = ["guild", 7]
    assert Anonymous(bus, mutable).scope is mutable  # pyrefly: ignore[bad-argument-type]


def test_repr_names_the_class_and_the_scope(bus: LocalTopicBus, here: Member) -> None:
    assert repr(Preferences(bus, here)) == "Preferences(Member(user_id=1, guild_id=2))"
    assert repr(Anonymous(bus)) == "Anonymous()"


def test_a_reserved_name_raises_at_class_creation() -> None:
    with pytest.raises(TypeError, match="reserves 'scope'"):

        class Bad(SharedState[int]):
            scope: int = state(0)


def test_an_underscored_cell_raises_at_class_creation() -> None:
    with pytest.raises(TypeError, match="underscored"):

        class Bad(SharedState[int]):
            _hidden: int = state(0)


def test_one_declaration_serves_both_owners(bus: LocalTopicBus, here: Member) -> None:
    """`sl.state()` everywhere: what it means is what holds it, which the class already says."""

    class Namespace(SharedState[Member]):
        value: int = state(0)

    class Panel(Component):
        value: int = state(0)

        def render(self):
            return Text(str(self.value))

    namespace = Namespace(bus, here)
    panel = Panel()
    namespace.value = 1
    panel.value = 1

    assert (namespace.value, panel.value) == (1, 1)


def test_only_a_namespace_gives_its_state_an_address(bus: LocalTopicBus, here: Member) -> None:
    """The whole difference, and it is a property of the owner rather than the declaration."""

    class Namespace(SharedState[Member]):
        value: int = state(0)

    class Panel(Component):
        value: int = state(0)

        def render(self):
            return Text(str(self.value))

    namespace = Namespace(bus, here)
    assert type(namespace)._state_descriptors["value"].address(namespace) == CellAddress(namespace, "value")
    assert Panel._state_descriptors["value"].address(Panel()) is None


def test_a_namespace_refuses_persistence_it_cannot_honour() -> None:
    with pytest.raises(TypeError, match="never persisted"):

        class Bad(SharedState[int]):
            wrong: int = state(0, persist=True)


def test_a_namespace_field_that_merely_defaulted_to_persist_is_fine(bus: LocalTopicBus, here: Member) -> None:
    """`persist` defaults to True, so only an explicit ask can be refused."""

    class Fine(SharedState[Member]):
        value: int = state(0)

    assert Fine(bus, here).value == 0


def test_an_undeclared_attribute_cannot_be_written(bus: LocalTopicBus, here: Member) -> None:
    preferences = Preferences(bus, here)
    with pytest.raises(AttributeError, match="not declared state"):
        preferences.undeclared = 1


def test_a_cell_cannot_be_deleted(bus: LocalTopicBus, here: Member) -> None:
    """Reset is an assignment. `del` would mean removal, and the attribute stays."""
    preferences = Preferences(bus, here)
    preferences.theme = "dark"
    with pytest.raises(AttributeError):
        del preferences.theme
    assert preferences.theme == "dark"


# --- Values -----------------------------------------------------------------------------


def test_an_equal_write_changes_nothing(bus: LocalTopicBus, here: Member) -> None:
    preferences = Preferences(bus, here)
    preferences.theme = "dark"
    published: list[object] = []
    bus.subscribe(CellAddress(preferences, "theme"), _record(published))
    preferences.theme = "dark"
    assert published == []
    preferences.theme = "light"
    assert len(bus.snapshot().topics) == 1


class Held:
    """A collaborator: real, and its `__eq__` is the author's code, not a settle check."""

    def __eq__(self, other: object) -> bool:
        message = "an opaque cell compared by value"
        raise AssertionError(message)

    __hash__ = None  # type: ignore[assignment]


def test_an_opaque_cell_settles_by_identity(bus: LocalTopicBus) -> None:
    class Services(SharedState):
        held: Held = state(factory=Held, opaque=True)

    services = Services(bus)
    same = services.held
    services.held = same
    services.held = Held()
    assert services.held is not same


def test_a_replaced_collection_is_stored_as_assigned(bus: LocalTopicBus, here: Member) -> None:
    workspace = Workspace(bus, here)
    workspace.filters = (*workspace.filters, "redstone")
    assert workspace.filters == ("redstone",)


# --- Immediate writes and publication -----------------------------------------------------


def _record(into: list[object]):
    def subscriber(topic: object) -> None:
        into.append(topic)

    return subscriber


async def test_a_write_outside_an_action_publishes_immediately(bus: LocalTopicBus, here: Member) -> None:
    preferences = Preferences(bus, here)
    theme = CellAddress(preferences, "theme")
    seen: list[object] = []
    bus.subscribe(theme, _record(seen))
    preferences.theme = "dark"
    assert seen == [theme]


async def test_only_the_cells_that_moved_publish(bus: LocalTopicBus, here: Member) -> None:
    preferences = Preferences(bus, here)
    seen: list[object] = []
    bus.subscribe(CellAddress(preferences, "theme"), _record(seen))
    bus.subscribe(CellAddress(preferences, "locale"), _record(seen))
    with transaction():
        preferences.theme = "dark"
    assert seen == [CellAddress(preferences, "theme")]


async def test_an_in_place_mutation_publishes_with_its_action(bus: LocalTopicBus) -> None:
    """`mutated()` is a write like any other, and a rolled-back one was never published.

    The list itself keeps the appended item -- in place means in place -- but no other mount
    is told to re-read a namespace on behalf of an action that failed.
    """

    class Draft(SharedState):
        body: list[str] = state(factory=list, opaque=True)  # pyrefly: ignore[bad-assignment]

    draft = Draft(bus)
    seen: list[object] = []
    bus.subscribe(CellAddress(draft, "body"), _record(seen))

    with pytest.raises(RuntimeError, match="abort"), transaction():
        draft.body.append("first")
        draft.mutated(draft.body)
        assert seen == [], "nothing is published while the action can still fail"
        message = "abort"
        raise RuntimeError(message)

    assert seen == []

    with transaction():
        draft.body.append("second")
        draft.mutated(draft.body)
    assert seen == [CellAddress(draft, "body")]


# --- Actions ----------------------------------------------------------------------------


async def test_writes_stage_and_publish_together(bus: LocalTopicBus, here: Member) -> None:
    preferences = Preferences(bus, here)
    workspace = Workspace(bus, here)
    seen: list[object] = []
    for handle, name in ((preferences, "theme"), (workspace, "selected")):
        bus.subscribe(CellAddress(handle, name), _record(seen))
    with transaction():
        preferences.theme = "dark"
        assert preferences.theme == "dark", "an action reads its own writes"
        workspace.selected = 7
        assert seen == [], "nothing is published while the action is still running"
    assert len(seen) == 2


def test_a_staged_write_is_not_visible_to_another_reader(bus: LocalTopicBus, here: Member) -> None:
    preferences = Preferences(bus, here)
    observed: list[str] = []
    with transaction():
        preferences.theme = "dark"
        observed.append(elsewhere(lambda: preferences.theme))
    assert observed == ["system"], "no dirty reads: another task never sees a staged value"
    assert preferences.theme == "dark"


def elsewhere[ValueT](what: Callable[[], ValueT]) -> ValueT:
    """Run `what` the way another task would: no transaction of this test's in scope.

    A fresh context, not a copy of this one, so every ContextVar the runtime keys off falls
    back to its default. That is exactly what a second handler on the event loop sees.
    """
    return contextvars.Context().run(what)


async def test_a_failed_action_leaks_no_staged_value(bus: LocalTopicBus, here: Member) -> None:
    preferences = Preferences(bus, here)
    seen: list[object] = []
    bus.subscribe(CellAddress(preferences, "theme"), _record(seen))
    with pytest.raises(RuntimeError, match="handler failed"), transaction():
        preferences.theme = "dark"
        message = "handler failed"
        raise RuntimeError(message)
    assert preferences.theme == "system"
    assert seen == []


def test_one_action_spans_several_namespaces(bus: LocalTopicBus, here: Member) -> None:
    handles = [Workspace(bus, Member(index, 2)) for index in range(3)]
    with pytest.raises(RuntimeError, match="handler failed"), transaction():
        for index, handle in enumerate(handles):
            handle.selected = index
        message = "handler failed"
        raise RuntimeError(message)
    assert [handle.selected for handle in handles] == [None, None, None]


# --- The commit precondition --------------------------------------------------------------


def test_a_read_and_written_cell_conflicts_when_it_moves(bus: LocalTopicBus, here: Member) -> None:
    workspace = Workspace(bus, here)
    conflict = r"Workspace\(Member\(user_id=1, guild_id=2\)\)\.filters"
    with pytest.raises(ReactiveConflictError, match=conflict), transaction():
        workspace.filters = (*workspace.filters, "mine")
        _write_from_elsewhere(workspace, "filters", ("theirs",))
    assert workspace.filters == ("theirs",), "the losing action published nothing"


def _write_from_elsewhere(handle: SharedState[Any], name: str, value: object) -> None:
    """Commit a value the way another task's action would, around this transaction."""
    elsewhere(lambda: setattr(handle, name, value))


def test_a_write_only_cell_is_blind(bus: LocalTopicBus, here: Member) -> None:
    preferences = Preferences(bus, here)
    with transaction():
        preferences.locale = "fr"
        _write_from_elsewhere(preferences, "locale", "de")
    assert preferences.locale == "fr", "last commit wins"


def test_a_read_only_action_never_conflicts(bus: LocalTopicBus, here: Member) -> None:
    preferences = Preferences(bus, here)
    composed: list[str] = []
    with transaction():
        composed.append(preferences.theme)
        _write_from_elsewhere(preferences, "theme", "dark")
    assert composed == ["system"]
    assert preferences.theme == "dark"


def test_reading_a_staged_value_does_not_enter_the_read_set(bus: LocalTopicBus, here: Member) -> None:
    preferences = Preferences(bus, here)
    with transaction():
        preferences.theme = "dark"
        assert preferences.theme == "dark"
        _write_from_elsewhere(preferences, "theme", "light")
    assert preferences.theme == "dark"


def test_a_later_write_does_not_clear_the_guard(bus: LocalTopicBus, here: Member) -> None:
    preferences = Preferences(bus, here)
    with pytest.raises(ReactiveConflictError), transaction():
        seen = preferences.theme
        preferences.theme = f"{seen}+"
        preferences.theme = "final"
        _write_from_elsewhere(preferences, "theme", "moved")


def test_a_b_a_conflicts_by_version_lineage(bus: LocalTopicBus, here: Member) -> None:
    preferences = Preferences(bus, here)
    with pytest.raises(ReactiveConflictError), transaction():
        seen = preferences.theme
        preferences.theme = f"{seen}!"
        _write_from_elsewhere(preferences, "theme", "dark")
        _write_from_elsewhere(preferences, "theme", "system")
    assert preferences.theme == "system"


def test_a_conflict_publishes_nothing_at_all(bus: LocalTopicBus, here: Member) -> None:
    preferences = Preferences(bus, here)
    workspace = Workspace(bus, here)
    with pytest.raises(ReactiveConflictError), transaction():
        workspace.selected = 9
        preferences.theme = f"{preferences.theme}!"
        _write_from_elsewhere(preferences, "theme", "dark")
    assert workspace.selected is None


def test_local_state_rolls_back_with_a_conflict(bus: LocalTopicBus, here: Member) -> None:
    preferences = Preferences(bus, here)

    class Panel(Component):
        open: bool = state(default=False)

    panel = Panel()
    with pytest.raises(ReactiveConflictError), transaction():
        panel.open = True
        preferences.theme = f"{preferences.theme}!"
        _write_from_elsewhere(preferences, "theme", "dark")
    assert panel.open is False


def test_outside_an_action_every_write_is_blind(bus: LocalTopicBus, here: Member) -> None:
    preferences = Preferences(bus, here)
    seen = preferences.theme
    _write_from_elsewhere(preferences, "theme", "dark")
    preferences.theme = f"{seen}!"
    assert preferences.theme == "system!"


# --- Computed over shared state -----------------------------------------------------------


def test_a_computed_recomputes_when_another_owner_writes(bus: LocalTopicBus, here: Member) -> None:
    workspace = Workspace(bus, here)
    runs: list[int] = []

    class Detail(Component):
        def __init__(self, workspace: Workspace) -> None:
            self.workspace = workspace

        @computed
        def title(self) -> str:
            runs.append(1)
            return f"build {self.workspace.selected}"

    detail = Detail(workspace)
    assert detail.title == "build None"
    assert detail.title == "build None"
    assert len(runs) == 1
    workspace.selected = 7
    assert detail.title == "build 7"
    assert len(runs) == 2


# --- Lifetime ---------------------------------------------------------------------------


async def test_a_namespace_with_no_holder_is_collected(bus: LocalTopicBus, here: Member) -> None:
    workspace = Workspace(bus, here)
    gone = weakref.ref(workspace)
    unfollow = bus.subscribe(CellAddress(workspace, "selected"), _record([]))
    workspace.selected = 3
    # Both halves of what keeps a namespace alive: the handle itself, and the unfollow that
    # closes over its address. A mount holds exactly these two and drops both on finish.
    unfollow()
    del unfollow, workspace
    gc.collect()
    assert gone() is None, "the handle is the state, so nothing outlives its last holder"
