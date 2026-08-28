"""Queries, doubles and determinism controls for exercising the engine with no frontend.

Three halves, which is one more than the name suggests. `walk`, `texts`, `labels`, `keys`,
`find` and `find_all` read a rendered semantic tree, so an assertion can say what the tree
holds rather than how one target serializes it. `RecordingResponder` and the event factories
stand in for the frontend end of `ActionEvent`, so a handler can be driven directly.
`ManualClock` and `assert_imports_without` remove the two things a test cannot otherwise
control: the passage of time and what the interpreter has already imported.

This module is public and versioned like the rest of the package. It is imported by tests
rather than by a running application, so it is reachable as `squid_ui.testing.X` and promotes
no names to `squid_ui` itself.
"""

import subprocess
import sys
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from squid_ui.forms import FormIssue, FormLike, SubmitHandler
from squid_ui.interactions import (
    ActionMode,
    Actor,
    PressEvent,
    SelectionEvent,
    SubmitEvent,
    Visibility,
)
from squid_ui.runtime.component import AnyComponent, Component, render_component_tree
from squid_ui.semantic import AnyLayoutNode, ChoiceEvent
from squid_ui.text import NEUTRAL, TextLike, resolve_text

type Tree = AnyLayoutNode | Iterable[Any] | object
"""Anything `walk` accepts: one node, a sequence of them, or a `ComponentTree`'s `nodes`."""


# --- Tree queries -------------------------------------------------------------------------


def walk(tree: Tree) -> Iterator[Any]:
    """Yield every dataclass in `tree`, depth first, including `tree` itself.

    Deliberately generic rather than built on `runtime._tree`'s `_CHILD_FIELD_NAMES`. That set
    names the fields a *structural rewrite* must descend, which excludes the control axis on
    purpose -- `ActionControls.items` is not a layout child. A test asking "is this button in
    the render" wants both axes, and every hand-written walker this replaces had to pick which
    container types to special-case. Visiting every dataclass field cannot make that mistake,
    and cannot drift when a node grows a field.
    """
    yield from _walk(tree, set())


def _walk(value: object, seen: set[int]) -> Iterator[Any]:
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
    return isinstance(value, str) or hasattr(value, "template") or hasattr(value, "content")


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
    return [node.key for node in walk(tree) if isinstance(getattr(node, "key", None), str)]


def find_all[NodeT](tree: Tree, kind: type[NodeT], *, key: str | None = None) -> tuple[NodeT, ...]:
    """Every `kind` in `tree`, optionally narrowed to one key, in render order."""
    return tuple(
        node for node in walk(tree) if isinstance(node, kind) and (key is None or getattr(node, "key", None) == key)
    )


def find[NodeT](tree: Tree, kind: type[NodeT], *, key: str | None = None) -> NodeT:
    """The single `kind` in `tree`, or a failure naming what was there instead.

    Asserts uniqueness rather than taking the first match: a duplicate key is a real defect
    (two controls that share one), and silently reading past it is how such a defect survives
    a test that looks like it covers the control.
    """
    found = find_all(tree, kind, key=key)
    where = f"{kind.__name__}" + (f" keyed {key!r}" if key is not None else "")
    assert found, f"no {where} in this render; it holds {sorted(set(keys(tree)))}"
    assert len(found) == 1, f"{len(found)} of {where} in this render"
    return found[0]


def render_tree(component: AnyComponent) -> tuple[AnyLayoutNode, ...]:
    """The expanded semantic nodes of one component, with no frontend and no runtime."""
    return tuple(render_component_tree(component).nodes)


# --- Frontend doubles ---------------------------------------------------------------------


@dataclass
class RecordingResponder:
    """An `ActionResponder` that records what a handler asked the frontend to do.

    Every method a frontend must honestly implement, answered by appending to a list. A test
    asserts against `notices`, `redirects`, `forms`, `finished` and `invalidations` rather than
    against a mock's call args, so it reads as the handler's intent.
    """

    acknowledged: int = 0
    notices: list[tuple[str, Visibility]] = field(default_factory=list)
    redirects: list[str] = field(default_factory=list)
    forms: list[FormLike] = field(default_factory=list)
    finished: bool = False
    invalidations: int = 0

    async def acknowledge(self) -> None:
        self.acknowledged += 1

    async def notice(self, text: TextLike, *, visibility: Visibility = Visibility.PRIVATE) -> None:
        self.notices.append((resolve_text(text, NEUTRAL).content, visibility))

    async def redirect(self, url: str) -> None:
        self.redirects.append(url)

    async def finish(self) -> None:
        self.finished = True

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
    """A responder that fails the test if anything reaches it.

    For the cases whose whole claim is that no frontend call happens -- a guard answering a
    verdict, a pure machine computing a transition. An unused `RecordingResponder` proves the
    same thing only if the test remembers to assert it; this one cannot be forgotten.
    """

    def __getattr__(self, name: str) -> object:
        message = f"this code was not supposed to reach the responder, but called {name}"
        raise AssertionError(message)


