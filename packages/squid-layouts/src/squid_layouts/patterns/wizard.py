"""Branching multi-step forms over the shared two-shell state machine."""

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType

from squid_layouts.factories import actions, heading, progress, stack
from squid_layouts.forms import Form, FormLike, FormSpec
from squid_layouts.patterns._content import ContentItem, ContentLike, normalize_content, require_key
from squid_layouts.patterns.shells import ComponentShell, PatternControls, PatternEvent
from squid_layouts.runtime.component import RenderResult
from squid_layouts.semantic import Action, ActionDisplay, FormTrigger, RoutedAction
from squid_layouts.text import TextLike


@dataclass(frozen=True, slots=True)
class WizardAnswer:
    """One retained step submission in route-serializable tuple form."""

    step: str
    values: tuple[tuple[str, object], ...]


@dataclass(frozen=True, slots=True)
class WizardState:
    """Current step plus every retained answer, including hidden branch orphans."""

    current: str
    answers: tuple[WizardAnswer, ...] = ()
    complete: bool = False


@dataclass(frozen=True, slots=True, init=False)
class WizardStep:
    """One keyed form or plain-content step."""

    key: str
    label: TextLike
    form: FormSpec | None
    content: tuple[ContentItem, ...]

    def __init__(self, key: str, label: TextLike, body: FormLike | ContentLike) -> None:
        require_key(key, name="WizardStep.key")
        if isinstance(body, Form):
            form_spec: FormSpec | None = body.spec()
            content: tuple[ContentItem, ...] = ()
        elif isinstance(body, FormSpec):
            form_spec = body
            content = ()
        else:
            form_spec = None
            content = normalize_content(body, name=f"WizardStep {key!r}.content")
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "label", label)
        object.__setattr__(self, "form", form_spec)
        object.__setattr__(self, "content", content)


type WizardAnswers = Mapping[str, Mapping[str, object]]
type StepSource = Iterable[WizardStep] | Callable[[WizardAnswers], Iterable[WizardStep]]
type WizardFinishHandler = Callable[[PatternEvent[WizardState], WizardAnswers], Awaitable[None]]


