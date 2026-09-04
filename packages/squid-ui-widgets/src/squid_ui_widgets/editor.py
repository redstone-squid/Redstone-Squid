"""Section-oriented editing over forms and nested pure machines."""

from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, Generic, TypeVar, cast

from squid_ui.document import DocumentLike, as_document
from squid_ui.factories import action_controls, heading, paragraph, stack, status
from squid_ui.forms import Form, FormField, FormIssue, FormLike, FormSpec
from squid_ui.semantic import (
    ActionControl,
    Choice,
    Choices,
    ControlDisplay,
    Emphasis,
    FormTrigger,
    LayoutNode,
    RoutedActionControl,
    RoutedChoices,
    Tone,
)
from squid_ui.target_types import RenderTarget
from squid_ui.text import TextLike
from squid_ui_widgets._actions import MachineKeySegment, NestedAction, keyed_action, match_keyed_action
from squid_ui_widgets._content import ContentItem, ContentLike, display_text, normalize_content, require_key
from squid_ui_widgets.commit import CommitMode
from squid_ui_widgets.drivers import (
    ComponentDriver,
    FormPresentingMachine,
    FormValues,
    MachineControls,
    StateMachine,
    TransitionEvent,
)

type EditorValues = FormValues
type EditorCommitHandler = Callable[[TransitionEvent[EditorState], EditorValues, frozenset[str]], Awaitable[None]]

StateT = TypeVar("StateT")
ValueT = TypeVar("ValueT")
RenderTargetT = TypeVar("RenderTargetT", bound=RenderTarget, contravariant=True, default=RenderTarget)


@dataclass(frozen=True, slots=True)
class EditorSectionState:
    """One section's interaction state and last committed projected value."""

    key: MachineKeySegment
    state: object
    committed: object


@dataclass(frozen=True, slots=True)
class EditorState:
    """Every section state plus the nested workspace currently open."""

    sections: tuple[EditorSectionState, ...]
    editing: MachineKeySegment | None = None


def _formatted(value: object) -> str:
    if isinstance(value, tuple | list):
        return ", ".join(display_text(item) for item in value)
    return display_text(value)


class EditorSection(Generic[StateT, ValueT, RenderTargetT]):
    """A typed adapter between one editor value and its interactive section state."""

    def __init__(
        self,
        key: str,
        label: TextLike,
        *,
        initial: StateT,
        load: Callable[[ValueT], StateT],
        dump: Callable[[StateT], ValueT],
        summary: Callable[[ValueT], TextLike],
        machine: StateMachine[StateT, RenderTargetT] | None = None,
        form: FormSpec | None = None,
        issues: Callable[[StateT], Iterable[FormIssue]] | None = None,
    ) -> None:
        self.key = MachineKeySegment(key, name="EditorSection.key")
        self.label = label
        self.initial = initial
        self.load = load
        self.dump = dump
        self.summary = summary
        self.machine = machine
        self.form = form
        self.issues = issues

    @classmethod
    def from_form(
        cls,
        key: str,
        label: TextLike,
        form: FormLike,
        *,
        summary: Callable[[FormValues], TextLike] | None = None,
    ) -> EditorSection[tuple[tuple[str, object], ...], FormValues, RenderTarget]:
        """Adapt one form schema into an editor section."""
        spec = form.spec() if isinstance(form, Form) else form
        initial_values = {
            field.key: spec.prefill.get(field.key, field.default)
            for field in spec.items
            if isinstance(field, FormField)
        }

        def load(value: FormValues) -> tuple[tuple[str, object], ...]:
            if not isinstance(value, Mapping):
                message = f"Editor section {key!r} needs a mapping value"
                raise TypeError(message)
            known = set(spec.field_keys)
            unknown = set(value) - known
            if unknown:
                message = f"Editor section {key!r} values contain unknown fields: {sorted(unknown)!r}"
                raise ValueError(message)
            return tuple((field_key, value.get(field_key)) for field_key in spec.field_keys)

        def dump(state: tuple[tuple[str, object], ...]) -> FormValues:
            return MappingProxyType(dict(state))

        def default_summary(value: FormValues) -> TextLike:
            parts = []
            for field in spec.items:
                if not isinstance(field, FormField):
                    continue
                formatted = field.format(value.get(field.key))
                parts.append(f"{display_text(field.label)}: {_formatted(formatted)}")
            return " · ".join(parts)

        return EditorSection[tuple[tuple[str, object], ...], FormValues, RenderTarget](
            key,
            label,
            initial=tuple(initial_values.items()),
            load=load,
            dump=dump,
            summary=summary or default_summary,
            form=spec,
        )

    @classmethod
    def from_pattern(
        cls,
        key: str,
        label: TextLike,
        machine: StateMachine[StateT, RenderTargetT],
        *,
        load: Callable[[ValueT], StateT],
        dump: Callable[[StateT], ValueT],
        summary: Callable[[ValueT], TextLike],
        issues: Callable[[StateT], Iterable[FormIssue]] | None = None,
    ) -> EditorSection[StateT, ValueT, RenderTargetT]:
        """Adapt a nested pure machine into an editor section."""
        section: EditorSection[StateT, ValueT, RenderTargetT] = EditorSection(
            key,
            label,
            initial=machine.initial_state,
            load=load,
            dump=dump,
            summary=summary,
            machine=machine,
            issues=issues,
        )
        return section

    def value(self, state: EditorState) -> ValueT:
        """Return this section's precisely typed current value."""
        slot = next((candidate for candidate in state.sections if candidate.key == self.key), None)
        if slot is None:
            message = f"Editor state does not contain section {self.key!r}"
            raise KeyError(message)
        return self.dump(cast(StateT, slot.state))

    def form_prefill(self, state: StateT) -> FormValues:
        """Project a form section to string-keyed values, rejecting a mismatched adapter."""
        value = self.dump(state)
        if not isinstance(value, Mapping):
            message = f"Editor section {self.key!r} with a form must dump a mapping"
            raise TypeError(message)
        result: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                message = f"Editor section {self.key!r} form value keys must be strings"
                raise TypeError(message)
            result[key] = item
        return result


