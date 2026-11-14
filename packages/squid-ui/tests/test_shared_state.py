"""Reactive namespaces: declaration, lifetime, staging, and the commit precondition."""

import contextvars
import gc
import weakref
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import override

import pytest

from squid_reactivity import Address, SubscriptionReconciler, UndeclaredStateError, addresses, export_state
from squid_ui import Component, DiscordTarget, Reactive, computed, state
from squid_ui.primitives import Text
from squid_ui.runtime import CellAddress, ReactiveConflictError, transaction


@dataclass(frozen=True, slots=True)
class Member:
    user_id: int
    guild_id: int


class Preferences(Reactive):
    theme: str = state("system")
    locale: str = state("en")


class Workspace(Reactive):
    theme: str = state("unrelated")
    selected: int | None = state(None)
    filters: tuple[str, ...] = state(())


class Anonymous(Reactive):
    flag: bool = state(False)


type Follow = Callable[[CellAddress, Callable[[Address], None]], Callable[[], None]]


@pytest.fixture
def follow() -> Iterator[Follow]:
    subscriptions: list[SubscriptionReconciler] = []

    def observe(address: CellAddress, callback: Callable[[Address], None]) -> Callable[[], None]:
        reconciler = SubscriptionReconciler(None, callback)
        reconciler.stage([address])
        reconciler.commit()
        subscriptions.append(reconciler)

        def close() -> None:
            reconciler.close()
            subscriptions.remove(reconciler)

        return close

    yield observe
    for reconciler in subscriptions:
        reconciler.close()


@pytest.fixture
def here() -> Member:
    return Member(1, 2)


# --- Declaration ------------------------------------------------------------------------


def test_cells_are_read_and_written_as_attributes(follow: Follow, here: Member) -> None:
    preferences = Preferences()
    assert preferences.theme == "system"
    preferences.theme = "dark"
    assert preferences.theme == "dark"
    assert preferences.locale == "en"


def test_two_namespaces_with_the_same_cell_name_do_not_collide(follow: Follow, here: Member) -> None:
    preferences = Preferences()
    workspace = Workspace()
    preferences.theme = "dark"
    assert workspace.theme == "unrelated"


def test_two_handles_of_one_class_are_separate_state(follow: Follow) -> None:
    mine = Preferences()
    yours = Preferences()
    mine.theme = "dark"
    assert yours.theme == "system"


def test_models_can_declare_domain_metadata_and_private_state() -> None:
    class Model(Reactive):
        scope: int = state(0)
        _hidden: int = state(1)

    model = Model()
    assert model.scope == 0
    assert model._hidden == 1


def test_one_declaration_serves_both_owners(follow: Follow, here: Member) -> None:
    """The same descriptor supports component-local and shared state through its owner type."""

    class Namespace(Reactive):
        value: int = state(0)

    class Panel(Component[DiscordTarget]):
        value: int = state(0)

        @override
        def render(self):
            return Text(str(self.value))

    namespace = Namespace()
    panel = Panel()
    namespace.value = 1
    panel.value = 1

    assert (namespace.value, panel.value) == (1, 1)


def test_models_and_components_have_identity_addresses(follow: Follow, here: Member) -> None:

    class Namespace(Reactive):
        value: int = state(0)

    class Panel(Component[DiscordTarget]):
        value: int = state(0)

        @override
        def render(self):
            return Text(str(self.value))

    namespace = Namespace()
    assert addresses(lambda: namespace.value) == (CellAddress(namespace, "value"),)
    panel = Panel()
    assert addresses(lambda: panel.value) == (CellAddress(panel, "value"),)


def test_explicit_persistence_uses_declared_fields() -> None:
    class Model(Reactive):
        saved: int = state(1, persist=True)
        transient: int = state(2, persist=False)

    assert export_state(Model()) == {"saved": 1}


def test_an_undeclared_attribute_cannot_be_written(follow: Follow, here: Member) -> None:
    preferences = Preferences()
    with pytest.raises(UndeclaredStateError, match="not declared state"), transaction():
        preferences.undeclared = 1


def test_a_cell_cannot_be_deleted(follow: Follow, here: Member) -> None:
    """Reset is an assignment. `del` would mean removal, and the attribute stays."""
    preferences = Preferences()
    preferences.theme = "dark"
    with pytest.raises(AttributeError):
        del preferences.theme
    assert preferences.theme == "dark"


# --- Values -----------------------------------------------------------------------------


def test_an_equal_write_changes_nothing(follow: Follow, here: Member) -> None:
    preferences = Preferences()
    preferences.theme = "dark"
    published: list[object] = []
    follow(CellAddress(preferences, "theme"), _record(published))
    preferences.theme = "dark"
    assert published == []
    preferences.theme = "light"
    assert published == [CellAddress(preferences, "theme")]


