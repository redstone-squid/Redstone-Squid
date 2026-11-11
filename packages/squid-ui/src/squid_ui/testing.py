"""Tree queries, frontend doubles, and deterministic helpers for UI tests.

These helpers inspect semantic values and call handlers directly. They do not emulate
mounted dispatch, guards, transactions, or transport. Import them from squid_ui.testing;
they are not promoted into the package root.
"""

import subprocess
import sys
from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from squid_ui.forms import FormIssue, FormLike, SubmitHandler
from squid_ui.interactions import (
    ActionMode,
    ActionResponder,
    Actor,
    PressEvent,
    SelectionEvent,
    SubmitEvent,
    Visibility,
)
from squid_ui.ownership import PageEvent
from squid_ui.runtime.continuations import PostCommitOperation
from squid_ui.runtime.definition import AnyComponent, Component
from squid_ui.runtime.expansion import render_component_tree
from squid_ui.semantic import ActionControl, AnyLayoutNode, ChoiceEvent, Choices, Controlled, FormTrigger
from squid_ui.text import NEUTRAL, Message, TextLike, resolve_text

type Tree = object
"""Anything `walk` can inspect recursively."""


# --- Tree queries -------------------------------------------------------------------------


def walk(tree: Tree) -> Iterator[object]:
    """Yield each reachable dataclass once by identity, in depth-first traversal order.

    Traverse all dataclass fields, mapping values, and iterable items, including controls
    that the structural layout rewriter does not visit. Shared references and cycles are
    visited once; traversing an iterator consumes it.
    """
    yield from _walk(tree, set())


def _walk(value: object, seen: set[int]) -> Iterator[object]:
    if id(value) in seen:
        return
    if isinstance(value, str | bytes):
        return
    if is_dataclass(value) and not isinstance(value, type):
        seen.add(id(value))
        yield value
        for entry in fields(value):
            yield from _walk(getattr(value, entry.name, None), seen)
        return
    if isinstance(value, Mapping):
        seen.add(id(value))
        for item in value.values():
            yield from _walk(item, seen)
        return
    if isinstance(value, Iterable):
        seen.add(id(value))
        for item in value:
            yield from _walk(item, seen)


def _field_strings(tree: Tree, name: str) -> list[str]:
    found: list[str] = []
    for node in walk(tree):
        value = getattr(node, name, None)
        if value is not None and _is_text(value):
            found.append(resolve_text(value, NEUTRAL).content)
    return found


def _is_text(value: object) -> bool:
    return isinstance(value, str | Message) or hasattr(value, "content")


def texts(tree: Tree) -> list[str]:
    """The resolved `content` of every node in `tree` that has one, in render order.

    Resolved against `text.NEUTRAL`, so a `Message` reads as its untranslated template. A test
    about translation wants `resolve_text` with a real `Localization`, not this.
    """
    return _field_strings(tree, "content")


def labels(tree: Tree) -> list[str]:
    """The resolved `label` of every control in `tree`, in render order."""
    return _field_strings(tree, "label")


def keys(tree: Tree) -> list[str]:
    """The `key` of every node in `tree` that carries one, in render order."""
    return [key for node in walk(tree) if isinstance(key := getattr(node, "key", None), str)]


def find_all[NodeT](tree: Tree, kind: type[NodeT], *, key: str | None = None) -> tuple[NodeT, ...]:
    """Every `kind` in `tree`, optionally narrowed to one key, in render order."""
    return tuple(
        node for node in walk(tree) if isinstance(node, kind) and (key is None or getattr(node, "key", None) == key)
    )


def find[NodeT](tree: Tree, kind: type[NodeT], *, key: str | None = None) -> NodeT:
    """Return the unique matching node, asserting when it is missing or ambiguous."""
    found = find_all(tree, kind, key=key)
    where = f"{kind.__name__}" + (f" keyed {key!r}" if key is not None else "")
    assert found, f"no {where} in this render; it holds {sorted(set(keys(tree)))}"
    assert len(found) == 1, f"{len(found)} of {where} in this render"
    return found[0]


def render_tree(component: AnyComponent) -> tuple[AnyLayoutNode, ...]:
    """Expand one component's layout without frontend loading, delivery, or lifecycle hooks."""
    return tuple(render_component_tree(component).nodes)


# --- Frontend doubles ---------------------------------------------------------------------


