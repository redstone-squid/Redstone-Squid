"""Section-oriented editing over forms and nested pure machines."""

from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, cast

from squid_ui.document import Document
from squid_ui.factories import action_controls, heading, paragraph, stack, status
from squid_ui.forms import Form, FormField, FormIssue, FormLike, FormSpec
from squid_ui.runtime.component import RenderResult
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
from squid_ui.text import TextLike
from squid_ui_widgets._content import ContentLike, display_text, normalize_content, require_key
from squid_ui_widgets.commit import CommitMode
from squid_ui_widgets.drivers import ComponentDriver, MachineControls, StateMachine, TransitionEvent

type EditorValues = Mapping[str, object]
type EditorCommitHandler = Callable[[TransitionEvent[EditorState], EditorValues, frozenset[str]], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class EditorSectionState:
    """One section's interaction state and last committed projected value."""

    key: str
    state: object
    committed: object


@dataclass(frozen=True, slots=True)
class EditorState:
    """Every section state plus the nested workspace currently open."""

    sections: tuple[EditorSectionState, ...]
    editing: str | None = None


def _formatted(value: object) -> str:
    if isinstance(value, tuple | list):
        return ", ".join(display_text(item) for item in value)
    return display_text(value)


class EditorSection[StateT, ValueT]:
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
        machine: StateMachine[StateT] | None = None,
        form: FormSpec | None = None,
        issues: Callable[[StateT], Iterable[FormIssue]] | None = None,
    ) -> None:
        self.key = require_key(key, name="EditorSection.key")
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
        summary: Callable[[Mapping[str, object]], TextLike] | None = None,
    ) -> EditorSection[tuple[tuple[str, object], ...], Mapping[str, object]]:
        """Adapt one form schema into an editor section."""
        spec = form.spec() if isinstance(form, Form) else form
        initial_values = {
            field.key: spec.prefill.get(field.key, field.default)
            for field in spec.items
            if isinstance(field, FormField)
        }

        def load(value: Mapping[str, object]) -> tuple[tuple[str, object], ...]:
            if not isinstance(value, Mapping):
                message = f"Editor section {key!r} needs a mapping value"
                raise TypeError(message)
            known = set(spec.field_keys)
            unknown = set(value) - known
            if unknown:
                message = f"Editor section {key!r} values contain unknown fields: {sorted(unknown)!r}"
                raise ValueError(message)
            return tuple((field_key, value.get(field_key)) for field_key in spec.field_keys)

        def dump(state: tuple[tuple[str, object], ...]) -> Mapping[str, object]:
            return MappingProxyType(dict(state))

        def default_summary(value: Mapping[str, object]) -> TextLike:
            parts = []
            for field in spec.items:
                if not isinstance(field, FormField):
                    continue
                formatted = field.format(value.get(field.key))
                parts.append(f"{display_text(field.label)}: {_formatted(formatted)}")
            return " · ".join(parts)

        return cls(
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
        machine: StateMachine[StateT],
        *,
        load: Callable[[ValueT], StateT],
        dump: Callable[[StateT], ValueT],
        summary: Callable[[ValueT], TextLike],
        issues: Callable[[StateT], Iterable[FormIssue]] | None = None,
    ) -> EditorSection[StateT, ValueT]:
        """Adapt a nested pure machine into an editor section."""
        return cls(
            key,
            label,
            initial=machine.initial_state,
            load=load,
            dump=dump,
            summary=summary,
            machine=machine,
            issues=issues,
        )

    def value(self, state: EditorState) -> ValueT:
        """Return this section's precisely typed current value."""
        slot = next((candidate for candidate in state.sections if candidate.key == self.key), None)
        if slot is None:
            message = f"Editor state does not contain section {self.key!r}"
            raise KeyError(message)
        return self.dump(cast(StateT, slot.state))