class Held:
    """Fail loudly if opaque state compares values instead of object identity."""

    @override
    def __eq__(self, other: object) -> bool:
        message = "an opaque cell compared by value"
        raise AssertionError(message)

    __hash__ = None  # type: ignore[assignment]


def test_an_opaque_cell_settles_by_identity(follow: Follow) -> None:
    class Services(Reactive):
        held: Held = state(factory=Held, opaque=True)

    services = Services()
    same = services.held
    services.held = same
    services.held = Held()
    assert services.held is not same


def test_a_replaced_collection_is_stored_as_assigned(follow: Follow, here: Member) -> None:
    workspace = Workspace()
    workspace.filters = (*workspace.filters, "redstone")
    assert workspace.filters == ("redstone",)


# --- Immediate writes and publication -----------------------------------------------------


def _record(into: list[object]):
    def subscriber(topic: object) -> None:
        into.append(topic)

    return subscriber


async def test_a_write_outside_an_action_publishes_immediately(follow: Follow, here: Member) -> None:
    preferences = Preferences()
    theme = CellAddress(preferences, "theme")
    seen: list[object] = []
    follow(theme, _record(seen))
    preferences.theme = "dark"
    assert seen == [theme]


async def test_only_the_cells_that_moved_publish(follow: Follow, here: Member) -> None:
    preferences = Preferences()
    seen: list[object] = []
    follow(CellAddress(preferences, "theme"), _record(seen))
    follow(CellAddress(preferences, "locale"), _record(seen))
    with transaction():
        preferences.theme = "dark"
    assert seen == [CellAddress(preferences, "theme")]


async def test_an_in_place_mutation_publishes_with_its_action(follow: Follow) -> None:
    """`mutated()` is a write like any other, and a rolled-back one was never published.

    The list itself keeps the appended item, but subscribers receive no notification
    from the failed transaction.
    """

    class Draft(Reactive):
        body: list[str] = state(factory=list, opaque=True)  # pyright: ignore[reportAssignmentType]  # pyrefly: ignore[bad-assignment]

    draft = Draft()
    seen: list[object] = []
    follow(CellAddress(draft, "body"), _record(seen))

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


# --- Transaction staging -------------------------------------------------------------------


async def test_writes_stage_and_publish_together(follow: Follow, here: Member) -> None:
    preferences = Preferences()
    workspace = Workspace()
    seen: list[object] = []
    for handle, name in ((preferences, "theme"), (workspace, "selected")):
        follow(CellAddress(handle, name), _record(seen))
    with transaction():
        preferences.theme = "dark"
        assert preferences.theme == "dark", "an action reads its own writes"
        workspace.selected = 7
        assert seen == [], "nothing is published while the action is still running"
    assert len(seen) == 2


def test_a_staged_write_is_not_visible_to_another_reader(follow: Follow, here: Member) -> None:
    preferences = Preferences()
    observed: list[str] = []
    with transaction():
        preferences.theme = "dark"
        observed.append(elsewhere(lambda: preferences.theme))
    assert observed == ["system"], "no dirty reads: another task never sees a staged value"
    assert preferences.theme == "dark"


def elsewhere[ValueT](what: Callable[[], ValueT]) -> ValueT:
    """Run a synchronous read or write in a fresh context with no ambient transaction.

    This simulates an unrelated handler without scheduling a task. A task spawned inside
    the current transaction would normally inherit its context instead.
    """
    return contextvars.Context().run(what)


async def test_a_failed_action_leaks_no_staged_value(follow: Follow, here: Member) -> None:
    preferences = Preferences()
    seen: list[object] = []
    follow(CellAddress(preferences, "theme"), _record(seen))
    with pytest.raises(RuntimeError, match="handler failed"), transaction():
        preferences.theme = "dark"
        message = "handler failed"
        raise RuntimeError(message)
    assert preferences.theme == "system"
    assert seen == []


def test_one_action_spans_several_namespaces(follow: Follow, here: Member) -> None:
    handles = [Workspace() for index in range(3)]
    with pytest.raises(RuntimeError, match="handler failed"), transaction():
        for index, handle in enumerate(handles):
            handle.selected = index
        message = "handler failed"
        raise RuntimeError(message)
    assert [handle.selected for handle in handles] == [None, None, None]


# --- The commit precondition --------------------------------------------------------------


