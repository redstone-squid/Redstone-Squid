"""Explicit frontend implementations selected when a control is bound."""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Self, cast

from squid_ui.interactions import ActionBinding, ActionEvent

if TYPE_CHECKING:
    from squid_ui.forms import FormBinding, FormSpec


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


class _HandlerComposition(ABC):
    """Framework-owned callback edges that participate in frontend selection.

    Implementations bind only declared callback children. Application values held
    beside those children are opaque, even when they happen to be dataclasses.
    """

    @abstractmethod
    def _bind_handlers(self, binder: _HandlerBinder) -> Self: ...


class _HandlerBinder:
    """Select one frontend across a plan's explicit callback graph.

    Unchanged objects retain identity. A shared child is resolved once across both
    action and form bindings; backreferences retain the original object as before.
    """

    def __init__(self, frontend: str) -> None:
        self.frontend = frontend
        self._visited: dict[int, object] = {}

    def cached[T](self, source: T, resolve: Callable[[], T]) -> T:
        """Preserve sharing for one explicitly declared framework value."""
        identity = id(source)
        if identity in self._visited:
            # Each entry is resolved by the same typed operation that first saw it.
            return cast(T, self._visited[identity])
        self._visited[identity] = source
        result = resolve()
        self._visited[identity] = result
        return result

    def callback[EventT](self, callback: Callable[[EventT], Awaitable[None]]) -> Callable[[EventT], Awaitable[None]]:
        def resolve() -> Callable[[EventT], Awaitable[None]]:
            if isinstance(callback, HandlerSet):
                return callback.select(self.frontend)
            if isinstance(callback, FrontendHandler):
                if callback.frontend != self.frontend:
                    message = f"handler for {callback.frontend!r} cannot be bound on {self.frontend!r}"
                    raise TypeError(message)
                return callback
            if isinstance(callback, _HandlerComposition):
                # Self preserves the callable type; narrowing loses that intersection.
                return cast(Callable[[EventT], Awaitable[None]], callback._bind_handlers(self))
            return callback

        return self.cached(callback, resolve)

    def composition[CompositionT: _HandlerComposition](self, value: CompositionT) -> CompositionT:
        return self.cached(value, lambda: value._bind_handlers(self))

    def _mapping[ValueT](self, values: Mapping[str, ValueT], bind: Callable[[ValueT], ValueT]) -> Mapping[str, ValueT]:
        def resolve() -> Mapping[str, ValueT]:
            mapped = {key: bind(value) for key, value in values.items()}
            return mapped if any(mapped[key] is not value for key, value in values.items()) else values

        return self.cached(values, resolve)

    def actions[EventT: ActionEvent](
        self, bindings: Mapping[str, ActionBinding[EventT]]
    ) -> Mapping[str, ActionBinding[EventT]]:
        return self._mapping(bindings, self.action)

    def action[EventT: ActionEvent](self, binding: ActionBinding[EventT]) -> ActionBinding[EventT]:
        def resolve() -> ActionBinding[EventT]:
            handler = self.callback(binding.handler)
            routes = self.actions(binding.routes)
            return (
                replace(binding, handler=handler, routes=routes)
                if handler is not binding.handler or routes is not binding.routes
                else binding
            )

        return self.cached(binding, resolve)

    def form_spec[ContextT](self, spec: FormSpec[ContextT]) -> FormSpec[ContextT]:
        def resolve() -> FormSpec[ContextT]:
            if spec.on_invalid is None:
                return spec
            invalid = self.callback(spec.on_invalid)
            return replace(spec, on_invalid=invalid) if invalid is not spec.on_invalid else spec

        return self.cached(spec, resolve)

    def form[ContextT](self, binding: FormBinding[ContextT]) -> FormBinding[ContextT]:
        def resolve() -> FormBinding[ContextT]:
            submit = self.callback(binding.on_submit)
            spec = self.form_spec(binding.spec)
            return (
                replace(binding, on_submit=submit, spec=spec)
                if submit is not binding.on_submit or spec is not binding.spec
                else binding
            )

        return self.cached(binding, resolve)

    def forms[ContextT](self, bindings: Mapping[str, FormBinding[ContextT]]) -> Mapping[str, FormBinding[ContextT]]:
        return self._mapping(bindings, self.form)


def bind_handlers(
    actions: Mapping[str, ActionBinding], forms: Mapping[str, FormBinding], frontend: str
) -> tuple[Mapping[str, ActionBinding], Mapping[str, FormBinding]]:
    """Resolve frontend registrations along framework-declared callback edges.

    Application callback objects and their data remain opaque. Generated adapters
    explicitly declare their children rather than exposing every dataclass field.
    """
    binder = _HandlerBinder(frontend)
    return binder.actions(actions), binder.forms(forms)
