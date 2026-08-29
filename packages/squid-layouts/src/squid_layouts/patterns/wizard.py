"""Branching multi-step forms over the shared two-shell state machine."""

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, replace

from squid_layouts.factories import actions, field, fields, heading, progress, stack
from squid_layouts.forms import Form, FormField, FormLike, FormSpec
from squid_layouts.frozen import FrozenMapping
from squid_layouts.patterns._content import ContentItem, ContentLike, normalize_content, require_key
from squid_layouts.patterns.shells import ComponentShell, PatternControls, PatternEvent
from squid_layouts.runtime.component import RenderResult
from squid_layouts.semantic import Action, ActionDisplay, FormTrigger, LayoutNode, RoutedAction, Tone
from squid_layouts.text import TextLike

REVIEW_STEP = "@review"
"""The reserved `WizardState.current` value naming the review screen rather than a step."""


@dataclass(frozen=True, slots=True)
class WizardAnswer:
    """One retained step submission in route-serializable tuple form."""

    step: str
    values: tuple[tuple[str, object], ...]


@dataclass(frozen=True, slots=True)
class WizardReview:
    """A final screen that shows every answer and lets the reader jump back to one."""

    label: TextLike | None = None
    """Heading and control wording for the review destination; `None` uses chrome."""
    summarize: Callable[[WizardAnswers], ContentLike] | None = None
    """Replace the default per-step rows with content of your own."""


