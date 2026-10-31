"""Declarative event adapters emitted by semantic lowering."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from squid_ui.entity import EntityRef, encode_entity_ref
from squid_ui.forms import FormSpec, SubmitHandler
from squid_ui.interactions import (
    ActionBinding,
    ActionEvent,
    ActionMode,
    EntitySelectionEvent,
    PressEvent,
    SelectionEvent,
)
from squid_ui.planning.generated import GeneratedHandler
from squid_ui.runtime.presentation_state import PresentationState
from squid_ui.semantic import (
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
from squid_ui.text import TextLike

if TYPE_CHECKING:
    from squid_ui.runtime.histories import History


@dataclass(frozen=True, slots=True)
class PresentForm(GeneratedHandler[PressEvent]):
    spec: FormSpec
    key: str
    on_submit: SubmitHandler
    mode: ActionMode
    label: TextLike
    record: History | None

    async def __call__(self, event: PressEvent) -> None:
        await event.present_form(
            self.spec,
            key=self.key,
            on_submit=self.on_submit,
            mode=self.mode,
            label=self.label,
            record=self.record,
        )


@dataclass(frozen=True, slots=True)
class ChoiceCommit:
    ownership: ChoiceOwnership
    key: str
    previous: tuple[str, ...]
    session: PresentationState

    async def commit(self, event: ActionEvent, selected: tuple[str, ...]) -> None:
        match self.ownership:
            case Controlled(on_change=on_change):
                await on_change(
                    ChoiceEvent(
                        event.actor,
                        event.responder,
                        event.locale,
                        event.context,
                        selected,
                        tuple(key for key in selected if key not in self.previous),
                        tuple(key for key in self.previous if key not in selected),
                    )
                )
            case Uncontrolled():
                await event.acknowledge()
                self.session.select(self.key, selected)
                event.invalidate()


@dataclass(frozen=True, slots=True)
class ChooseChoice(GeneratedHandler[PressEvent]):
    commit: ChoiceCommit
    key: str

    async def __call__(self, event: PressEvent) -> None:
        await self.commit.commit(event, (self.key,))


@dataclass(frozen=True, slots=True)
class SelectChoices(GeneratedHandler[SelectionEvent]):
    commit: ChoiceCommit

    async def __call__(self, event: SelectionEvent) -> None:
        await self.commit.commit(event, event.values)


@dataclass(frozen=True, slots=True)
class EntityCommit:
    ownership: EntityOwnership
    key: str
    previous: tuple[EntityRef, ...]
    session: PresentationState

    async def commit(self, event: ActionEvent, selected: tuple[EntityRef, ...]) -> None:
        match self.ownership:
            case Controlled(on_change=on_change):
                await on_change(
                    EntityEvent(
                        event.actor,
                        event.responder,
                        event.locale,
                        event.context,
                        selected,
                        tuple(value for value in selected if value not in self.previous),
                        tuple(value for value in self.previous if value not in selected),
                    )
                )
            case Uncontrolled():
                await event.acknowledge()
                self.session.select(self.key, tuple(_entity_key(value) for value in selected))
                event.invalidate()


@dataclass(frozen=True, slots=True)
class SelectEntities(GeneratedHandler[EntitySelectionEvent]):
    commit: EntityCommit

    async def __call__(self, event: EntitySelectionEvent) -> None:
        await self.commit.commit(event, event.values)


@dataclass(frozen=True, slots=True)
class SelectEntityFallback(GeneratedHandler[ChoiceEvent]):
    commit: EntityCommit
    by_key: Mapping[str, EntityRef]

    async def __call__(self, event: ChoiceEvent) -> None:
        await self.commit.commit(event, tuple(self.by_key[key] for key in event.selected if key in self.by_key))


@dataclass(frozen=True, slots=True)
class ItemCommit:
    ownership: ItemOwnership
    key: str
    session: PresentationState

    async def commit(self, event: ActionEvent, opened: str | None) -> None:
        match self.ownership:
            case Controlled(on_change=on_change):
                await on_change(OpenEvent(event.actor, event.responder, event.locale, event.context, opened=opened))
            case Uncontrolled():
                await event.acknowledge()
                self.session.select(self.key, () if opened is None else (opened,))
                event.invalidate()


@dataclass(frozen=True, slots=True)
class CloseItem(GeneratedHandler[PressEvent]):
    commit: ItemCommit

    async def __call__(self, event: PressEvent) -> None:
        await self.commit.commit(event, None)


@dataclass(frozen=True, slots=True)
class FocusItem(GeneratedHandler[SelectionEvent]):
    commit: ItemCommit

    async def __call__(self, event: SelectionEvent) -> None:
        await self.commit.commit(event, event.values[0] if event.values else None)


@dataclass(frozen=True, slots=True)
class NavigationCommit:
    ownership: NavOwnership
    key: str
    session: PresentationState

    async def commit(self, event: ActionEvent, destination: str) -> None:
        match self.ownership:
            case Controlled(on_change=on_change):
                await on_change(NavigateEvent(event.actor, event.responder, event.locale, event.context, destination))
            case Uncontrolled():
                await event.acknowledge()
                self.session.select(self.key, (destination,))
                event.invalidate()


@dataclass(frozen=True, slots=True)
class SelectDestination(GeneratedHandler[SelectionEvent]):
    commit: NavigationCommit

    async def __call__(self, event: SelectionEvent) -> None:
        if event.values:
            await self.commit.commit(event, event.values[0])


@dataclass(frozen=True, slots=True)
class GoToDestination(GeneratedHandler[PressEvent]):
    commit: NavigationCommit
    key: str

    async def __call__(self, event: PressEvent) -> None:
        await self.commit.commit(event, self.key)


@dataclass(frozen=True, slots=True)
class ToggleDetails(GeneratedHandler[PressEvent]):
    node: Details
    open: bool
    session: PresentationState

    async def __call__(self, event: PressEvent) -> None:
        match self.node.open:
            case Controlled(on_change=on_change):
                await on_change(
                    OpenEvent(event.actor, event.responder, event.locale, event.context, opened=not self.open)
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

    async def __call__(self, event: PressEvent) -> None:
        match self.node.on:
            case Controlled(on_change=on_change):
                await on_change(ToggleEvent(event.actor, event.responder, event.locale, event.context, not self.on))
            case Uncontrolled(initial=initial):
                await event.acknowledge()
                current = self.session.toggle(self.node.key, initial=initial).on
                self.session.set_toggle(self.node.key, on=not current)
                event.invalidate()


@dataclass(frozen=True, slots=True)
class ForwardSelection(GeneratedHandler[PressEvent]):
    handler: Callable[[SelectionEvent], Awaitable[None]]
    key: str

    async def __call__(self, event: PressEvent) -> None:
        await self.handler(SelectionEvent(event.actor, event.responder, event.locale, event.context, (self.key,)))


@dataclass(frozen=True, slots=True)
class RouteSelection(GeneratedHandler[SelectionEvent]):
    routes: Mapping[str, ActionBinding]

    async def __call__(self, event: SelectionEvent) -> None:
        binding = self.routes.get(event.values[0]) if len(event.values) == 1 else None
        if binding is not None:
            await binding.handler(event)


def _entity_key(ref: EntityRef) -> str:
    return encode_entity_ref(ref)