@dataclass
class RecordingResponder:
    """Record handler intents without mounted dispatch or transport.

    Notices use neutral localization. Forms record only their schemas; callbacks and
    other presentation options are not retained. on_success records a callback and
    returns a pending handle, but never commits or starts it.
    """

    acknowledged: int = 0
    notices: list[tuple[str, Visibility]] = field(default_factory=list)
    redirects: list[str] = field(default_factory=list)
    forms: list[FormLike] = field(default_factory=list)
    finished: bool = False
    invalidations: int = 0
    on_success_callbacks: list[Callable[[], Awaitable[Any]]] = field(default_factory=list)

    async def acknowledge(self) -> None:
        self.acknowledged += 1

    async def notice(self, text: TextLike, *, visibility: Visibility = Visibility.PRIVATE) -> None:
        self.notices.append((resolve_text(text, NEUTRAL).content, visibility))

    async def redirect(self, url: str) -> None:
        self.redirects.append(url)

    def finish_on_success(self) -> None:
        """Record a closure request; this double does not run a transaction or cleanup."""
        self.finished = True

    def on_success[ValueT](
        self, callback: Callable[[], Awaitable[ValueT]], *, name: str | None = None
    ) -> PostCommitOperation[ValueT]:
        """Record the callback without committing an action or executing the work."""
        self.on_success_callbacks.append(callback)
        return PostCommitOperation(callback, name=name or "recorded operation")

    async def present_form(
        self,
        form: FormLike,
        *,
        key: str = "form",
        on_submit: SubmitHandler | None = None,
        mode: ActionMode | None = None,
        label: TextLike = "",
        record: object | None = None,
    ) -> None:
        self.forms.append(form)

    def invalidate(self) -> None:
        self.invalidations += 1


class UntouchedResponder:
    """Reject every response operation so tests fail immediately on an unexpected frontend call."""

    def _unexpected(self, name: str) -> None:
        message = f"this code was not supposed to reach the responder, but called {name}"
        raise AssertionError(message)

    async def acknowledge(self) -> None:
        self._unexpected("acknowledge")

    async def notice(self, text: TextLike, *, visibility: Visibility = Visibility.PRIVATE) -> None:
        self._unexpected("notice")

    async def redirect(self, url: str) -> None:
        self._unexpected("redirect")

    def finish_on_success(self) -> None:
        self._unexpected("finish_on_success")

    def on_success[ValueT](
        self, callback: Callable[[], Awaitable[ValueT]], *, name: str | None = None
    ) -> PostCommitOperation[ValueT]:
        message = "this code was not supposed to reach the responder, but called on_success"
        raise AssertionError(message)

    async def present_form(
        self,
        form: FormLike,
        *,
        key: str = "form",
        on_submit: SubmitHandler | None = None,
        mode: ActionMode | None = None,
        label: TextLike = "",
        record: object | None = None,
    ) -> None:
        self._unexpected("present_form")

    def invalidate(self) -> None:
        self._unexpected("invalidate")


def _responder(responder: ActionResponder | None) -> ActionResponder:
    return RecordingResponder() if responder is None else responder


def press_event(*, actor: str = "1", responder: ActionResponder | None = None, locale: str | None = None) -> PressEvent:
    """A press, carrying a `RecordingResponder` unless one is supplied."""
    return PressEvent(Actor(actor), _responder(responder), locale, context=None)


def selection_event(
    *values: str, actor: str = "1", responder: ActionResponder | None = None, locale: str | None = None
) -> SelectionEvent:
    """A selection of `values`, carrying a `RecordingResponder` unless one is supplied."""
    return SelectionEvent(Actor(actor), _responder(responder), locale, values=values, context=None)


def choice_event(
    *selected: str,
    actor: str = "1",
    responder: ActionResponder | None = None,
    locale: str | None = None,
    added: Sequence[str] = (),
    removed: Sequence[str] = (),
) -> ChoiceEvent:
    """A picker settling on `selected`, carrying a `RecordingResponder` unless one is supplied.

    Distinct from `selection_event`: `semantic.Choices` hands its owner a `ChoiceEvent`, which
    also reports what the interaction added and removed. SelectionEvent carries only
    submitted values, as supplied by a primitive SelectMenu.
    """
    return ChoiceEvent(
        Actor(actor),
        _responder(responder),
        locale,
        selected=selected,
        added=tuple(added),
        removed=tuple(removed),
        context=None,
    )


def page_event(
    page: int, *, actor: str = "1", responder: ActionResponder | None = None, locale: str | None = None
) -> PageEvent:
    """A request to show the zero-based `page`, carrying a `RecordingResponder` unless one is supplied."""
    return PageEvent(Actor(actor), _responder(responder), locale, page=page, context=None)