class Editor:
    """A pure editor whose form and nested-machine sections share one commit boundary."""

    def __init__(
        self,
        title: TextLike,
        sections: Iterable[EditorSection[Any, Any]],
        *,
        key: str = "editor",
        preview: Callable[[EditorValues], ContentLike] | None = None,
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
    ) -> ComponentDriver[EditorState]:
        """Build an in-memory editor and dispatch each committed value change once."""
        state = self.initial_from(initial) if isinstance(initial, Mapping) else initial

        async def changed(event: TransitionEvent[EditorState]) -> None:
            if on_commit is None:
                return
            changed_keys = self._committed_changes(event.previous, event.state)
            if changed_keys:
                await on_commit(event, self.committed_values(event.state), changed_keys)

        return ComponentDriver(self, initial=state, on_change=changed)

    def _slot(self, state: EditorState, key: str) -> EditorSectionState | None:
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

    def _nested_action(self, action: str) -> tuple[EditorSection[Any, Any], str] | None:
        if not action.startswith("section:"):
            return None
        remainder = action.removeprefix("section:")
        for section in self.sections:
            prefix = f"{section.key}:"
            if remainder.startswith(prefix) and section.machine is not None:
                return section, remainder.removeprefix(prefix)
        return None

    def transition(
        self,
        state: EditorState,
        action: str,
        *,
        values: tuple[str, ...] = (),
        submitted: Mapping[str, object] | None = None,
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
        if action.startswith("edit:"):
            key = action.removeprefix("edit:")
            section = self._sections.get(key)
            return replace(state, editing=key) if section is not None and section.machine is not None else state
        if action.startswith("submit:") and submitted is not None:
            key = action.removeprefix("submit:")
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
        if action.startswith("submit:"):
            key = action.removeprefix("submit:")
            section = self._sections.get(key)
            slot = self._slot(state, key)
            if section is None or section.form is None or slot is None:
                return None
            return section.form.with_prefill(cast(Mapping[str, object], section.dump(slot.state)))
        nested = self._nested_action(action)
        if nested is None:
            return None
        section, nested_action = nested
        slot = self._slot(state, section.key)
        resolver = getattr(section.machine, "form_for", None)
        if slot is None or resolver is None:
            return None
        return cast(FormSpec | None, resolver(slot.state, nested_action))

    @staticmethod
    def _committed_changes(previous: EditorState, state: EditorState) -> frozenset[str]:
        before = {slot.key: slot.committed for slot in previous.sections}
        return frozenset(slot.key for slot in state.sections if before.get(slot.key) != slot.committed)

    @staticmethod
    def _nodes(rendered: RenderResult) -> tuple[LayoutNode, ...]:
        if isinstance(rendered, Document):
            return rendered.children
        return tuple(rendered) if isinstance(rendered, Sequence) else (rendered,)

    def render(self, state: EditorState, controls: MachineControls[EditorState]) -> RenderResult:
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
                    *self._nodes(nested),
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
                    section.form.with_prefill(cast(Mapping[str, object], value)),
                    f"submit:{section.key}",
                    key=f"{self.key}.{section.key}",
                    label=controls.chrome.edit,
                )
            else:
                edit = controls.action_control(
                    controls.chrome.edit,
                    f"edit:{section.key}",
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


class _NestedControls[ParentStateT, ChildStateT]:
    """Namespace child-machine controls through an Editor transition."""

    def __init__(
        self,
        parent: MachineControls[ParentStateT],
        editor_key: str,
        section_key: str,
        pattern_key: str | None,
    ) -> None:
        self.parent = parent
        self.editor_key = editor_key
        self.section_key = section_key
        self.pattern_key = pattern_key
        self.chrome = parent.chrome

    def _action(self, action: str) -> str:
        return f"section:{self.section_key}:{action}"

    def _key(self, key: str) -> str:
        if self.pattern_key is not None:
            key = key.removeprefix(f"{self.pattern_key}.")
        return f"{self.editor_key}.{self.section_key}.{key}"

    def content(self, content: Sequence[Any], *, prefix: str) -> tuple[LayoutNode, ...]:
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
