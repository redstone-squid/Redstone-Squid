"""The one guard whose refusal is a rendered question.

`squid_ui.guards` decides whether a press is allowed and never renders. `confirm` answers
with a `Decision` shell, so it lives here beside the shells: `squid_ui` does not import this
package. It composes with `all_of`/`any_of` like any other `Guard`.
"""

from dataclasses import dataclass

from squid_ui.guards import (
    ADMIT,
    Challenge,
    ChallengeResolver,
    Guard,
    GuardLedger,
    GuardResult,
    approvals,
)
from squid_ui.interactions import ActionEvent
from squid_ui.palette import Tone
from squid_ui.runtime.component import Component
from squid_ui.text import TextLike
from squid_ui_widgets.decision import DecisionState
from squid_ui_widgets.decision import confirm as confirm_shell
from squid_ui_widgets.drivers import TransitionEvent

__all__ = ["confirm"]


@dataclass(frozen=True, slots=True)
class _Confirm:
    prompt: TextLike
    danger: bool
    deadline: float | None
    on_decline: TextLike | None

    async def admit(self, event: ActionEvent, ledger: GuardLedger) -> GuardResult:
        bucket = approvals(ledger, event.actor.id)
        outstanding: int = ledger.read(bucket, 0)
        if outstanding > 0:
            ledger.write(bucket, outstanding - 1)
            return ADMIT
        return Challenge(self._ask, deadline=self.deadline, on_decline=self.on_decline)

    def _ask(self, resolver: ChallengeResolver) -> Component:
        async def approved(event: TransitionEvent[DecisionState]) -> None:
            # Closing first answers the click inside its own deadline, and leaves nothing in
            # this handler that could fail after the press has been handed on.
            await event.source.finish()
            await resolver.approve()

        async def declined(event: TransitionEvent[DecisionState]) -> None:
            await event.source.finish()
            await resolver.decline()

        return confirm_shell(
            self.prompt,
            on_confirm=approved,
            on_cancel=declined,
            tone=Tone.DANGER if self.danger else Tone.NEUTRAL,
        )


def confirm(
    prompt: TextLike,
    *,
    danger: bool = True,
    deadline: float | None = 120.0,
    on_decline: TextLike | None = None,
) -> Guard:
    """Admit once the actor reaffirms this press, and ask them when they have not.

    The first press opens a private Confirm/Cancel dialog and executes nothing. Approving
    counts one approval for that actor and re-runs the whole guard chain, so access lost or a
    cooldown started while the dialog was open still refuses the press. `deadline` is the
    seconds the dialog stays answerable (`None`: until the mount ends); `on_decline` is
    private wording shown on Cancel.

    Put it last in an `all_of`: a chain should not ask a question it is about to deny, and
    an earlier guard's ledger writes are discarded by the pass that ends in the question.
    """
    return _Confirm(prompt, danger, deadline, on_decline)