@dataclass(frozen=True, slots=True)
class WizardState:
    """Current step plus every retained answer, including hidden branch orphans.

    Where the reader *is* and where the reader *returns to* are two facts, so they are two
    fields: `current` holds `REVIEW_STEP` while the review screen is up, and `reviewing`
    says review is home -- it stays set while a jumped edit is in progress, which is what
    makes that edit come back rather than resuming the march.
    """

    current: str
    answers: tuple[WizardAnswer, ...] = ()
    complete: bool = False
    reviewing: bool = False


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

    def __init__(
        self,
        title: TextLike,
        steps: StepSource,
        *,
        key: str = "wizard",
        review: WizardReview | bool = False,
    ) -> None:
        self.key = require_key(key, name="Wizard.key")
        self.title = title
        self.steps = steps
        self.review = WizardReview() if review is True else (review or None)
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
        resolved = tuple(self.steps(FrozenMapping(answer_map)) if callable(self.steps) else self.steps)
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
        if REVIEW_STEP in keys:
            message = f"Wizard step key {REVIEW_STEP!r} is reserved for the review screen"
            raise ValueError(message)
        return resolved

    def live_steps(self, state: WizardState) -> tuple[WizardStep, ...]:
        """Return the branch visible for all answers currently retained."""
        return self._steps(state.answers)

    def live_answers(self, state: WizardState) -> WizardAnswers:
        """Return only answers belonging to the current computed branch."""
        retained = self._answer_map(state.answers)
        return FrozenMapping({step.key: retained[step.key] for step in self.live_steps(state) if step.key in retained})

    def answered(self, state: WizardState) -> bool:
        """Whether every live form step has an answer — what gates Finish."""
        retained = self._answer_map(state.answers)
        return all(step.key in retained for step in self.live_steps(state) if step.form is not None)

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
        at_review = state.current == REVIEW_STEP
        # Review is home once visited, so an edit reached from it returns there rather than
        # silently resuming the march and losing the reader's place.
        returns_to_review = self.review is not None and state.reviewing
        if self.review is not None and action == "review":
            return replace(state, current=REVIEW_STEP, reviewing=True, complete=False)
        if self.review is not None and action.startswith("goto:"):
            step_key = action.removeprefix("goto:")
            if step_key not in {step.key for step in live}:
                return state
            return replace(state, current=step_key, reviewing=True, complete=False)
        index = self._index(live, state.current)
        if action == "back":
            if at_review:
                return state
            if returns_to_review:
                return replace(state, current=REVIEW_STEP, complete=False)
            return replace(state, current=live[max(0, index - 1)].key, complete=False)
        if action == "next":
            if returns_to_review:
                return replace(state, current=REVIEW_STEP, complete=False)
            return replace(state, current=live[min(len(live) - 1, index + 1)].key, complete=False)
        if action == "finish":
            # The state machine enforces completeness; the render only reflects it.
            if self.review is not None and not self.answered(state):
                return state
            return replace(state, complete=True)
        if not action.startswith("submit:") or submitted is None:
            return state

        step_key = action.removeprefix("submit:")
        if step_key not in {step.key for step in live}:
            return state
        submitted_index = self._index(live, step_key)
        answers = self._store(state.answers, step_key, submitted)
        recomputed = self._steps(answers)
        if returns_to_review:
            return WizardState(REVIEW_STEP, answers, reviewing=True)
        recomputed_index = next(
            (position for position, step in enumerate(recomputed) if step.key == step_key),
            min(submitted_index, len(recomputed) - 1),
        )
        if recomputed_index >= len(recomputed) - 1:
            if self.review is not None:
                return WizardState(REVIEW_STEP, answers, reviewing=True)
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

    @staticmethod
    def _summarize_step(step: WizardStep, answer: Mapping[str, object] | None, unanswered: TextLike) -> TextLike:
        """One step's answers as a single line, each value through its field's prefill form."""
        if step.form is None or answer is None:
            return unanswered
        shown = [
            text for form_field in step.form.fields if (text := _prefill_text(form_field, answer.get(form_field.key)))
        ]
        return ", ".join(shown) if shown else unanswered

    def _render_review(
        self,
        state: WizardState,
        controls: PatternControls[WizardState],
        review: WizardReview,
    ) -> RenderResult:
        live = self.live_steps(state)
        answered = self._answer_map(state.answers)
        steps = tuple(step for step in live if step.form is not None)
        if review.summarize is not None:
            body: tuple[LayoutNode, ...] = controls.content(
                normalize_content(review.summarize(self.live_answers(state)), name=f"Wizard {self.key!r} review"),
                prefix=f"{self.key}-review",
            )
        else:
            body = (
                (
                    fields(
                        *(
                            field(
                                step.label,
                                self._summarize_step(step, answered.get(step.key), controls.chrome.unanswered),
                            )
                            for step in steps
                        )
                    ),
                )
                if steps
                else ()
            )

        return stack(
            heading(self.title),
            heading(review.label if review.label is not None else controls.chrome.review, level=3),
            *body,
            actions(
                *(controls.action(step.label, f"goto:{step.key}", key=f"{self.key}.goto.{step.key}") for step in steps),
                key=f"{self.key}.review",
            )
            if steps
            else None,
            actions(
                controls.action(
                    controls.chrome.finish,
                    "finish",
                    key=f"{self.key}.finish",
                    tone=Tone.SUCCESS,
                    available=self.answered(state),
                ),
                key=f"{self.key}.chrome",
                display=ActionDisplay.INDIVIDUAL,
            ),
        )

    def render(self, state: WizardState, controls: PatternControls[WizardState]) -> RenderResult:
        if self.review is not None and state.current == REVIEW_STEP:
            return self._render_review(state, controls, self.review)
        live = self.live_steps(state)
        index = self._index(live, state.current)
        current = live[index]
        next_step = live[index + 1] if index + 1 < len(live) else None
        # A step reached by jumping back from review returns there when it is done, so its
        # primary control says "edit this", not "continue the march".
        editing = self.review is not None and state.reviewing
        last_label = controls.chrome.review if self.review is not None else controls.chrome.finish
        last_action = "review" if self.review is not None else "finish"

        if current.form is not None:
            primary = controls.form(
                self._prefilled(current, state),
                f"submit:{current.key}",
                key=f"{self.key}.{current.key}",
                label=controls.chrome.edit if editing else last_label if next_step is None else "Continue",
            )
        elif next_step is not None and next_step.form is not None:
            primary = controls.form(
                self._prefilled(next_step, state),
                f"submit:{next_step.key}",
                key=f"{self.key}.{next_step.key}",
                label=controls.chrome.next,
            )
        elif editing:
            primary = controls.action(controls.chrome.review, "review", key=f"{self.key}.primary")
        else:
            primary = controls.action(
                last_label if next_step is None else controls.chrome.next,
                last_action if next_step is None else "next",
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
                    available=index > 0 or editing,
                ),
                primary if isinstance(primary, Action | RoutedAction) else None,
                key=f"{self.key}.chrome",
                display=ActionDisplay.INDIVIDUAL,
            ),
        )


def _prefill_text(form_field: FormField[object], value: object) -> str:
    """One answer value as review text, through the field's own prefill conversion."""
    if value is None:
        return ""
    shown = form_field.format_prefill(value)
    if isinstance(shown, list | tuple):
        return ", ".join(str(item) for item in shown)
    return str(shown)
