"""Add, edit, remove, and reorder records through forms and selection controls."""

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from squid_i18n import NUMBER
from squid_ui.document import DocumentLike
from squid_ui.factories import action_controls, choice, heading, stack, status
from squid_ui.forms import Form, FormLike, FormSpec
from squid_ui.semantic import ControlDisplay, FormTrigger, Tone
from squid_ui.text import TextLike, tr
from squid_ui_widgets._actions import MachineKeySegment, WindowSelection
from squid_ui_widgets._option_window import OptionWindow
from squid_ui_widgets.drivers import ComponentDriver, FormValues, MachineControls, TransitionEvent


@dataclass(frozen=True, slots=True)
class CollectionEntry:
    """One keyed form-value record in a collection."""

    key: str
    values: tuple[tuple[str, object], ...]


@dataclass(frozen=True, slots=True)
class CollectionState:
    """Ordered records, current selection, and visible page."""

    entries: tuple[CollectionEntry, ...] = ()
    selected: str | None = None
    page: int = 0


type CollectionChangeHandler = Callable[[TransitionEvent[CollectionState], tuple[FormValues, ...]], Awaitable[None]]


class _Action(StrEnum):
    """Closed collection transition vocabulary."""

    SELECT = "select"
    ADD = "add"
    EDIT = "edit"
    REMOVE = "remove"
    UP = "up"
    DOWN = "down"
    PREVIOUS = "page:previous"
    NEXT = "page:next"


