"""Frontend-neutral action events and dispatch metadata."""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol

from squid_reactivity.actions import ActionContext
from squid_ui.entity import EntityRef
from squid_ui.text import TextLike

if TYPE_CHECKING:
    from squid_ui.forms import FormIssue, FormLike, SubmitHandler
    from squid_ui.guards import Guard
    from squid_ui.runtime.histories import History


class ActionMode(StrEnum):
    """Concurrency and stale-generation policy for an interactive action.

    A press carries the render generation its control was issued in. `EXCLUSIVE` and `REBASE`
    serialize under the mount's action lock; `PARALLEL_READ` and `IMMEDIATE` skip the lock and the
    generation check.
    """

    EXCLUSIVE = "exclusive"
    """A press from an older generation is acknowledged and dropped."""
    REBASE = "rebase"
    """A press from an older generation is re-resolved against the newest render and runs `rebased`."""
    PARALLEL_READ = "parallel_read"
    """Runs in a read-only transaction; a state write is an error."""
    IMMEDIATE = "immediate"
    """Runs in a normal transaction with no lock, whatever generation it came from."""


@dataclass(frozen=True, slots=True)
class BusySpec:
    """Ask the framework to show that a slow action is running.

    The interim paint is a patch of the scene already on screen — the pressed control
    relabelled, every interactive control disabled — rather than a re-render, because the
    handler is mid-transaction and a re-render would observe half-written state. It appears
    only once the action outlives the mount's `pending_after` threshold, so fast handlers
    never flicker.
    """

    pending: TextLike | None = None
    """What the pressed control says while the handler runs; `None` uses chrome."""
    restore_on_error: bool = True
    """Put the previous scene back before the error hook runs."""


class InteractionKind(StrEnum):
    """The portable interaction shape being dispatched; names the `ActionEvent` subclass the handler receives."""

    PRESS = "press"
    SELECTION = "selection"
    SUBMIT = "submit"


class Visibility(StrEnum):
    """Who sees a `notice`: `PRIVATE` only the actor (a Discord ephemeral), `PUBLIC` everyone in the channel."""

    PUBLIC = "public"
    PRIVATE = "private"


@dataclass(frozen=True, slots=True)
class Actor:
    """Portable identity facts supplied by a frontend."""

    id: str
    """Frontend-stable identifier, e.g. the Discord user id as a string; what author locks compare."""
    display_name: str | None = None


class ActionResponder(Protocol):
    """Response surface a frontend adapter gives one dispatched event; `finish()` ends the mount behind it.

    Every method is one that any frontend can implement, forms included since their schemas are
    portable. Frontend-native payloads stay on the concrete adapters, reached through helpers
    such as the Discord package's `responder(event)`.
    """

    async def acknowledge(self) -> None:
        """Tell the frontend the interaction is handled with no visible reply; a no-op once anything replied."""
        ...

    async def notice(self, text: TextLike, *, visibility: Visibility = Visibility.PRIVATE) -> None:
        """Send `text` as a reply to the interaction, resolved with the mount's localization."""
        ...

    async def redirect(self, url: str) -> None:
        """Send the actor to `url`; a frontend with no navigation delivers it as a private notice."""
        ...

    async def finish(self) -> None:
        """End the mount this event was dispatched from, disabling its controls; a no-op when already finished."""
        ...

    async def present_form(
        self,
        form: FormLike,
        *,
        key: str = "form",
        on_submit: SubmitHandler | None = None,
        mode: ActionMode | None = None,
        label: TextLike = "",
        record: History | None = None,
    ) -> None:
        """Open `form` and route its submission back through the mount as a `SubmitEvent`.

        A `FormSpec` requires `on_submit` and a `Form` forbids it (`TypeError` either way). `mode`
        defaults to the form's own, `EXCLUSIVE` for a spec. With `record`, the submission enters
        that history under `label`. The Discord adapter raises `RuntimeError` if the interaction
        already replied, since a modal must be the initial response.
        """
        ...

    def invalidate(self) -> None:
        """Schedule a re-render without a state change, for presentation-only updates."""
        ...


