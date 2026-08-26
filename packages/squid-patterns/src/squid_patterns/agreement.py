"""Actor-keyed mounted agreement collection."""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from squid_layouts.chrome import CHROME_CONTEXT, DEFAULT_CHROME
from squid_layouts.factories import action, actions, bullet, bullets, stack, status
from squid_layouts.interactions import ActionEvent, ActionMode, PressEvent
from squid_layouts.runtime.component import Component, RenderResult
from squid_layouts.runtime.reactivity import state
from squid_layouts.semantic import ActionDisplay, Emphasis, Tone
from squid_layouts.text import Message, TextLike
from squid_patterns._content import ContentLike, normalize_content, render_content, require_key


@dataclass(frozen=True, slots=True)
class AgreementParticipant:
    """One actor eligible to approve an agreement."""

    actor_id: str
    display: TextLike

    def __post_init__(self) -> None:
        if not self.actor_id:
            message = "agreement participant actor id must not be empty"
            raise ValueError(message)


type AgreementResolveHandler = Callable[[PressEvent, tuple[str, ...]], Awaitable[None]]


class Agreement(Component):
    """Collect actor-keyed approvals to a declared threshold."""

    approved: tuple[str, ...] = state((), persist=False)
    resolved: bool = state(default=False, persist=False)

    def __init__(
        self,
        prompt: ContentLike,
        participants: Sequence[AgreementParticipant],
        *,
        key: str = "agreement",
        require: int | Literal["all"] = "all",
        allow_withdraw: bool = True,
        on_resolve: AgreementResolveHandler | None = None,
    ) -> None:
        self.key = require_key(key, name="Agreement.key")
        self.prompt = normalize_content(prompt, name="Agreement.prompt")
        self.participants = tuple(participants)
        if not self.participants:
            message = "Agreement needs at least one participant"
            raise ValueError(message)
        actor_ids = tuple(participant.actor_id for participant in self.participants)
        if len(set(actor_ids)) != len(actor_ids):
            message = "Agreement participant actor ids must be unique"
            raise ValueError(message)
        if require == "all":
            self.required = len(self.participants)
        elif isinstance(require, bool) or not 1 <= require <= len(self.participants):
            message = "Agreement require must be 'all' or a reachable positive threshold"
            raise ValueError(message)
        else:
            self.required = require
        self.allow_withdraw = allow_withdraw
        self.on_resolve = on_resolve

    def render(self) -> RenderResult:
        chrome = self.inject(CHROME_CONTEXT, DEFAULT_CHROME)
        approved = frozenset(self.approved)
        participant_rows = tuple(
            bullet(
                Message(
                    "{mark} {display}",
                    {
                        "mark": "✓" if participant.actor_id in approved else "○",
                        "display": participant.display,
                    },
                ),
                key=participant.actor_id,
            )
            for participant in self.participants
        )
        controls = [
            action(
                chrome.approve,
                self._approve,
                key=f"{self.key}.approve",
                available=not self.resolved,
                mode=ActionMode.EXCLUSIVE,
                tone=Tone.SUCCESS,
                emphasis=Emphasis.STRONG,
            )
        ]
        if self.allow_withdraw:
            controls.append(
                action(
                    chrome.withdraw,
                    self._withdraw,
                    key=f"{self.key}.withdraw",
                    available=not self.resolved,
                    mode=ActionMode.EXCLUSIVE,
                )
            )
        return stack(
            *render_content(self, self.prompt, prefix=f"{self.key}.prompt"),
            bullets(*participant_rows, key=f"{self.key}.participants"),
            status(
                chrome.approved_count(len(self.approved), self.required),
                tone=Tone.SUCCESS if self.resolved else Tone.INFO,
                emphasis=Emphasis.STRONG if self.resolved else Emphasis.NORMAL,
            ),
            actions(*controls, key=f"{self.key}.controls", display=ActionDisplay.INDIVIDUAL),
        )

    def _participant(self, actor_id: str) -> bool:
        return any(participant.actor_id == actor_id for participant in self.participants)

    async def _approve(self, event: ActionEvent) -> None:
        if not isinstance(event, PressEvent):
            message = "Agreement actions require a press event"
            raise TypeError(message)
        actor_id = event.actor.id
        if self.resolved or not self._participant(actor_id) or actor_id in self.approved:
            await event.acknowledge()
            return
        selected = {*self.approved, actor_id}
        approved = tuple(participant.actor_id for participant in self.participants if participant.actor_id in selected)
        self.approved = approved
        if len(approved) >= self.required:
            self.resolved = True
            if self.on_resolve is not None:
                await self.on_resolve(event, approved)

    async def _withdraw(self, event: ActionEvent) -> None:
        if not isinstance(event, PressEvent):
            message = "Agreement actions require a press event"
            raise TypeError(message)
        actor_id = event.actor.id
        if self.resolved or not self._participant(actor_id) or actor_id not in self.approved:
            await event.acknowledge()
            return
        self.approved = tuple(approved for approved in self.approved if approved != actor_id)


__all__ = ["Agreement", "AgreementParticipant", "AgreementResolveHandler"]
