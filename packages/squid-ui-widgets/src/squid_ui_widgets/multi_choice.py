"""Paged multi-selection with explicit staging and commit semantics."""

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

from squid_ui.document import DocumentLike
from squid_ui.factories import action_controls, heading, paragraph, stack, status
from squid_ui.forms import ChoiceOption, FormSpec, MultiChoiceField
from squid_ui.semantic import Choice, ControlDisplay, FormTrigger, Tone, fallback
from squid_ui.text import TextLike
from squid_ui_widgets._actions import (
    MachineKeySegment,
    PageAction,
    PageDirection,
    keyed_action,
    match_keyed_action,
)
from squid_ui_widgets._content import display_text, require_key
from squid_ui_widgets._paging import PagePosition, window
from squid_ui_widgets.commit import CommitMode
from squid_ui_widgets.drivers import ComponentDriver, FormValues, MachineControls, TransitionEvent


@dataclass(frozen=True, slots=True)
class MultiChoiceGroup:
    """One labelled option group and the groups it excludes."""

    key: MachineKeySegment
    label: TextLike
    choices: tuple[Choice, ...]
    exclusive_with: tuple[MachineKeySegment, ...] = ()

    def __init__(
        self,
        key: str,
        label: TextLike,
        choices: Iterable[Choice],
        *,
        exclusive_with: Iterable[str] = (),
    ) -> None:
        key = MachineKeySegment(key, name="MultiChoiceGroup.key")
        entries = tuple(choices)
        keys = [entry.key for entry in entries]
        if not entries:
            message = f"MultiChoiceGroup {key!r} needs at least one choice"
            raise ValueError(message)
        if len(set(keys)) != len(keys):
            message = f"MultiChoiceGroup {key!r} choice keys must be unique: {keys!r}"
            raise ValueError(message)
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "label", label)
        object.__setattr__(self, "choices", entries)
        object.__setattr__(
            self,
            "exclusive_with",
            tuple(MachineKeySegment(rival, name="MultiChoiceGroup.exclusive_with") for rival in exclusive_with),
        )


@dataclass(frozen=True, slots=True)
class MultiChoiceState:
    """Staged and committed selections plus a zero-based page per group."""

    staged: tuple[str, ...] = ()
    committed: tuple[str, ...] = ()
    pages: tuple[tuple[MachineKeySegment, int], ...] = ()


type MultiChoiceCommitHandler = Callable[[TransitionEvent[MultiChoiceState], tuple[str, ...]], Awaitable[None]]


