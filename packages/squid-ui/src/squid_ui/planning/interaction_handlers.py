"""Event adapters emitted by semantic lowering.

Controlled handlers delegate state changes and acknowledgement to the author's
callback. Uncontrolled handlers acknowledge first, update presentation state, and
invalidate the mounted view. Dataclass fields let cached plans reconstruct these
adapters with the current render's callbacks and state.
"""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Self, cast, override

from squid_ui.authoring.semantic import (
    ChoiceEvent,
    ChoiceOwnership,
    Controlled,
    Details,
    EntityEvent,
    EntityOwnership,
    ItemOwnership,
    NavigateEvent,
    NavOwnership,
    OpenEvent,
    Toggle,
    ToggleEvent,
    Uncontrolled,
)
from squid_ui.content.entity import EntityRef
from squid_ui.content.text import TextLike
from squid_ui.forms import FormSpec, SubmitHandler
from squid_ui.handlers import _HandlerBinder, _HandlerComposition
from squid_ui.interactions import (
    ActionBinding,
    ActionEvent,
    ActionMode,
    EntitySelectionEvent,
    PressEvent,
    SelectionEvent,
)
from squid_ui.ownership import bind_ownership
from squid_ui.planning.generated import GeneratedHandler
from squid_ui.presentation import PresentationState

if TYPE_CHECKING:
    from squid_ui.runtime.histories import History


@dataclass(frozen=True, slots=True)
class PresentForm[ContextT = object](GeneratedHandler[PressEvent[ContextT]]):
    spec: FormSpec[ContextT]
    key: str
    on_submit: SubmitHandler[ContextT]
    mode: ActionMode
    label: TextLike
    record: History | None

    @override
    def _bind_handlers(self, binder: _HandlerBinder) -> Self:
        spec = binder.form_spec(self.spec)
        submit = binder.callback(self.on_submit)
        return (
            replace(self, spec=spec, on_submit=submit)
            if spec is not self.spec or submit is not self.on_submit
            else self
        )

    @override
    async def __call__(self, event: PressEvent[ContextT]) -> None:
        # The responder API erases invocation context at the frontend boundary.
        await event.present_form(
            cast(FormSpec, self.spec),
            key=self.key,
            on_submit=cast(SubmitHandler, self.on_submit),
            mode=self.mode,
            label=self.label,
            record=self.record,
        )


@dataclass(frozen=True, slots=True)
class ChoiceCommit[ContextT = object](_HandlerComposition):
    ownership: ChoiceOwnership[ContextT]
    key: str
    previous: tuple[str, ...]
    session: PresentationState

    @override
    def _bind_handlers(self, binder: _HandlerBinder) -> Self:
        ownership = bind_ownership(self.ownership, binder)
        return replace(self, ownership=ownership) if ownership is not self.ownership else self

    async def commit(self, event: ActionEvent[ContextT], selected: tuple[str, ...]) -> None:
        match self.ownership:
            case Controlled(on_change=on_change):
                await on_change(
                    ChoiceEvent(
                        event.actor,
                        event.responder,
                        event.locale,
                        event.metadata,
                        selected,
                        tuple(key for key in selected if key not in self.previous),
                        tuple(key for key in self.previous if key not in selected),
                        context=event.context,
                    )
                )
            case Uncontrolled():
                await event.acknowledge()
                self.session.select(self.key, selected)
                event.invalidate()


@dataclass(frozen=True, slots=True)
class ChooseChoice[ContextT = object](GeneratedHandler[PressEvent[ContextT]]):
    commit: ChoiceCommit[ContextT]
    key: str

    @override
    def _bind_handlers(self, binder: _HandlerBinder) -> Self:
        commit = binder.composition(self.commit)
        return replace(self, commit=commit) if commit is not self.commit else self

    @override
    async def __call__(self, event: PressEvent[ContextT]) -> None:
        await self.commit.commit(event, (self.key,))


@dataclass(frozen=True, slots=True)
class SelectChoices[ContextT = object](GeneratedHandler[SelectionEvent[ContextT]]):
    commit: ChoiceCommit[ContextT]

    @override
    def _bind_handlers(self, binder: _HandlerBinder) -> Self:
        commit = binder.composition(self.commit)
        return replace(self, commit=commit) if commit is not self.commit else self

    @override
    async def __call__(self, event: SelectionEvent[ContextT]) -> None:
        await self.commit.commit(event, event.values)