def submit_event(
    values: Mapping[str, object] | None = None,
    *,
    actor: str = "1",
    responder: ActionResponder | None = None,
    locale: str | None = None,
    attempted: Mapping[str, object] | None = None,
    errors: Sequence[FormIssue] = (),
) -> SubmitEvent:
    """A form submission, carrying a `RecordingResponder` unless one is supplied."""
    submitted = dict(values or {})
    return SubmitEvent(
        Actor(actor),
        _responder(responder),
        locale,
        values=submitted,
        attempted=dict(attempted) if attempted is not None else submitted,
        errors=tuple(errors),
        context=None,
    )


# --- Driving a render ---------------------------------------------------------------------


def control(tree: Tree, key: str) -> ActionControl:
    """The single control keyed `key` in `tree`."""
    return find(tree, ActionControl, key=key)


async def press(
    component: AnyComponent,
    key: str,
    *,
    actor: str = "1",
    responder: ActionResponder | None = None,
) -> None:
    """Render the component and directly invoke the keyed ActionControl handler.

    This bypasses mounted admission, guards, concurrency modes, and transactions.
    """
    await control(render_tree(component), key).on_trigger(press_event(actor=actor, responder=responder))


async def choose(
    component: AnyComponent,
    key: str,
    *values: str,
    actor: str = "1",
    responder: ActionResponder | None = None,
) -> None:
    """Directly invoke a controlled picker's change handler with the selected keys, without dispatch."""
    picker = find(render_tree(component), Choices, key=key)
    assert isinstance(picker.selection, Controlled), f"the picker keyed {key!r} does not own its selection"
    await picker.selection.on_change(choice_event(*values, actor=actor, responder=responder))


async def submit(
    component: AnyComponent,
    key: str,
    values: Mapping[str, object],
    *,
    actor: str = "1",
    responder: ActionResponder | None = None,
) -> None:
    """Directly call the keyed form trigger with supplied values; no parsing or validation runs."""
    trigger = find(render_tree(component), FormTrigger, key=key)
    await trigger.on_submit(submit_event(values, actor=actor, responder=responder))


# --- Determinism --------------------------------------------------------------------------


class ManualClock:
    """A monotonic and wall clock that only moves when a test moves it.

    One object for both readings so they cannot drift: `advance` moves them together, which is
    what makes a test about a deadline and a test about a timestamp agree about the same
    elapsed second. Callable, so it also stands in where a bare `now()` is expected.
    """

    def __init__(self, *, monotonic: float = 1_000.0, wall: datetime | None = None) -> None:
        self.value = monotonic
        self.wall = wall if wall is not None else datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> float:
        return self.value

    def monotonic(self) -> float:
        return self.value

    def utc(self) -> datetime:
        return self.wall

    def advance(self, seconds: float) -> None:
        self.value += seconds
        self.wall += timedelta(seconds=seconds)


# --- Components ---------------------------------------------------------------------------


type Line = TextLike | Callable[[Any], TextLike]
"""One paragraph of a `text_component`: fixed text, or text read from the instance."""


def text_component(*lines: Line, **declared: object) -> AnyComponent:
    """Build a component rendering fixed text or instance-dependent callables as paragraphs.

    Keyword arguments declare initial reactive state; each callable receives the instance:

        subject = text_component(lambda self: f"count {self.count}", count=0)
    """
    from squid_reactivity.core import (
        state,
    )
    from squid_ui.factories import paragraph

    def render(self: Any) -> list[Any]:
        return [paragraph(line(self) if callable(line) else line) for line in lines]

    namespace: dict[str, Any] = {name: state(value) for name, value in declared.items()}
    namespace["__annotations__"] = dict.fromkeys(declared, Any)
    namespace["render"] = render
    return type("TextComponent", (Component,), namespace)()


# --- Import isolation ---------------------------------------------------------------------


def assert_imports_without(imports: str | Sequence[str], *blocked: str) -> None:
    """Check imports in a fresh subprocess with the named dependency roots blocked.

    A fresh process prevents cached imports from bypassing the blocker. Use the current
    interpreter so installed packages match the test environment. A standalone helper
    installs the blocker before importing squid_ui, then checks sys.modules for blocked roots.
    """
    names = (imports,) if isinstance(imports, str) else tuple(imports)
    argument = ", ".join(repr(name) for name in (names, *blocked))
    helper = Path(__file__).with_name("_import_isolation.py")
    code = f"import runpy; runpy.run_path({str(helper)!r})['_isolated_import']({argument})"
    subprocess.run([sys.executable, "-c", code], check=True)


__all__ = [
    "ManualClock",
    "RecordingResponder",
    "UntouchedResponder",
    "assert_imports_without",
    "choice_event",
    "choose",
    "control",
    "find",
    "find_all",
    "keys",
    "labels",
    "page_event",
    "press",
    "press_event",
    "render_tree",
    "selection_event",
    "submit",
    "submit_event",
    "text_component",
    "texts",
    "walk",
]