class CollectionEditor:
    """Add, edit, remove, and reorder form-value records in local state.

    Identities are assigned on creation and retained through edits. The default numeric
    identity may be reused after deletion. Submitted values must already be parsed and
    validated by the form pipeline; bounds and identity checks do not validate fields.
    """

    def __init__(
        self,
        title: TextLike,
        *,
        key: str = "collection",
        create: FormLike,
        edit: Callable[[FormValues], FormLike] | None = None,
        label: Callable[[FormValues], TextLike],
        identity: Callable[[FormValues], str] | None = None,
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
        """Return an empty collection state."""
        return CollectionState()

    @staticmethod
    def _mapping(entry: CollectionEntry) -> FormValues:
        """Expose an entry through a read-only mapping copy."""
        return MappingProxyType(dict(entry.values))

    def initial_from(self, entries: Iterable[FormValues]) -> CollectionState:
        """Build state from public values, rejecting empty or duplicate identities.

        Initial cardinality may violate the configured bounds; ``errors`` reports those issues.
        """
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
    ) -> ComponentDriver[CollectionState]:
        """Build a component shell that reports ordered value changes."""

        async def changed(event: TransitionEvent[CollectionState]) -> None:
            if on_change is None or event.state.entries == event.previous.entries:
                return
            await on_change(event, tuple(self._mapping(entry) for entry in event.state.entries))

        if initial is None:
            return ComponentDriver(self, on_change=changed)
        return ComponentDriver(self, initial=initial, on_change=changed)

    def values(self, state: CollectionState) -> tuple[FormValues, ...]:
        """Project state to its ordered public form-value mappings."""
        return tuple(self._mapping(entry) for entry in state.entries)

    def errors(self, state: CollectionState) -> tuple[TextLike, ...]:
        """Return collection-bound violations for `state`."""
        errors: list[TextLike] = []
        minimum, maximum = self.minimum, self.maximum
        if len(state.entries) < minimum:
            errors.append(
                tr(t"""
                    widgets-collection-too-few = {{ {NUMBER(minimum)} ->
                        [one] Add at least {NUMBER(minimum)} entry.
                       *[other] Add at least {NUMBER(minimum)} entries.
                    }}
                    """)
            )
        if maximum is not None and len(state.entries) > maximum:
            errors.append(
                tr(t"""
                    widgets-collection-too-many = {{ {NUMBER(maximum)} ->
                        [one] Keep no more than {NUMBER(maximum)} entry.
                       *[other] Keep no more than {NUMBER(maximum)} entries.
                    }}
                    """)
            )
        return tuple(errors)

    def _selected_index(self, state: CollectionState) -> int | None:
        """Locate the selected entry, if it is still present."""
        return next((index for index, entry in enumerate(state.entries) if entry.key == state.selected), None)

    @staticmethod
    def _mint(entries: tuple[CollectionEntry, ...]) -> str:
        """Choose the first unused numeric identity."""
        used = {entry.key for entry in entries}
        ordinal = 1
        while str(ordinal) in used:
            ordinal += 1
        return str(ordinal)

    def _window(self, state: CollectionState, page: int | None = None) -> OptionWindow:
        return OptionWindow.resolve(
            tuple(choice(self.label(self._mapping(entry)), key=entry.key) for entry in state.entries),
            state.page if page is None else page,
            self.window_size,
        )

    def transition(
        self,
        state: CollectionState,
        action: str,
        *,
        values: tuple[str, ...] = (),
        submitted: FormValues | None = None,
    ) -> CollectionState:
        """Apply a collection action without mutating the input state."""
        scope = WindowSelection.parse(action)
        if action == _Action.SELECT or scope is not None:
            window = self._window(state, None if scope is None else scope.page)
            if scope is not None and (scope.group != "entries" or not window.matches(scope)):
                return state
            selected = values[0] if len(values) == 1 and values[0] in window.keys else None
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
            updated = CollectionState(tuple(entries), None, state.page)
            return CollectionState(updated.entries, None, self._window(updated).page)
        if action in {_Action.UP, _Action.DOWN} and self.reorder and selected_index is not None:
            target = selected_index + (-1 if action == _Action.UP else 1)
            if not 0 <= target < len(state.entries):
                return state
            entries = list(state.entries)
            entries[selected_index], entries[target] = entries[target], entries[selected_index]
            return CollectionState(tuple(entries), state.selected, target // self.window_size)
        if action in {_Action.PREVIOUS, _Action.NEXT}:
            delta = -1 if action == _Action.PREVIOUS else 1
            window = self._window(state)
            return CollectionState(state.entries, state.selected, max(0, min(window.page + delta, window.pages - 1)))
        return state

    def form_for(self, state: CollectionState, action: str) -> FormSpec | None:
        """Return the create or edit form available for `action`."""
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

    def render(self, state: CollectionState, controls: MachineControls[CollectionState]) -> DocumentLike:
        """Render the visible window and record actions."""
        window = self._window(state)
        selected_index = self._selected_index(state)
        picker = (
            controls.choices(
                window.choices,
                window.selection_action(MachineKeySegment("entries")),
                key=f"{self.key}.select",
                selected=(state.selected,) if state.selected is not None else (),
                minimum=0,
                maximum=1,
                placeholder=self.title,
            )
            if window.choices
            else None
        )
        add_form = self.form_for(state, _Action.ADD)
        edit_form = self.form_for(state, _Action.EDIT)
        add = (
            controls.form(add_form, _Action.ADD.value, key=f"{self.key}.add", label=controls.chrome.add)
            if add_form is not None
            else controls.action_control(controls.chrome.add, _Action.ADD.value, key=f"{self.key}.add", available=False)
        )
        edit = (
            controls.form(edit_form, _Action.EDIT.value, key=f"{self.key}.edit", label=controls.chrome.edit)
            if edit_form is not None
            else controls.action_control(
                controls.chrome.edit, _Action.EDIT.value, key=f"{self.key}.edit", available=False
            )
        )
        add_node = (
            add
            if isinstance(add, FormTrigger)
            else action_controls(add, key=f"{self.key}.add-action", display=ControlDisplay.INDIVIDUAL)
        )
        edit_node = (
            edit
            if isinstance(edit, FormTrigger)
            else action_controls(edit, key=f"{self.key}.edit-action", display=ControlDisplay.INDIVIDUAL)
        )
        entry_actions = action_controls(
            controls.action_control(
                controls.chrome.remove,
                _Action.REMOVE.value,
                key=f"{self.key}.remove",
                tone=Tone.DANGER,
                available=selected_index is not None and len(state.entries) > self.minimum,
            ),
            controls.action_control(
                controls.chrome.move_up,
                _Action.UP.value,
                key=f"{self.key}.up",
                available=self.reorder and selected_index is not None and selected_index > 0,
            ),
            controls.action_control(
                controls.chrome.move_down,
                _Action.DOWN.value,
                key=f"{self.key}.down",
                available=(self.reorder and selected_index is not None and selected_index < len(state.entries) - 1),
            ),
            key=f"{self.key}.entry-actions",
            display=ControlDisplay.INDIVIDUAL,
        )
        return stack(
            heading(self.title),
            *(status(message, tone=Tone.DANGER) for message in self.errors(state)),
            picker,
            edit_node,
            entry_actions,
            add_node,
            *window.navigation(controls, self.key, previous=_Action.PREVIOUS.value, next=_Action.NEXT.value),
        )