class MultiChoice:
    """A pure cross-page picker with one explicit Apply boundary."""

    def __init__(
        self,
        title: TextLike,
        groups: Iterable[MultiChoiceGroup],
        *,
        key: str = "choices",
        committed: Iterable[str] = (),
        minimum: int = 0,
        maximum: int | None = None,
        window_size: int = 25,
        commit: CommitMode = CommitMode.EXPLICIT,
    ) -> None:
        self.key = require_key(key, name="MultiChoice.key")
        self.title = title
        self.groups = tuple(groups)
        if not self.groups:
            message = "MultiChoice needs at least one group"
            raise ValueError(message)
        group_keys = [group.key for group in self.groups]
        if len(set(group_keys)) != len(group_keys):
            message = f"MultiChoice group keys must be unique: {group_keys!r}"
            raise ValueError(message)
        option_keys = [entry.key for group in self.groups for entry in group.choices]
        if len(set(option_keys)) != len(option_keys):
            message = "MultiChoice choice keys must be unique across groups"
            raise ValueError(message)
        unknown_rivals = {
            rival for group in self.groups for rival in group.exclusive_with if rival not in set(group_keys)
        }
        if unknown_rivals:
            message = f"MultiChoice exclusivity names unknown groups: {sorted(unknown_rivals)!r}"
            raise ValueError(message)
        if minimum < 0:
            message = "MultiChoice.minimum must not be negative"
            raise ValueError(message)
        if maximum is not None and maximum < minimum:
            message = "MultiChoice.maximum must be at least minimum"
            raise ValueError(message)
        if window_size < 1 or window_size > 25:
            message = "MultiChoice.window_size must be between 1 and 25"
            raise ValueError(message)
        self.minimum = minimum
        self.maximum = len(option_keys) if maximum is None else maximum
        self.window_size = window_size
        self.commit = commit
        self._choice_order = tuple(option_keys)
        self._choices = {entry.key: entry for group in self.groups for entry in group.choices}
        self._group_for = {entry.key: group.key for group in self.groups for entry in group.choices}
        initial = tuple(dict.fromkeys(committed))
        unknown = set(initial) - set(option_keys)
        if unknown:
            message = f"MultiChoice committed values are unknown: {sorted(unknown)!r}"
            raise ValueError(message)
        self._initial_state = MultiChoiceState(initial, initial)

    @property
    def initial_state(self) -> MultiChoiceState:
        return self._initial_state

    def build_component(
        self,
        *,
        initial: MultiChoiceState | None = None,
        on_commit: MultiChoiceCommitHandler | None = None,
    ) -> ComponentDriver[MultiChoiceState]:
        """Build an in-memory panel shell and dispatch each new commit once."""

        async def changed(event: TransitionEvent[MultiChoiceState]) -> None:
            if on_commit is not None and event.state.committed != event.previous.committed:
                await on_commit(event, event.state.committed)

        if initial is None:
            return ComponentDriver(self, on_change=changed)
        return ComponentDriver(self, initial=initial, on_change=changed)

    def _ordered(self, selected: Iterable[str]) -> tuple[str, ...]:
        values = set(selected)
        return tuple(key for key in self._choice_order if key in values) + tuple(
            sorted(key for key in values if key not in self._choices)
        )

    @staticmethod
    def _pages(state: MultiChoiceState) -> dict[MachineKeySegment, int]:
        return dict(state.pages)

    def _window(
        self, group: MultiChoiceGroup, state: MultiChoiceState, controls: MachineControls[MultiChoiceState]
    ) -> tuple[tuple[Choice, ...], int, int]:
        visible, position, extent = window(
            group.choices,
            key=f"{self.key}.{group.key}",
            position=PagePosition(self._pages(state).get(group.key, 0)),
            per_page=self.window_size,
            chrome=controls.chrome,
            identity=lambda entry: entry.key,
        )
        return visible, position.index, extent

    def _rivals(self, group_key: MachineKeySegment) -> frozenset[MachineKeySegment]:
        direct = next(group.exclusive_with for group in self.groups if group.key == group_key)
        inverse = tuple(group.key for group in self.groups if group_key in group.exclusive_with)
        return frozenset((*direct, *inverse))

    def errors(self, state: MultiChoiceState) -> tuple[str, ...]:
        """Return every commit-blocking violation for the staged set."""
        errors: list[str] = []
        count = len(state.staged)
        if count < self.minimum:
            errors.append(f"Select at least {self.minimum} options.")
        if count > self.maximum:
            errors.append(f"Select no more than {self.maximum} options.")
        unknown = set(state.staged) - set(self._choices)
        if unknown:
            errors.append("One or more selected options are no longer available.")
        selected_groups = {self._group_for[key] for key in state.staged if key in self._group_for}
        for group in self.groups:
            if group.key in selected_groups and self._rivals(group.key) & selected_groups:
                errors.append(f"{display_text(group.label)} cannot be combined with an exclusive group.")
                break
        return tuple(errors)

    def transition(
        self,
        state: MultiChoiceState,
        action: str,
        *,
        values: tuple[str, ...] = (),
        submitted: FormValues | None = None,
    ) -> MultiChoiceState:
        if action == "apply" and self.commit is CommitMode.EXPLICIT:
            return state if self.errors(state) else MultiChoiceState(state.staged, state.staged, state.pages)
        if action == "modal" and submitted is not None:
            raw = submitted.get("selection", ())
            # Modal submissions arrive untyped; option keys are strings, and this mirrors how
            # `MultiChoiceField.parse` coerces the same payload.
            values = (
                tuple(str(item) for item in raw)
                if isinstance(raw, list | tuple)
                else (() if raw is None else (str(raw),))
            )
            selected = tuple(key for key in self._choice_order if key in values)
            return self._commit_valid(MultiChoiceState(selected, state.committed, state.pages))
        if page_action := PageAction.parse(action):
            group = next((group for group in self.groups if group.key == page_action.key), None)
            if group is None:
                return state
            pages = self._pages(state)
            last_page = (len(group.choices) - 1) // self.window_size
            pages[page_action.key] = min(
                last_page,
                max(0, pages.get(page_action.key, 0) + page_action.direction.delta),
            )
            return MultiChoiceState(state.staged, state.committed, tuple(pages.items()))
        group_key = match_keyed_action(action, "select")
        if group_key is None:
            return state

        group = next((item for item in self.groups if item.key == group_key), None)
        if group is None:
            return state
        pages = max(1, (len(group.choices) + self.window_size - 1) // self.window_size)
        page = min(self._pages(state).get(group.key, 0), pages - 1)
        start = page * self.window_size
        visible = {entry.key for entry in group.choices[start : start + self.window_size] if entry.available}
        replacement = set(values) & visible
        staged = set(state.staged) - visible
        staged.update(replacement)
        if replacement:
            rivals = self._rivals(group.key)
            staged = {key for key in staged if self._group_for.get(key) not in rivals}
        return self._commit_valid(MultiChoiceState(self._ordered(staged), state.committed, state.pages))

    def _commit_valid(self, state: MultiChoiceState) -> MultiChoiceState:
        if self.commit is CommitMode.EXPLICIT or self.errors(state):
            return state
        return MultiChoiceState(state.staged, state.staged, state.pages)

    def _summary(self, state: MultiChoiceState) -> str:
        selected = state.committed if self.commit is CommitMode.IMMEDIATE else state.staged
        labels = [display_text(self._choices[key].label) for key in selected if key in self._choices]
        return f"{len(selected)} selected" + (f": {', '.join(labels)}" if labels else "")

    def form_for(self, state: MultiChoiceState, action: str) -> FormSpec | None:
        """Resolve the routed modal action to its small-panel form schema."""
        if action != "modal" or len(self._choice_order) > 25:
            return None
        return FormSpec(
            "Select options",
            (
                MultiChoiceField(
                    key="selection",
                    label="Options",
                    required=self.minimum > 0,
                    options=tuple(
                        ChoiceOption(
                            key,
                            self._choices[key].label,
                            key,
                            self._choices[key].description,
                        )
                        for key in self._choice_order
                    ),
                    minimum=self.minimum,
                    maximum=min(self.maximum, len(self._choice_order)),
                ),
            ),
            prefill={"selection": state.staged},
        )

    def render(self, state: MultiChoiceState, controls: MachineControls[MultiChoiceState]) -> DocumentLike:
        group_nodes = []
        staged = set(state.staged)
        for group in self.groups:
            visible, page, pages = self._window(group, state, controls)
            visible_keys = {entry.key for entry in visible}
            elsewhere = len(staged - visible_keys)
            allowance = min(self.window_size, self.maximum - elsewhere)
            picker = (
                controls.choices(
                    visible,
                    keyed_action("select", group.key),
                    key=f"{self.key}.{group.key}.select",
                    selected=tuple(key for key in state.staged if key in visible_keys),
                    minimum=0,
                    maximum=max(1, allowance, len(staged & visible_keys)),
                    placeholder=group.label,
                )
                if allowance > 0 or bool(staged & visible_keys)
                else status("Selection limit reached in another group.", tone=Tone.INFO)
            )
            pager = (
                action_controls(
                    controls.action_control(
                        controls.chrome.previous,
                        PageAction(group.key, PageDirection.PREVIOUS).encode(),
                        key=f"{self.key}.{group.key}.previous",
                        available=page > 0,
                    ),
                    controls.action_control(
                        controls.chrome.next,
                        PageAction(group.key, PageDirection.NEXT).encode(),
                        key=f"{self.key}.{group.key}.next",
                        available=page < pages - 1,
                    ),
                    key=f"{self.key}.{group.key}.pager",
                )
                if pages > 1
                else None
            )
            group_nodes.append(
                stack(
                    heading(group.label, level=3),
                    picker,
                    paragraph(controls.chrome.page_footer(page + 1, pages)) if pages > 1 else None,
                    pager,
                )
            )

        direct = stack(*group_nodes)
        if len(self._choice_order) <= 25:
            modal = self.form_for(state, "modal")
            assert modal is not None
            # `controls.form` answers either a `FormTrigger`, which is a layout node, or a
            # `RoutedActionControl`, which is a control and only legal inside a control
            # container. `fallback` takes layout nodes, so the routed half needs wrapping --
            # the same narrowing `collection.py` does with the same union.
            trigger = controls.form(modal, "modal", key=f"{self.key}.modal", label="Select options")
            alternate = (
                trigger
                if isinstance(trigger, FormTrigger)
                else action_controls(trigger, key=f"{self.key}.modal-action")
            )
            selection = fallback(direct, alternate)
        else:
            selection = direct
        errors = self.errors(state)
        return stack(
            heading(self.title),
            paragraph(self._summary(state)),
            *(status(message, tone=Tone.DANGER) for message in errors),
            selection,
            (
                action_controls(
                    controls.action_control(
                        controls.chrome.apply,
                        "apply",
                        key=f"{self.key}.apply",
                        available=not errors and state.staged != state.committed,
                    ),
                    key=f"{self.key}.commit",
                    display=ControlDisplay.INDIVIDUAL,
                )
                if self.commit is CommitMode.EXPLICIT
                else None
            ),
        )
