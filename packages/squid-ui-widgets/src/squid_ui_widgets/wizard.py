"""A mounted branching wizard with typed answers and completion callbacks."""

from typing import override

from squid_ui.target_types import RenderTarget
from squid_ui.text import TextLike
from squid_ui_widgets.drivers import ComponentDriver, TransitionEvent
from squid_ui_widgets.machines.wizard import REVIEW_STEP as REVIEW_STEP
from squid_ui_widgets.machines.wizard import StepSource as StepSource
from squid_ui_widgets.machines.wizard import Wizard as WizardMachine
from squid_ui_widgets.machines.wizard import WizardAnswer as WizardAnswer
from squid_ui_widgets.machines.wizard import WizardAnswers as WizardAnswers
from squid_ui_widgets.machines.wizard import WizardFinishHandler as WizardFinishHandler
from squid_ui_widgets.machines.wizard import WizardReview as WizardReview
from squid_ui_widgets.machines.wizard import WizardState as WizardState
from squid_ui_widgets.machines.wizard import WizardStep as WizardStep


class Wizard[RenderTargetT: RenderTarget = RenderTarget, ContextT = object](
    ComponentDriver[WizardState, RenderTargetT, ContextT]
):
    """Own a wizard's state and notify when incomplete state becomes complete.

    The callback receives only answers in the current branch. It may run again after
    navigation reopens the wizard, and must request finish_on_success if completion
    should close the presentation. Machine state is not persisted automatically.
    """

    @property
    @override
    def machine(self) -> WizardMachine[RenderTargetT, ContextT]:
        """The pure definition used by this component."""
        return self._definition

    def __init__(
        self,
        title: TextLike,
        steps: StepSource[RenderTargetT, ContextT],
        *,
        key: str = "wizard",
        review: WizardReview[RenderTargetT, ContextT] | bool = False,
        initial_state: WizardState | None = None,
        on_finish: WizardFinishHandler | None = None,
    ) -> None:
        machine = WizardMachine[RenderTargetT, ContextT](title, steps, key=key, review=review)

        async def changed(event: TransitionEvent[WizardState]) -> None:
            if on_finish is not None and event.state.complete and not event.previous.complete:
                await on_finish(event, machine.live_answers(event.state))

        self._definition = machine
        super().__init__(
            machine,
            initial=machine.initial_state if initial_state is None else initial_state,
            on_change=changed,
        )