class Wizard:
    """A pure branching wizard whose step list is recomputed after every answer."""

    def __init__(self, title: TextLike, steps: StepSource, *, key: str = "wizard") -> None:
        self.key = require_key(key, name="Wizard.key")
        self.title = title
        self.steps = steps
        initial_steps = self._steps(())
        if not initial_steps:
            message = "Wizard needs at least one initial step"
            raise ValueError(message)
        self._initial_state = WizardState(initial_steps[0].key)

    @property
    def initial_state(self) -> WizardState:
        return self._initial_state

    def component(
        self,
        *,
        initial: WizardState | None = None,
        on_finish: WizardFinishHandler | None = None,
    ) -> ComponentShell[WizardState]:
        """Build an in-memory wizard shell and dispatch Finish once."""

        async def changed(event: PatternEvent[WizardState]) -> None:
            if on_finish is not None and event.state.complete and not event.previous.complete:
                await on_finish(event, self.live_answers(event.state))

        return ComponentShell(self, initial=initial, on_change=changed)

    @staticmethod
    def _answer_map(answers: tuple[WizardAnswer, ...]) -> dict[str, Mapping[str, object]]:
        return {answer.step: dict(answer.values) for answer in answers}

    def _steps(self, answers: tuple[WizardAnswer, ...]) -> tuple[WizardStep, ...]:
        answer_map = self._answer_map(answers)
        resolved = tuple(self.steps(MappingProxyType(answer_map)) if callable(self.steps) else self.steps)
        if any(not isinstance(step, WizardStep) for step in resolved):
            message = "Wizard steps must contain WizardStep instances"
            raise TypeError(message)
        if not resolved:
            message = "Wizard's computed branch must contain at least one step"
            raise ValueError(message)
        keys = [step.key for step in resolved]
        if len(set(keys)) != len(keys):
            message = f"Wizard step keys must be unique: {keys!r}"
            raise ValueError(message)
        return resolved

    def live_steps(self, state: WizardState) -> tuple[WizardStep, ...]:
        """Return the branch visible for all answers currently retained."""
        return self._steps(state.answers)

    def live_answers(self, state: WizardState) -> WizardAnswers:
        """Return only answers belonging to the current computed branch."""
        retained = self._answer_map(state.answers)
        return MappingProxyType(
            {step.key: retained[step.key] for step in self.live_steps(state) if step.key in retained}
        )

    @staticmethod
    def _store(
        answers: tuple[WizardAnswer, ...], step: str, submitted: Mapping[str, object]
    ) -> tuple[WizardAnswer, ...]:
        replacement = WizardAnswer(step, tuple(submitted.items()))
        result = [answer for answer in answers if answer.step != step]
        result.append(replacement)
        return tuple(result)

    @staticmethod
    def _index(steps: tuple[WizardStep, ...], key: str) -> int:
        return next((index for index, step in enumerate(steps) if step.key == key), 0)

    def transition(
        self,
        state: WizardState,
        action: str,
        *,
        values: tuple[str, ...] = (),
        submitted: Mapping[str, object] | None = None,
    ) -> WizardState:
        del values
        live = self.live_steps(state)
        index = self._index(live, state.current)
        if action == "back":
            return replace(state, current=live[max(0, index - 1)].key, complete=False)
        if action == "next":
            return replace(state, current=live[min(len(live) - 1, index + 1)].key, complete=False)
        if action == "finish":
            return replace(state, complete=True)
        if not action.startswith("submit:") or submitted is None:
            return state

        step_key = action.removeprefix("submit:")
        if step_key not in {step.key for step in live}:
            return state
        submitted_index = self._index(live, step_key)
        answers = self._store(state.answers, step_key, submitted)
        recomputed = self._steps(answers)
        recomputed_index = next(
            (position for position, step in enumerate(recomputed) if step.key == step_key),
            min(submitted_index, len(recomputed) - 1),
        )
        if recomputed_index >= len(recomputed) - 1:
            return WizardState(recomputed[recomputed_index].key, answers, complete=True)
        return WizardState(recomputed[recomputed_index + 1].key, answers)

    def _prefilled(self, step: WizardStep, state: WizardState) -> FormSpec:
        assert step.form is not None
        attempted = self._answer_map(state.answers).get(step.key)
        return step.form if attempted is None else step.form.with_prefill(attempted)

    def form_for(self, state: WizardState, action: str) -> FormSpec | None:
        """Resolve a routed form action to the schema its handler should present."""
        if not action.startswith("submit:"):
            return None
        key = action.removeprefix("submit:")
        step = next((candidate for candidate in self.live_steps(state) if candidate.key == key), None)
        return None if step is None or step.form is None else self._prefilled(step, state)

    def render(self, state: WizardState, controls: PatternControls[WizardState]) -> RenderResult:
        live = self.live_steps(state)
        index = self._index(live, state.current)
        current = live[index]
        next_step = live[index + 1] if index + 1 < len(live) else None

        if current.form is not None:
            primary = controls.form(
                self._prefilled(current, state),
                f"submit:{current.key}",
                key=f"{self.key}.{current.key}",
                label="Finish" if next_step is None else "Continue",
            )
        elif next_step is not None and next_step.form is not None:
            primary = controls.form(
                self._prefilled(next_step, state),
                f"submit:{next_step.key}",
                key=f"{self.key}.{next_step.key}",
                label=controls.chrome.next,
            )
        else:
            primary = controls.action(
                "Finish" if next_step is None else controls.chrome.next,
                "finish" if next_step is None else "next",
                key=f"{self.key}.primary",
            )

        return stack(
            heading(self.title),
            progress(index + 1, maximum=len(live), label=f"Step {index + 1} of {len(live)}"),
            heading(current.label, level=3),
            *controls.content(current.content, prefix=f"step-{current.key}"),
            primary if isinstance(primary, FormTrigger) else None,
            actions(
                controls.action(
                    controls.chrome.back,
                    "back",
                    key=f"{self.key}.back",
                    available=index > 0,
                ),
                primary if isinstance(primary, Action | RoutedAction) else None,
                key=f"{self.key}.chrome",
                display=ActionDisplay.INDIVIDUAL,
            ),
        )
