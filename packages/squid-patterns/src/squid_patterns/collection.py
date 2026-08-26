"""Editable form-value collections over component and routed pattern shells."""

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from squid_ui.factories import actions, choice, heading, stack, status
from squid_ui.forms import Form, FormLike, FormSpec
from squid_ui.runtime.component import RenderResult
from squid_ui.semantic import ActionDisplay, FormTrigger, Tone
from squid_ui.sources import Position
from squid_ui.text import TextLike
from squid_patterns._paging import window
from squid_patterns.shells import ComponentShell, PatternControls, PatternEvent


@dataclass(frozen=True, slots=True)
class CollectionEntry:
    key: str
    values: tuple[tuple[str, object], ...]


@dataclass(frozen=True, slots=True)
class CollectionState:
    entries: tuple[CollectionEntry, ...] = ()
    selected: str | None = None
    page: int = 0


type CollectionChangeHandler = Callable[
    [PatternEvent[CollectionState], tuple[Mapping[str, object], ...]], Awaitable[None]
]


class _Action(StrEnum):
    SELECT = "select"
    ADD = "add"
    EDIT = "edit"
    REMOVE = "remove"
    UP = "up"
    DOWN = "down"
    PREVIOUS = "page:previous"
    NEXT = "page:next"


class CollectionEditor:
    """A pure add/edit/remove/reorder machine whose payloads are form values."""

    def __init__(
        self,
        title: TextLike,
        *,
        key: str = "collection",
        create: FormLike,
        edit: Callable[[Mapping[str, object]], FormLike] | None = None,
        label: Callable[[Mapping[str, object]], TextLike],
        identity: Callable[[Mapping[str, object]], str] | None = None,
        minimum: int = 0,
        maximum: int | None = None,
        reorder: bool = True,
        window_size: int = 25,
    ) -> None:
        if not key:
            message = "CollectionEditor.key must not be empty"
            raise ValueError(message)
        if minimum < 0 or (maximum is not None and maximum < minimum):
            message = "CollectionEditor bounds must satisfy 0 <= minimum <= maximum"
            raise ValueError(message)
        if not 1 <= window_size <= 25:
            message = "CollectionEditor.window_size must be between 1 and 25"
            raise ValueError(message)
        self.title = title
        self.key = key
        self.create = create.spec() if isinstance(create, Form) else create
        self.edit = edit
        self.label = label
        self.identity = identity
        self.minimum = minimum
        self.maximum = maximum
        self.reorder = reorder
        self.window_size = window_size

    @property
    def initial_state(self) -> CollectionState:
        return CollectionState()

    @staticmethod
    def _mapping(entry: CollectionEntry) -> Mapping[str, object]:
        return MappingProxyType(dict(entry.values))

    def initial_from(self, entries: Iterable[Mapping[str, object]]) -> CollectionState:
        collected: list[CollectionEntry] = []
        keys: set[str] = set()
        for index, values in enumerate(entries, start=1):
            copied = MappingProxyType(dict(values))
            key = self.identity(copied) if self.identity is not None else str(index)
            if not key or key in keys:
                message = f"CollectionEditor entry keys must be non-empty and unique: {key!r}"
                raise ValueError(message)
            keys.add(key)
            collected.append(CollectionEntry(key, tuple(copied.items())))
        return CollectionState(tuple(collected))

    def build_component(
        self,
        *,
        initial: CollectionState | None = None,
        on_change: CollectionChangeHandler | None = None,
    ) -> ComponentShell[CollectionState]:
        async def changed(event: PatternEvent[CollectionState]) -> None:
            if on_change is None or event.state.entries == event.previous.entries:
                return
            await on_change(event, tuple(self._mapping(entry) for entry in event.state.entries))

        return ComponentShell(self, initial=initial, on_change=changed)

    def values(self, state: CollectionState) -> tuple[Mapping[str, object], ...]:
        """Project state to its ordered public form-value mappings."""
        return tuple(self._mapping(entry) for entry in state.entries)

    def errors(self, state: CollectionState) -> tuple[str, ...]:
        errors: list[str] = []
        if len(state.entries) < self.minimum:
            errors.append(f"Add at least {self.minimum} entries.")
        if self.maximum is not None and len(state.entries) > self.maximum:
            errors.append(f"Keep no more than {self.maximum} entries.")
        return tuple(errors)

    def _selected_index(self, state: CollectionState) -> int | None:
        return next((index for index, entry in enumerate(state.entries) if entry.key == state.selected), None)

    @staticmethod
    def _mint(entries: tuple[CollectionEntry, ...]) -> str:
        used = {entry.key for entry in entries}
        ordinal = 1
        while str(ordinal) in used:
            ordinal += 1
        return str(ordinal)

    def transition(
        self,
        state: CollectionState,
        action: str,
        *,
        values: tuple[str, ...] = (),
        submitted: Mapping[str, object] | None = None,
    ) -> CollectionState:
        if action == _Action.SELECT:
            selected = values[0] if len(values) == 1 and values[0] in {entry.key for entry in state.entries} else None
            return CollectionState(state.entries, selected, state.page)
        if action == _Action.ADD and submitted is not None:
            if self.maximum is not None and len(state.entries) >= self.maximum:
                return state
            copied = MappingProxyType(dict(submitted))
            key = self.identity(copied) if self.identity is not None else self._mint(state.entries)
            if not key or key in {entry.key for entry in state.entries}:
                return state
            entry = CollectionEntry(key, tuple(copied.items()))
            return CollectionState((*state.entries, entry), key, state.page)
        selected_index = self._selected_index(state)
        if action == _Action.EDIT and submitted is not None and selected_index is not None:
            entries = list(state.entries)
            entries[selected_index] = CollectionEntry(entries[selected_index].key, tuple(submitted.items()))
            return CollectionState(tuple(entries), state.selected, state.page)
        if action == _Action.REMOVE and selected_index is not None and len(state.entries) > self.minimum:
            entries = list(state.entries)
            del entries[selected_index]
            pages = max(1, (len(entries) + self.window_size - 1) // self.window_size)
            return CollectionState(tuple(entries), None, min(state.page, pages - 1))
        if action in {_Action.UP, _Action.DOWN} and self.reorder and selected_index is not None:
            target = selected_index + (-1 if action == _Action.UP else 1)
            if not 0 <= target < len(state.entries):
                return state
            entries = list(state.entries)
            entries[selected_index], entries[target] = entries[target], entries[selected_index]
            return CollectionState(tuple(entries), state.selected, target // self.window_size)
        if action in {_Action.PREVIOUS, _Action.NEXT}:
            delta = -1 if action == _Action.PREVIOUS else 1
            pages = max(1, (len(state.entries) + self.window_size - 1) // self.window_size)
            return CollectionState(state.entries, state.selected, max(0, min(state.page + delta, pages - 1)))
        return state

    def form_for(self, state: CollectionState, action: str) -> FormSpec | None:
        if action == _Action.ADD:
            if self.maximum is not None and len(state.entries) >= self.maximum:
                return None
            return self.create
        if action != _Action.EDIT:
            return None
        index = self._selected_index(state)
        if index is None:
            return None
        values = self._mapping(state.entries[index])
        if self.edit is None:
            return self.create.with_prefill(values)
        form = self.edit(values)
        return form.spec() if isinstance(form, Form) else form

    def render(self, state: CollectionState, controls: PatternControls[CollectionState]) -> RenderResult:
        visible, position, pages = window(
            state.entries,
            key=self.key,
            position=Position(offset=state.page),
            per_page=self.window_size,
            chrome=controls.chrome,
            identity=lambda entry: entry.key,
        )
        selected_index = self._selected_index(state)
        picker = (
            controls.choices(
                tuple(choice(self.label(self._mapping(entry)), key=entry.key) for entry in visible),
                _Action.SELECT.value,
                key=f"{self.key}.select",
                selected=(state.selected,) if state.selected is not None else (),
                minimum=0,
                maximum=1,
                placeholder=self.title,
            )
            if visible
            else None
        )
        add_form = self.form_for(state, _Action.ADD)
        edit_form = self.form_for(state, _Action.EDIT)
        add = (
            controls.form(add_form, _Action.ADD.value, key=f"{self.key}.add", label=controls.chrome.add)
            if add_form is not None
            else controls.action(controls.chrome.add, _Action.ADD.value, key=f"{self.key}.add", available=False)
        )
        edit = (
            controls.form(edit_form, _Action.EDIT.value, key=f"{self.key}.edit", label=controls.chrome.edit)
            if edit_form is not None
            else controls.action(controls.chrome.edit, _Action.EDIT.value, key=f"{self.key}.edit", available=False)
        )
        add_node = (
            add
            if isinstance(add, FormTrigger)
            else actions(add, key=f"{self.key}.add-action", display=ActionDisplay.INDIVIDUAL)
        )
        edit_node = (
            edit
            if isinstance(edit, FormTrigger)
            else actions(edit, key=f"{self.key}.edit-action", display=ActionDisplay.INDIVIDUAL)
        )
        entry_actions = actions(
            controls.action(
                controls.chrome.remove,
                _Action.REMOVE.value,
                key=f"{self.key}.remove",
                tone=Tone.DANGER,
                available=selected_index is not None and len(state.entries) > self.minimum,
            ),
            controls.action(
                controls.chrome.move_up,
                _Action.UP.value,
                key=f"{self.key}.up",
                available=self.reorder and selected_index is not None and selected_index > 0,
            ),
            controls.action(
                controls.chrome.move_down,
                _Action.DOWN.value,
                key=f"{self.key}.down",
                available=(self.reorder and selected_index is not None and selected_index < len(state.entries) - 1),
            ),
            key=f"{self.key}.entry-actions",
            display=ActionDisplay.INDIVIDUAL,
        )
        paging = (
            actions(
                controls.action(
                    controls.chrome.previous,
                    _Action.PREVIOUS.value,
                    key=f"{self.key}.previous",
                    available=position.offset > 0,
                ),
                controls.action(
                    controls.chrome.next,
                    _Action.NEXT.value,
                    key=f"{self.key}.next",
                    available=position.offset < pages - 1,
                ),
                key=f"{self.key}.paging",
                display=ActionDisplay.INDIVIDUAL,
            )
            if pages > 1
            else None
        )
        return stack(
            heading(self.title),
            *(status(message, tone=Tone.DANGER) for message in self.errors(state)),
            picker,
            edit_node,
            entry_actions,
            add_node,
            paging,
        )