def test_a_read_and_written_cell_conflicts_when_it_moves(follow: Follow, here: Member) -> None:
    workspace = Workspace()
    conflict = r"\.filters changed"
    with pytest.raises(ReactiveConflictError, match=conflict), transaction():
        workspace.filters = (*workspace.filters, "mine")
        _write_from_elsewhere(workspace, "filters", ("theirs",))
    assert workspace.filters == ("theirs",), "the losing action published nothing"


def _write_from_elsewhere(handle: Reactive, name: str, value: object) -> None:
    """Commit a value the way another task's action would, around this transaction."""
    elsewhere(lambda: setattr(handle, name, value))


def test_a_write_only_cell_is_blind(follow: Follow, here: Member) -> None:
    preferences = Preferences()
    with transaction():
        preferences.locale = "fr"
        _write_from_elsewhere(preferences, "locale", "de")
    assert preferences.locale == "fr", "last commit wins"


def test_a_read_only_action_never_conflicts(follow: Follow, here: Member) -> None:
    preferences = Preferences()
    composed: list[str] = []
    with transaction():
        composed.append(preferences.theme)
        _write_from_elsewhere(preferences, "theme", "dark")
    assert composed == ["system"]
    assert preferences.theme == "dark"


def test_reading_a_staged_value_does_not_enter_the_read_set(follow: Follow, here: Member) -> None:
    preferences = Preferences()
    with transaction():
        preferences.theme = "dark"
        assert preferences.theme == "dark"
        _write_from_elsewhere(preferences, "theme", "light")
    assert preferences.theme == "dark"


def test_a_later_write_does_not_clear_the_guard(follow: Follow, here: Member) -> None:
    preferences = Preferences()
    with pytest.raises(ReactiveConflictError), transaction():
        seen = preferences.theme
        preferences.theme = f"{seen}+"
        preferences.theme = "final"
        _write_from_elsewhere(preferences, "theme", "moved")


def test_a_b_a_conflicts_by_version_lineage(follow: Follow, here: Member) -> None:
    preferences = Preferences()
    with pytest.raises(ReactiveConflictError), transaction():
        seen = preferences.theme
        preferences.theme = f"{seen}!"
        _write_from_elsewhere(preferences, "theme", "dark")
        _write_from_elsewhere(preferences, "theme", "system")
    assert preferences.theme == "system"


def test_a_conflict_publishes_nothing_at_all(follow: Follow, here: Member) -> None:
    preferences = Preferences()
    workspace = Workspace()
    with pytest.raises(ReactiveConflictError), transaction():
        workspace.selected = 9
        preferences.theme = f"{preferences.theme}!"
        _write_from_elsewhere(preferences, "theme", "dark")
    assert workspace.selected is None


def test_local_state_rolls_back_with_a_conflict(follow: Follow, here: Member) -> None:
    preferences = Preferences()

    class Panel(Component[DiscordTarget]):
        open: bool = state(False)

        @override
        def render(self):
            return Text(str(self.open))

    panel = Panel()
    with pytest.raises(ReactiveConflictError), transaction():
        panel.open = True
        preferences.theme = f"{preferences.theme}!"
        _write_from_elsewhere(preferences, "theme", "dark")
    assert panel.open is False


def test_outside_an_action_every_write_is_blind(follow: Follow, here: Member) -> None:
    preferences = Preferences()
    seen = preferences.theme
    _write_from_elsewhere(preferences, "theme", "dark")
    preferences.theme = f"{seen}!"
    assert preferences.theme == "system!"


# --- Computed over shared state -----------------------------------------------------------


def test_a_computed_recomputes_when_another_owner_writes(follow: Follow, here: Member) -> None:
    workspace = Workspace()
    runs: list[int] = []

    class Detail(Component[DiscordTarget]):
        def __init__(self, workspace: Workspace) -> None:
            self.workspace = workspace

        @computed
        def title(self) -> str:
            runs.append(1)
            return f"build {self.workspace.selected}"

        @override
        def render(self):
            return Text(self.title)

    detail = Detail(workspace)
    assert detail.title == "build None"
    assert detail.title == "build None"
    assert len(runs) == 1
    workspace.selected = 7
    assert detail.title == "build 7"
    assert len(runs) == 2


# --- Lifetime ---------------------------------------------------------------------------


async def test_a_namespace_with_no_holder_is_collected(follow: Follow, here: Member) -> None:
    workspace = Workspace()
    gone = weakref.ref(workspace)
    unfollow = follow(CellAddress(workspace, "selected"), _record([]))
    workspace.selected = 3
    # Release both references held by this fixture: the namespace itself and the unsubscribe
    # callback, which closes over its cell address.
    unfollow()
    del unfollow, workspace
    gc.collect()
    assert gone() is None, "the handle is the state, so nothing outlives its last holder"