@dataclass(frozen=True, slots=True)
class ActionEvent:
    """Base event passed to portable component handlers; `finish()` ends the mount that dispatched it.

    The response methods delegate to `responder`. `context` carries one reserved key,
    `"frontend"`, naming the adapter that dispatched the event; the rest is for host-injected
    `ContextKey`s. It is not a place to smuggle frontend facts — a Discord-only handler
    reaches for the Discord package's `native(event)` or `responder(event)` instead.
    """

    actor: Actor
    responder: ActionResponder
    locale: str | None = None
    """Locale negotiated for the actor, when the frontend reports one."""
    context: Mapping[str, Any] = field(default_factory=dict)

    async def acknowledge(self) -> None:
        await self.responder.acknowledge()

    async def notice(self, text: TextLike, *, visibility: Visibility = Visibility.PRIVATE) -> None:
        await self.responder.notice(text, visibility=visibility)

    async def redirect(self, url: str) -> None:
        await self.responder.redirect(url)

    async def finish(self) -> None:
        await self.responder.finish()

    async def present_form(
        self,
        form: FormLike,
        *,
        key: str = "form",
        on_submit: SubmitHandler | None = None,
        mode: ActionMode | None = None,
        label: TextLike = "",
        record: History | None = None,
    ) -> None:
        await self.responder.present_form(
            form,
            key=key,
            on_submit=on_submit,
            mode=mode,
            label=label,
            record=record,
        )

    def invalidate(self) -> None:
        self.responder.invalidate()


@dataclass(frozen=True, slots=True)
class PressEvent(ActionEvent):
    """A button or equivalent action was pressed."""


@dataclass(frozen=True, slots=True)
class SelectionEvent(ActionEvent):
    """A selection control submitted one or more values."""

    values: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EntitySelectionEvent(ActionEvent):
    """An entity selection submitted portable concrete references."""

    values: tuple[EntityRef, ...] = ()


@dataclass(frozen=True, slots=True)
class SubmitEvent(ActionEvent):
    """A portable form was submitted; the three fields are the `FormResult` of that attempt."""

    values: Mapping[str, object] = field(default_factory=dict)
    """Parsed values by key; a field whose `parse` failed is absent."""
    attempted: Mapping[str, object] = field(default_factory=dict)
    """The raw submission, which a retry re-presents as the prefill."""
    errors: tuple[FormIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class ActionRequest:
    """One admitted mounted action supplied to application middleware.

    Rebase is generation metadata, not an outcome: the rebased handler may still return,
    fail, or later encounter a delivery failure. Framework admission has already resolved
    the binding and generation before constructing this value.
    """

    event: ActionEvent
    key: str
    kind: InteractionKind
    mode: ActionMode
    submitted_generation: int | None
    """Render generation the pressed control was issued in; `None` when the frontend does not track one."""
    active_generation: int
    """Generation the mount is at as the handler runs."""
    context: ActionContext
    rebased: bool = False
    """`True` when `mode` is `REBASE` and the binding was re-resolved because the two generations differ."""


type ActionProceed = Callable[[], Awaitable[None]]


class ActionMiddleware(ABC):
    """Application-wide policy around an admitted mounted action.

    The middleware onion surrounds the handler's state transaction so it can observe and
    catch commit-time failures such as shared-state conflicts. State a middleware writes
    itself is therefore independent of the handler transaction and does not roll back with
    it. Middleware is a policy surface, not a component-state mutation surface, unless that
    independence is deliberate.
    """

    @abstractmethod
    async def dispatch(self, request: ActionRequest, proceed: ActionProceed) -> None:
        """Continue once through ``proceed``, or return to short-circuit."""


type ActionHandler = Callable[[ActionEvent], Awaitable[None]]
type PressHandler = Callable[[PressEvent], Awaitable[None]]
type SelectionHandler = Callable[[SelectionEvent], Awaitable[None]]
type EntitySelectionHandler = Callable[[EntitySelectionEvent], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ActionBinding[EventT: ActionEvent = Any]:
    """Ephemeral handler data kept out of serializable scenes."""

    key: str
    handler: Callable[[EventT], Awaitable[None]]
    mode: ActionMode = ActionMode.EXCLUSIVE
    routes: Mapping[str, ActionBinding[Any]] = field(default_factory=dict)
    """For a select that stands in for several buttons: the binding each option value dispatches to."""
    guard: Guard | None = None
    """Admission checked by the frontend after the concurrency gate, before the handler."""
    busy: BusySpec | None = None
    """Interim paint for a handler slow enough to need it; `None` shows nothing while it runs."""
    label: TextLike = ""
    """What the pressed control says, which is what a framework-written entry is called."""
    record: History | None = None
    """History this action enters itself into, under `label`, before the handler runs."""

    def routed(self, values: tuple[str, ...]) -> ActionBinding[Any] | None:
        """The binding `values` selects: `self` without `routes`, else the route for the single value, else `None`."""
        if not self.routes:
            return self
        if len(values) != 1:
            return None
        return self.routes.get(values[0])


__all__ = [
    "ActionBinding",
    "ActionEvent",
    "ActionHandler",
    "ActionMiddleware",
    "ActionMode",
    "ActionProceed",
    "ActionRequest",
    "ActionResponder",
    "Actor",
    "BusySpec",
    "EntitySelectionEvent",
    "EntitySelectionHandler",
    "InteractionKind",
    "PressEvent",
    "PressHandler",
    "SelectionEvent",
    "SelectionHandler",
    "SubmitEvent",
    "Visibility",
]