@dataclass(frozen=True, slots=True)
class EntityCommit[ContextT = object](_HandlerComposition):
    ownership: EntityOwnership[ContextT]
    key: str
    previous: tuple[EntityRef, ...]
    session: PresentationState

    @override
    def _bind_handlers(self, binder: _HandlerBinder) -> Self:
        ownership = bind_ownership(self.ownership, binder)
        return replace(self, ownership=ownership) if ownership is not self.ownership else self

    async def commit(self, event: ActionEvent[ContextT], selected: tuple[EntityRef, ...]) -> None:
        match self.ownership:
            case Controlled(on_change=on_change):
                await on_change(
                    EntityEvent(
                        event.actor,
                        event.responder,
                        event.locale,
                        event.metadata,
                        selected,
                        tuple(value for value in selected if value not in self.previous),
                        tuple(value for value in self.previous if value not in selected),
                        context=event.context,
                    )
                )
            case Uncontrolled():
                await event.acknowledge()
                self.session.select(self.key, tuple(value.encode() for value in selected))
                event.invalidate()


@dataclass(frozen=True, slots=True)
class SelectEntities[ContextT = object](GeneratedHandler[EntitySelectionEvent[ContextT]]):
    commit: EntityCommit[ContextT]

    @override
    def _bind_handlers(self, binder: _HandlerBinder) -> Self:
        commit = binder.composition(self.commit)
        return replace(self, commit=commit) if commit is not self.commit else self

    @override
    async def __call__(self, event: EntitySelectionEvent[ContextT]) -> None:
        await self.commit.commit(event, event.values)


@dataclass(frozen=True, slots=True)
class SelectEntityFallback[ContextT = object](GeneratedHandler[ChoiceEvent[ContextT]]):
    commit: EntityCommit[ContextT]
    by_key: Mapping[str, EntityRef]

    @override
    def _bind_handlers(self, binder: _HandlerBinder) -> Self:
        commit = binder.composition(self.commit)
        return replace(self, commit=commit) if commit is not self.commit else self

    @override
    async def __call__(self, event: ChoiceEvent[ContextT]) -> None:
        await self.commit.commit(event, tuple(self.by_key[key] for key in event.selected if key in self.by_key))


@dataclass(frozen=True, slots=True)
class ItemCommit[ContextT = object](_HandlerComposition):
    ownership: ItemOwnership[ContextT]
    key: str
    session: PresentationState

    @override
    def _bind_handlers(self, binder: _HandlerBinder) -> Self:
        ownership = bind_ownership(self.ownership, binder)
        return replace(self, ownership=ownership) if ownership is not self.ownership else self

    async def commit(self, event: ActionEvent[ContextT], opened: str | None) -> None:
        match self.ownership:
            case Controlled(on_change=on_change):
                await on_change(
                    OpenEvent(
                        event.actor, event.responder, event.locale, event.metadata, opened=opened, context=event.context
                    )
                )
            case Uncontrolled():
                await event.acknowledge()
                self.session.select(self.key, () if opened is None else (opened,))
                event.invalidate()


@dataclass(frozen=True, slots=True)
class CloseItem[ContextT = object](GeneratedHandler[PressEvent[ContextT]]):
    commit: ItemCommit[ContextT]

    @override
    def _bind_handlers(self, binder: _HandlerBinder) -> Self:
        commit = binder.composition(self.commit)
        return replace(self, commit=commit) if commit is not self.commit else self

    @override
    async def __call__(self, event: PressEvent[ContextT]) -> None:
        await self.commit.commit(event, None)


@dataclass(frozen=True, slots=True)
class FocusItem[ContextT = object](GeneratedHandler[SelectionEvent[ContextT]]):
    commit: ItemCommit[ContextT]

    @override
    def _bind_handlers(self, binder: _HandlerBinder) -> Self:
        commit = binder.composition(self.commit)
        return replace(self, commit=commit) if commit is not self.commit else self

    @override
    async def __call__(self, event: SelectionEvent[ContextT]) -> None:
        await self.commit.commit(event, event.values[0] if event.values else None)


@dataclass(frozen=True, slots=True)
class NavigationCommit[ContextT = object](_HandlerComposition):
    ownership: NavOwnership[ContextT]
    key: str
    session: PresentationState

    @override
    def _bind_handlers(self, binder: _HandlerBinder) -> Self:
        ownership = bind_ownership(self.ownership, binder)
        return replace(self, ownership=ownership) if ownership is not self.ownership else self

    async def commit(self, event: ActionEvent[ContextT], destination: str) -> None:
        match self.ownership:
            case Controlled(on_change=on_change):
                await on_change(
                    NavigateEvent(
                        event.actor, event.responder, event.locale, event.metadata, destination, context=event.context
                    )
                )
            case Uncontrolled():
                await event.acknowledge()
                self.session.select(self.key, (destination,))
                event.invalidate()