class Editor[RenderTargetT: RenderTarget = RenderTarget]:
    """A pure editor whose form and nested-machine sections share one commit boundary."""

    def __init__(
        self,
        title: TextLike,
        sections: Iterable[EditorSection[Any, Any, RenderTargetT]],
        *,
        key: str = "editor",
        preview: Callable[[EditorValues], ContentLike[RenderTargetT]] | None = None,
        commit: CommitMode = CommitMode.EXPLICIT,
        commit_label: TextLike | None = None,
        validate: Callable[[EditorValues], Iterable[FormIssue]] | None = None,
    ) -> None:
        self.title = title
        self.key = require_key(key, name="Editor.key")
        self.sections = tuple(sections)
        if not self.sections:
            message = "Editor needs at least one section"
            raise ValueError(message)
        keys = [section.key for section in self.sections]
        if len(set(keys)) != len(keys):
            message = f"Editor section keys must be unique: {keys!r}"
            raise ValueError(message)
        self._sections = {section.key: section for section in self.sections}
        self.preview = preview
        self.commit = commit
        self.commit_label = commit_label
        self.validate = validate
        self._initial_state = self._state_from({})

    @property
    def initial_state(self) -> EditorState:
        return self._initial_state

    def initial_from(self, values: EditorValues) -> EditorState:
        """Build initial section states from keyed public values."""
        unknown = set(values) - set(self._sections)
        if unknown:
            message = f"Editor initial values contain unknown sections: {sorted(unknown)!r}"
            raise ValueError(message)
        return self._state_from(values)

    def _state_from(self, values: EditorValues) -> EditorState:
        slots = []
        for section in self.sections:
            state = section.initial if section.key not in values else section.load(values[section.key])
            slots.append(EditorSectionState(section.key, state, section.dump(state)))
        return EditorState(tuple(slots))

    def build_component(
        self,
        *,
        initial: EditorValues | EditorState | None = None,
        on_commit: EditorCommitHandler | None = None,
    ) -> ComponentDriver[EditorState, RenderTargetT]:
        """Build an in-memory editor and dispatch each committed value change once."""
        state = self.initial_from(initial) if isinstance(initial, Mapping) else initial

        async def changed(event: TransitionEvent[EditorState]) -> None:
            if on_commit is None:
                return
            changed_keys = self._committed_changes(event.previous, event.state)
            if changed_keys:
                await on_commit(event, self.committed_values(event.state), changed_keys)

        if state is None:
            return ComponentDriver(self, on_change=changed)
        return ComponentDriver(self, initial=state, on_change=changed)

    def _slot(self, state: EditorState, key: MachineKeySegment) -> EditorSectionState | None:
        return next((slot for slot in state.sections if slot.key == key), None)

    def values(self, state: EditorState) -> EditorValues:
        """Project all current section states to their public values."""
        return MappingProxyType({slot.key: self._sections[slot.key].dump(slot.state) for slot in state.sections})

    def committed_values(self, state: EditorState) -> EditorValues:
        """Return the last committed value of every section."""
        return MappingProxyType({slot.key: slot.committed for slot in state.sections})

    def dirty_sections(self, state: EditorState) -> frozenset[str]:
        """Return sections whose projected value differs from their commit snapshot."""
        return frozenset(
            slot.key for slot in state.sections if self._sections[slot.key].dump(slot.state) != slot.committed
        )

    def issues(self, state: EditorState) -> tuple[FormIssue, ...]:
        """Return nested-section and aggregate commit violations."""
        issues: list[FormIssue] = []
        for slot in state.sections:
            section = self._sections[slot.key]
            if section.issues is not None:
                issues.extend(section.issues(slot.state))
        if self.validate is not None:
            issues.extend(self.validate(self.values(state)))
        return tuple(issues)

    def _replace_slot(self, state: EditorState, replacement: EditorSectionState) -> EditorState:
        return replace(
            state,
            sections=tuple(replacement if slot.key == replacement.key else slot for slot in state.sections),
        )

    def _commit_valid(self, state: EditorState) -> EditorState:
        if self.commit is CommitMode.EXPLICIT or self.issues(state):
            return state
        dirty = self.dirty_sections(state)
        if not dirty:
            return state
        return replace(
            state,
            sections=tuple(
                replace(slot, committed=self._sections[slot.key].dump(slot.state)) if slot.key in dirty else slot
                for slot in state.sections
            ),
        )

    def _nested_action(self, action: str) -> tuple[EditorSection[Any, Any, RenderTargetT], str] | None:
        nested = NestedAction.parse(action)
        if nested is None:
            return None
        section = self._sections.get(nested.key)
        return (section, nested.action) if section is not None and section.machine is not None else None

    def transition(
        self,
        state: EditorState,
        action: str,
        *,
        values: tuple[str, ...] = (),
        submitted: FormValues | None = None,
    ) -> EditorState:
        if action == "back":
            return replace(state, editing=None)
        if action == "save" and self.commit is CommitMode.EXPLICIT:
            if self.issues(state):
                return state
            dirty = self.dirty_sections(state)
            if not dirty:
                return state
            return replace(
                state,
                sections=tuple(
                    replace(slot, committed=self._sections[slot.key].dump(slot.state)) if slot.key in dirty else slot
                    for slot in state.sections
                ),
            )
        if (key := match_keyed_action(action, "edit")) is not None:
            section = self._sections.get(key)
            return replace(state, editing=key) if section is not None and section.machine is not None else state
        if (key := match_keyed_action(action, "submit")) is not None and submitted is not None:
            section = self._sections.get(key)
            slot = self._slot(state, key)
            if section is None or section.form is None or slot is None:
                return state
            changed = self._replace_slot(state, replace(slot, state=section.load(submitted)))
            return self._commit_valid(changed)
        nested = self._nested_action(action)
        if nested is None:
            return state
        section, nested_action = nested
        slot = self._slot(state, section.key)
        assert slot is not None and section.machine is not None
        nested_state = section.machine.transition(
            slot.state,
            nested_action,
            values=values,
            submitted=submitted,
        )
        changed = self._replace_slot(state, replace(slot, state=nested_state))
        return self._commit_valid(changed)

    def form_for(self, state: EditorState, action: str) -> FormSpec | None:
        """Resolve direct and nested routed form actions."""
        if (key := match_keyed_action(action, "submit")) is not None:
            section = self._sections.get(key)
            slot = self._slot(state, key)
            if section is None or section.form is None or slot is None:
                return None
            return section.form.with_prefill(section.form_prefill(slot.state))
        nested = self._nested_action(action)
        if nested is None:
            return None
        section, nested_action = nested
        slot = self._slot(state, section.key)
        machine = section.machine
        if slot is None or not isinstance(machine, FormPresentingMachine):
            return None
        return machine.form_for(slot.state, nested_action)

    @staticmethod
    def _committed_changes(previous: EditorState, state: EditorState) -> frozenset[str]:
        before = {slot.key: slot.committed for slot in previous.sections}
        return frozenset(slot.key for slot in state.sections if before.get(slot.key) != slot.committed)

    def render(
        self, state: EditorState, controls: MachineControls[EditorState, RenderTargetT]
    ) -> DocumentLike[RenderTargetT]:
        if state.editing is not None:
            section = self._sections.get(state.editing)
            slot = self._slot(state, state.editing)
            if section is not None and section.machine is not None and slot is not None:
                nested = section.machine.render(
                    slot.state,
                    _NestedControls(
                        controls,
                        self.key,
                        section.key,
                        getattr(section.machine, "key", None),
                    ),
                )
                return stack(
                    heading(self.title),
                    heading(section.label, level=3),
                    *as_document(nested).children,
                    action_controls(
                        controls.action_control(controls.chrome.back, "back", key=f"{self.key}.back"),
                        key=f"{self.key}.workspace",
                        display=ControlDisplay.INDIVIDUAL,
                    ),
                )

        current_values = self.values(state)
        preview = (
            controls.content(
                normalize_content(self.preview(current_values), name="Editor.preview"),
                prefix=f"{self.key}.preview",
            )
            if self.preview is not None
            else ()
        )
        section_nodes = []
        for section in self.sections:
            slot = self._slot(state, section.key)
            assert slot is not None
            value = section.dump(slot.state)
            if section.form is not None:
                edit = controls.form(
                    section.form.with_prefill(section.form_prefill(slot.state)),
                    keyed_action("submit", section.key),
                    key=f"{self.key}.{section.key}",
                    label=controls.chrome.edit,
                )
            else:
                edit = controls.action_control(
                    controls.chrome.edit,
                    keyed_action("edit", section.key),
                    key=f"{self.key}.{section.key}",
                )
            section_nodes.append(
                stack(
                    heading(section.label, level=3),
                    paragraph(section.summary(value)),
                    edit
                    if isinstance(edit, FormTrigger)
                    else action_controls(edit, key=f"{self.key}.{section.key}.actions"),
                )
            )

        issues = self.issues(state)
        dirty = self.dirty_sections(state)
        commit = (
            action_controls(
                controls.action_control(
                    self.commit_label or controls.chrome.save,
                    "save",
                    key=f"{self.key}.save",
                    available=not issues,
                ),
                key=f"{self.key}.commit",
                display=ControlDisplay.INDIVIDUAL,
            )
            if self.commit is CommitMode.EXPLICIT and dirty
            else None
        )
        return stack(
            heading(self.title),
            *preview,
            *section_nodes,
            status(controls.chrome.unsaved, tone=Tone.INFO) if dirty else None,
            *(status(issue.message, tone=Tone.DANGER) for issue in issues),
            commit,
        )


