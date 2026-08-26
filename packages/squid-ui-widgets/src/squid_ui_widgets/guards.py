"""The one guard whose refusal is a rendered question.

`squid_ui.guards` is the admission vocabulary and stays portable: it decides whether a
press is allowed and has no opinion about what a denial looks like. `confirm` is the single
member that does have one -- its challenge is a `Decision` shell -- so it lives here, beside
the shells, rather than making the vocabulary import its own rendering.

That inversion is why this module exists. The engine used to reach forward into `patterns`
through a function-local import, with a comment explaining that the vocabulary could not
depend on the rendering at import time. Moving the guard up removes the edge instead of
working around it, and `all_of`/`any_of` compose with it exactly as before because they accept
any `Guard`.
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
from squid_ui_widgets.shells import PatternEvent

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
        async def approved(event: PatternEvent[DecisionState]) -> None:
            # Closing first answers the click inside its own deadline, and leaves nothing in
            # this handler that could fail after the press has been handed on.
            await event.source.finish()
            await resolver.approve()

        async def declined(event: PatternEvent[DecisionState]) -> None:
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

    The two-press "are you sure" state machine, declared where the control is rather than
    hand-rolled in component state: no armed flag, no early return, no relabelling. The
    first press opens a private confirmation and executes nothing; approving it re-runs the
    whole funnel, so access lost or a cooldown started while the dialog was open still
    refuse the press the actor confirmed.

    Put it last in an `all_of`: a chain should not ask a question it is about to deny, and
    an earlier guard's record is discarded by the pass that ends in the question.
    """
    return _Confirm(prompt, danger, deadline, on_decline)