@dataclass(frozen=True, slots=True)
class SelectDestination[ContextT = object](GeneratedHandler[SelectionEvent[ContextT]]):
    commit: NavigationCommit[ContextT]

    @override
    def _bind_handlers(self, binder: _HandlerBinder) -> Self:
        commit = binder.composition(self.commit)
        return replace(self, commit=commit) if commit is not self.commit else self

    @override
    async def __call__(self, event: SelectionEvent[ContextT]) -> None:
        if event.values:
            await self.commit.commit(event, event.values[0])


@dataclass(frozen=True, slots=True)
class GoToDestination[ContextT = object](GeneratedHandler[PressEvent[ContextT]]):
    commit: NavigationCommit[ContextT]
    key: str

    @override
    def _bind_handlers(self, binder: _HandlerBinder) -> Self:
        commit = binder.composition(self.commit)
        return replace(self, commit=commit) if commit is not self.commit else self

    @override
    async def __call__(self, event: PressEvent[ContextT]) -> None:
        await self.commit.commit(event, self.key)


@dataclass(frozen=True, slots=True)
class ToggleDetails[ContextT = object](GeneratedHandler[PressEvent[ContextT]]):
    node: Details[Any, ContextT]
    open: bool
    session: PresentationState

    @override
    def _bind_handlers(self, binder: _HandlerBinder) -> Self:
        def bind_node() -> Details[Any, ContextT]:
            ownership = bind_ownership(self.node.open, binder)
            return replace(self.node, open=ownership) if ownership is not self.node.open else self.node

        node = binder.cached(self.node, bind_node)
        return replace(self, node=node) if node is not self.node else self

    @override
    async def __call__(self, event: PressEvent[ContextT]) -> None:
        match self.node.open:
            case Controlled(on_change=on_change):
                await on_change(
                    OpenEvent(
                        event.actor,
                        event.responder,
                        event.locale,
                        event.metadata,
                        opened=not self.open,
                        context=event.context,
                    )
                )
            case Uncontrolled(initial=seed):
                await event.acknowledge()
                current = self.session.disclosure(self.node.key, initial=seed).open
                self.session.disclose(self.node.key, not current)
                event.invalidate()


@dataclass(frozen=True, slots=True)
class FlipToggle(GeneratedHandler[PressEvent]):
    node: Toggle
    on: bool
    session: PresentationState

    @override
    def _bind_handlers(self, binder: _HandlerBinder) -> Self:
        def bind_node() -> Toggle:
            ownership = bind_ownership(self.node.on, binder)
            return replace(self.node, on=ownership) if ownership is not self.node.on else self.node

        node = binder.cached(self.node, bind_node)
        return replace(self, node=node) if node is not self.node else self

    @override
    async def __call__(self, event: PressEvent) -> None:
        match self.node.on:
            case Controlled(on_change=on_change):
                await on_change(
                    ToggleEvent(
                        event.actor, event.responder, event.locale, event.metadata, not self.on, context=event.context
                    )
                )
            case Uncontrolled(initial=initial):
                await event.acknowledge()
                current = self.session.toggle(self.node.key, initial=initial).on
                self.session.set_toggle(self.node.key, on=not current)
                event.invalidate()


@dataclass(frozen=True, slots=True)
class ForwardSelection(GeneratedHandler[PressEvent]):
    handler: Callable[[SelectionEvent], Awaitable[None]]
    key: str

    @override
    def _bind_handlers(self, binder: _HandlerBinder) -> Self:
        handler = binder.callback(self.handler)
        return replace(self, handler=handler) if handler is not self.handler else self

    @override
    async def __call__(self, event: PressEvent) -> None:
        await self.handler(
            SelectionEvent(
                event.actor, event.responder, event.locale, event.metadata, (self.key,), context=event.context
            )
        )


@dataclass(frozen=True, slots=True)
class RouteSelection(GeneratedHandler[SelectionEvent]):
    routes: Mapping[str, ActionBinding]

    @override
    def _bind_handlers(self, binder: _HandlerBinder) -> Self:
        routes = binder.actions(self.routes)
        return replace(self, routes=routes) if routes is not self.routes else self

    @override
    async def __call__(self, event: SelectionEvent) -> None:
        binding = self.routes.get(event.values[0]) if len(event.values) == 1 else None
        if binding is not None:
            await binding.handler(event)
