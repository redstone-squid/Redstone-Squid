"""Frontend-neutral action events and dispatch metadata."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


class ActionPolicy(StrEnum):
    """Concurrency and stale-generation policy for an interactive action."""

    EXCLUSIVE = "exclusive"
    REBASE = "rebase"
    PARALLEL_READ = "parallel_read"
    IMMEDIATE = "immediate"


class Visibility(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"


@dataclass(frozen=True, slots=True)
class Actor:
    """Portable identity facts supplied by a frontend."""

    id: str
    display_name: str | None = None


class ActionResponder(Protocol):
    """Small UI response surface implemented by each frontend adapter."""

    async def acknowledge(self) -> None: ...

    async def notice(self, text: str, *, visibility: Visibility = Visibility.PRIVATE) -> None: ...

    async def present_form(self, form: object) -> None: ...

    async def download(self, asset: object) -> None: ...

    async def redirect(self, url: str) -> None: ...

    async def finish(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ActionEvent:
    """Base event passed to portable component handlers."""

    actor: Actor
    responder: ActionResponder
    locale: str | None = None
    context: Mapping[str, Any] = field(default_factory=dict)

    async def acknowledge(self) -> None:
        await self.responder.acknowledge()

    async def notice(self, text: str, *, visibility: Visibility = Visibility.PRIVATE) -> None:
        await self.responder.notice(text, visibility=visibility)

    async def present_form(self, form: object) -> None:
        await self.responder.present_form(form)

    async def download(self, asset: object) -> None:
        await self.responder.download(asset)

    async def redirect(self, url: str) -> None:
        await self.responder.redirect(url)

    async def finish(self) -> None:
        await self.responder.finish()


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

    values: Mapping[str, str] = field(default_factory=dict)


type ActionHandler = Callable[[ActionEvent], Awaitable[None]]
type PressHandler = Callable[[PressEvent], Awaitable[None]]
type SelectionHandler = Callable[[SelectionEvent], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ActionBinding:
    """Ephemeral handler data kept out of serializable scenes."""

    key: str
    handler: Callable[[Any], Awaitable[None]]
    policy: ActionPolicy = ActionPolicy.EXCLUSIVE