class _NestedControls[ParentStateT, ChildStateT, RenderTargetT: RenderTarget]:
    """Namespace child-machine controls through an Editor transition."""

    def __init__(
        self,
        parent: MachineControls[ParentStateT, RenderTargetT],
        editor_key: str,
        section_key: MachineKeySegment,
        pattern_key: str | None,
    ) -> None:
        self.parent = parent
        self.editor_key = editor_key
        self.section_key = section_key
        self.pattern_key = pattern_key
        self.chrome = parent.chrome

    def _action(self, action: str) -> str:
        return NestedAction(self.section_key, action).encode()

    def _key(self, key: str) -> str:
        if self.pattern_key is not None:
            key = key.removeprefix(f"{self.pattern_key}.")
        return f"{self.editor_key}.{self.section_key}.{key}"

    def content(
        self, content: Sequence[ContentItem[RenderTargetT]], *, prefix: str
    ) -> tuple[LayoutNode[RenderTargetT], ...]:
        return self.parent.content(content, prefix=self._key(prefix))

    def action_control(
        self,
        label: TextLike,
        action_name: str,
        *,
        key: str,
        tone: Tone = Tone.NEUTRAL,
        emphasis: Emphasis = Emphasis.NORMAL,
        available: bool = True,
    ) -> ActionControl | RoutedActionControl:
        return self.parent.action_control(
            label,
            self._action(action_name),
            key=self._key(key),
            tone=tone,
            emphasis=emphasis,
            available=available,
        )

    def choices(
        self,
        entries: Sequence[Choice],
        action_name: str,
        *,
        key: str,
        selected: tuple[str, ...],
        minimum: int,
        maximum: int,
        placeholder: TextLike | None = None,
        available: bool = True,
    ) -> Choices | RoutedChoices:
        return self.parent.choices(
            entries,
            self._action(action_name),
            key=self._key(key),
            selected=selected,
            minimum=minimum,
            maximum=maximum,
            placeholder=placeholder,
            available=available,
        )

    def form(
        self,
        spec: FormLike,
        action_name: str,
        *,
        key: str,
        label: TextLike,
        tone: Tone = Tone.NEUTRAL,
        emphasis: Emphasis = Emphasis.NORMAL,
    ) -> FormTrigger | RoutedActionControl:
        return self.parent.form(
            spec,
            self._action(action_name),
            key=self._key(key),
            label=label,
            tone=tone,
            emphasis=emphasis,
        )


__all__ = [
    "Editor",
    "EditorCommitHandler",
    "EditorSection",
    "EditorSectionState",
    "EditorState",
    "EditorValues",
]
