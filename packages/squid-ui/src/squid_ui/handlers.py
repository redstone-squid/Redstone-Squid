"""Explicit frontend implementations selected when a control is bound."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, fields, is_dataclass, replace
from typing import Any, cast

from squid_ui.interactions import ActionEvent


@dataclass(frozen=True, slots=True)
class FrontendHandler[EventT: ActionEvent = ActionEvent]:
    """One explicitly registered frontend implementation."""

    frontend: str
    callback: Callable[[EventT], Awaitable[None]]

    async def __call__(self, event: EventT) -> None:
        if event.metadata.get("frontend") != self.frontend:
            message = f"handler requires the {self.frontend!r} frontend"
            raise TypeError(message)
        await self.callback(event)


@dataclass(frozen=True, slots=True)
class HandlerSet[EventT: ActionEvent = ActionEvent]:
    """Frontend implementations with an optional portable fallback."""

    branches: tuple[FrontendHandler[EventT], ...]
    fallback: Callable[[EventT], Awaitable[None]] | None = None

    def __post_init__(self) -> None:
        names = [branch.frontend for branch in self.branches]
        if len(names) != len(set(names)):
            message = "a handler set cannot repeat a frontend"
            raise ValueError(message)
        if not names and self.fallback is None:
            message = "a handler set needs an implementation"
            raise ValueError(message)

    def select(self, frontend: str) -> Callable[[EventT], Awaitable[None]]:
        for branch in self.branches:
            if branch.frontend == frontend:
                return branch
        if self.fallback is not None:
            return self.fallback
        message = f"no handler implementation for frontend {frontend!r}"
        raise TypeError(message)

    async def __call__(self, event: EventT) -> None:
        await self.select(event.metadata["frontend"])(event)


def handlers[EventT: ActionEvent](
    *branches: FrontendHandler[EventT],
    fallback: Callable[[EventT], Awaitable[None]] | None = None,
) -> HandlerSet[EventT]:
    """Choose the frontend's implementation, then the portable fallback."""
    return HandlerSet(branches, fallback)


def bind_handlers[T](value: T, frontend: str) -> T:
    """Resolve registrations inside bindings and generated semantic handlers before exposure.

    Generated handlers carry other handlers in dataclass fields (choice commits, grouped
    actions, and forms). Walking those records keeps registration independent of how a
    planner represents the action. Unchanged records retain their identity for caching.
    """

    visited: dict[int, Any] = {}

    def bind(item: Any) -> Any:
        identity = id(item)
        if identity in visited:
            return visited[identity]
        # A callable may carry cyclic application data; leave backreferences intact.
        visited[identity] = item
        result = resolve(item)
        visited[identity] = result
        return result

    def resolve(item: Any) -> Any:
        if isinstance(item, HandlerSet):
            return item.select(frontend)
        if isinstance(item, FrontendHandler):
            if item.frontend != frontend:
                message = f"handler for {item.frontend!r} cannot be bound on {frontend!r}"
                raise TypeError(message)
            return item
        if is_dataclass(item) and not isinstance(item, type):
            changes = {}
            for field in fields(item):
                if not field.init:
                    continue
                old = getattr(item, field.name)
                new = bind(old)
                if new is not old:
                    changes[field.name] = new
            return replace(item, **changes) if changes else item
        if isinstance(item, Mapping):
            mapped = {key: bind(child) for key, child in item.items()}
            return mapped if any(mapped[key] is not child for key, child in item.items()) else item
        if isinstance(item, tuple):
            mapped_tuple = tuple(bind(child) for child in item)
            return mapped_tuple if any(a is not b for a, b in zip(mapped_tuple, item, strict=True)) else item
        return item

    return cast(T, bind(value))