def _responder(responder: object | None) -> Any:
    return RecordingResponder() if responder is None else responder


def press_event(*, actor: str = "1", responder: object | None = None, locale: str | None = None) -> PressEvent:
    """A press, carrying a `RecordingResponder` unless one is supplied."""
    return PressEvent(Actor(actor), _responder(responder), locale)


def selection_event(
    *values: str, actor: str = "1", responder: object | None = None, locale: str | None = None
) -> SelectionEvent:
    """A selection of `values`, carrying a `RecordingResponder` unless one is supplied."""
    return SelectionEvent(Actor(actor), _responder(responder), locale, values=values)


def choice_event(
    *selected: str,
    actor: str = "1",
    responder: object | None = None,
    locale: str | None = None,
    added: Sequence[str] = (),
    removed: Sequence[str] = (),
) -> ChoiceEvent:
    """A picker settling on `selected`, carrying a `RecordingResponder` unless one is supplied.

    Distinct from `selection_event`: `semantic.Choices` hands its owner a `ChoiceEvent`, which
    also reports what the interaction added and removed, while a raw `interactions.Selection`
    control reports only the values it submitted.
    """
    return ChoiceEvent(
        Actor(actor),
        _responder(responder),
        locale,
        selected=selected,
        added=tuple(added),
        removed=tuple(removed),
    )


def submit_event(
    values: Mapping[str, object] | None = None,
    *,
    actor: str = "1",
    responder: object | None = None,
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
    )


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
    """A component that renders `lines` as paragraphs and nothing else.

    Most component tests need a tree to hang behaviour off, not a particular tree; seven files
    each declared their own one-paragraph class to get one. `declared` becomes reactive state
    on the instance, and a line given as a callable is passed the instance -- which is what
    lets the render read that state, the way the hand-written classes did:

        subject = text_component(lambda self: f"count {self.count}", count=0)
    """
    from squid_ui.factories import paragraph
    from squid_ui.runtime.reactivity import state

    def render(self: Any) -> list[Any]:
        return [paragraph(line(self) if callable(line) else line) for line in lines]

    namespace: dict[str, Any] = {name: state(value) for name, value in declared.items()}
    namespace["__annotations__"] = dict.fromkeys(declared, Any)
    namespace["render"] = render
    return type("TextComponent", (Component,), namespace)()


# --- Import isolation ---------------------------------------------------------------------


def assert_imports_without(package: str, *blocked: str) -> None:
    """Assert `package` imports in a fresh interpreter where `blocked` cannot be imported.

    A subprocess rather than an in-process `sys.meta_path` finder, because by the time a test
    runs, the blocked packages are already in `sys.modules` and an import of `package` would
    find them there whether it declared them or not. The child runs `_isolated_import` below,
    so the blocker is real source this repo lints and type-checks rather than a string literal.
    """
    argument = ", ".join(repr(name) for name in (package, *blocked))
    code = f"from squid_ui.testing import _isolated_import; _isolated_import({argument})"
    subprocess.run([sys.executable, "-c", code], check=True)


def _isolated_import(package: str, *blocked: str) -> None:
    """Import `package` with `blocked` unimportable, and prove none of them arrived anyway.

    The child-process half of `assert_imports_without`. Both halves are needed: refusing the
    import proves nothing reached for the module directly, and the `sys.modules` check catches
    a name that arrived transitively before the finder was installed.
    """
    import importlib
    import importlib.abc
    import importlib.machinery

    roots = {name.split(".", 1)[0] for name in blocked}

    class BlockImports(importlib.abc.MetaPathFinder):
        def find_spec(
            self, fullname: str, path: Sequence[str] | None = None, target: object = None
        ) -> importlib.machinery.ModuleSpec | None:
            if fullname.split(".", 1)[0] in roots:
                raise ModuleNotFoundError(fullname)
            return None

    sys.meta_path.insert(0, BlockImports())
    importlib.import_module(package)
    leaked = roots & set(sys.modules)
    assert not leaked, f"{package} pulled in {sorted(leaked)}"


__all__ = [
    "ManualClock",
    "RecordingResponder",
    "UntouchedResponder",
    "assert_imports_without",
    "choice_event",
    "find",
    "find_all",
    "keys",
    "labels",
    "press_event",
    "render_tree",
    "selection_event",
    "submit_event",
    "text_component",
    "texts",
    "walk",
]
