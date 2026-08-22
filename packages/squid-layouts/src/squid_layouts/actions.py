"""Frontend-neutral action events and dispatch metadata."""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol

from squid_layouts.text import TextLike

if TYPE_CHECKING:
    from squid_layouts.forms import FormIssue, FormLike, SubmitHandler


class ActionPolicy(StrEnum):
    """Concurrency and stale-generation policy for an interactive action."""

    EXCLUSIVE = "exclusive"
    REBASE = "rebase"
    PARALLEL_READ = "parallel_read"
    IMMEDIATE = "immediate"


class ActionKind(StrEnum):
    """The portable interaction shape being dispatched."""

    PRESS = "press"
    SELECTION = "selection"
    SUBMIT = "submit"


class Visibility(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"


@dataclass(frozen=True, slots=True)
class Actor:
    """Portable identity facts supplied by a frontend."""

    id: str
    display_name: str | None = None


class ActionResponder(Protocol):
    """Small UI response surface implemented by each frontend adapter.

    Every method here is one that any frontend can honestly implement. Forms joined this
    surface once their schemas became portable; frontend-native payloads remain on concrete
    adapters reached through helpers such as `sl.discord.responder(event)`.
    """

    async def acknowledge(self) -> None: ...

    async def notice(self, text: TextLike, *, visibility: Visibility = Visibility.PRIVATE) -> None: ...

    async def redirect(self, url: str) -> None: ...

    async def finish(self) -> None: ...

    async def present_form(
        self,
        form: FormLike,
        *,
        key: str = "form",
        on_submit: SubmitHandler | None = None,
        policy: ActionPolicy | None = None,
    ) -> None: ...

    def invalidate(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ActionEvent:
    """Base event passed to portable component handlers.

    `context` carries one reserved key, `"frontend"`, naming the adapter that dispatched
    the event; the rest is for host-injected `ContextKey`s. It is not a place to smuggle
    frontend facts — a Discord-only handler should reach for `sl.discord.native(event)`
    or `sl.discord.responder(event)` instead, which hand back the real Discord surfaces.
    """

    actor: Actor
    responder: ActionResponder
    locale: str | None = None
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
        policy: ActionPolicy | None = None,
    ) -> None:
        """Present a portable form through the dispatching frontend."""
        await self.responder.present_form(form, key=key, on_submit=on_submit, policy=policy)

    def invalidate(self) -> None:
        """Request a redraw after presentation-only state changes."""
        self.responder.invalidate()


@dataclass(frozen=True, slots=True)
class PressEvent(ActionEvent):
    """A button or equivalent action was pressed."""


@dataclass(frozen=True, slots=True)
class SelectionEvent(ActionEvent):
    """A selection control submitted one or more values."""

    values: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SubmitEvent(ActionEvent):
    """A portable form was submitted."""

    values: Mapping[str, object] = field(default_factory=dict)
    attempted: Mapping[str, object] = field(default_factory=dict)
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
    kind: ActionKind
    policy: ActionPolicy
    submitted_generation: int | None
    active_generation: int
    rebased: bool = False


type ActionProceed = Callable[[], Awaitable[None]]


class ActionMiddleware(ABC):
    """Application-wide policy around an admitted mounted action."""

    @abstractmethod
    async def dispatch(self, request: ActionRequest, proceed: ActionProceed) -> None:
        """Continue once through ``proceed``, or return to short-circuit."""


type ActionHandler = Callable[[ActionEvent], Awaitable[None]]
type PressHandler = Callable[[PressEvent], Awaitable[None]]
type SelectionHandler = Callable[[SelectionEvent], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ActionBinding:
    """Ephemeral handler data kept out of serializable scenes."""

    key: str
    handler: Callable[[Any], Awaitable[None]]
    policy: ActionPolicy = ActionPolicy.EXCLUSIVE
    routes: Mapping[str, ActionBinding] = field(default_factory=dict)

    def routed(self, values: tuple[str, ...]) -> ActionBinding | None:
        """Resolve a grouped control to its logical action binding."""
        if not self.routes:
            return self
        if len(values) != 1:
            return None
        return self.routes.get(values[0])
