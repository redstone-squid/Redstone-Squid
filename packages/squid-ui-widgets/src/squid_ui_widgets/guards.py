"""Ask for confirmation before admitting an action through portable guards.

The guard returns a challenge whose question is a Decision component. It lives with the
widgets so the core guard vocabulary does not depend on rendering that question. The
frontend presents it and resumes the original action after approval.
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
    """Challenge an unconfirmed action and remember one approval."""

    prompt: TextLike
    danger: bool
    deadline: float | None
    on_decline: TextLike | None

    async def admit(self, event: ActionEvent, ledger: GuardLedger) -> GuardResult:
        """Consume prior approval or request a confirmation challenge."""
        bucket = approvals(ledger, event.actor.id)
        outstanding: int = ledger.read(bucket, 0)
        if outstanding > 0:
            ledger.write(bucket, outstanding - 1)
            return ADMIT
        return Challenge(self._ask, deadline=self.deadline, on_decline=self.on_decline)

    def _ask(self, resolver: ChallengeResolver) -> Component:
        """Build the private confirmation shell for this challenge."""

        async def approved(event: TransitionEvent[DecisionState]) -> None:
            # Request closure after this callback succeeds, before scheduling the guarded action.
            event.source.finish_on_success()
            await resolver.approve()

        async def declined(event: TransitionEvent[DecisionState]) -> None:
            event.source.finish_on_success()
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
    """Ask the actor to confirm before admitting the guarded action.

    Approval reruns the guard chain, including access and cooldown checks that may
    have changed while the question was open. Place confirm last in all_of so earlier
    guards can deny without opening a question. A challenged pass discards its writes;
    the approved pass consumes one confirmation for this actor.
    """
    return _Confirm(prompt, danger, deadline, on_decline)
