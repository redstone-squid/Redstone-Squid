"""The semantic review workspace for creator credit claims."""

from collections.abc import Awaitable, Callable, Sequence
from typing import override

import squid_ui as sl
import squid_ui_discord as sd
from squid.accounts.application import AccountService
from squid.accounts.errors import AliasAlreadyClaimedError
from squid.accounts.models import AliasClaim
from squid.bot.consent import with_consented_account
from squid.bot.profile_render import present_claimant
from squid.bot.ui import DISCORD_BLUE, tr
from squid.permissions.domain import PermissionNode
from squid.permissions.domain.catalogue import ACCOUNT_CLAIM_APPROVE, ACCOUNT_CLAIM_REJECT

REVIEW_SECONDS = 300
CLAIMS_PER_PAGE = 5
type ClaimAuthorizer = Callable[[PermissionNode], Awaitable[bool]]


class ClaimReviewComponent(sl.Component[sl.ComponentsV2Target, sd.Request]):
    """Review selected creator-credit claims with authorization checks before decisions.

    An approval that conflicts with an existing owner arms a second approval for
    that claim. Changing selection clears that confirmation state.
    """

    selected_id: int | None = sl.state(None)
    reassign_armed: int | None = sl.state(None)
    # Pending claims as last fetched from the account service. A decision reloads them inside
    # its action, so they must be state to roll back with it and to trigger the repaint.
    _claims: tuple[AliasClaim, ...] = sl.state((), persist=False)

    def __init__(
        self,
        accounts: AccountService,
        claims: Sequence[AliasClaim],
        *,
        author_id: int,
        can_approve: bool,
        can_reject: bool,
        authorize: ClaimAuthorizer,
        timeout: float = REVIEW_SECONDS,
    ) -> None:
        self._accounts = accounts
        self._claims = tuple(claims)
        self._author_id = author_id
        self._can_approve = can_approve
        self._can_reject = can_reject
        self._authorize = authorize
        self._timeout = timeout

    @property
    def claims(self) -> tuple[AliasClaim, ...]:
        return self._claims

    @property
    def selected(self) -> AliasClaim | None:
        return next((claim for claim in self._claims if claim.id == self.selected_id), None)

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        entries = tuple(_claim_entry(claim) for claim in self._claims)
        body: sl.LayoutNode[sl.ComponentsV2Target, sd.Request] = (
            sd.v2.lines(
                *entries,
                join="\n\n",
                overflow=sl.primitives.Paginate(key="claims", footer=self._page_footer),
            )
            if entries
            else sd.v2.text(tr("No creator credit claims are awaiting review."))
        )
        choices: sl.LayoutNode[sl.ComponentsV2Target, sd.Request] | None = None
        if self._claims:
            choices = sl.choices(
                *(
                    sl.choice(
                        _claim_label(claim),
                        key=str(claim.id),
                        description=present_claimant(claim, mention=False),
                    )
                    for claim in self._claims
                ),
                key="claim",
                selection=sl.controlled(
                    (str(self.selected_id),) if self.selected_id is not None else (), self._select_claim
                ),
                minimum=1,
                maximum=1,
            )
        buttons: list[sl.semantic.ActionControl[sd.Request]] = []
        if self._can_approve:
            buttons.append(
                sl.action_control(
                    tr("Take the name")
                    if self.selected_id is not None and self.reassign_armed == self.selected_id
                    else tr("Approve"),
                    self._approve,
                    key="approve",
                    tone=sl.Tone.DANGER if self.reassign_armed == self.selected_id else sl.Tone.SUCCESS,
                    available=self.selected is not None,
                )
            )
        if self._can_reject:
            buttons.append(
                sl.action_control(
                    tr("Reject"),
                    self._reject,
                    key="reject",
                    available=self.selected is not None,
                )
            )
        return (
            sd.v2.panel(
                sd.v2.heading(tr("Creator credit claims awaiting review")),
                body,
                choices,
                accent=DISCORD_BLUE,
            ),
            sl.action_controls(*buttons, key="claim-actions"),
        )

    def _page_footer(self, page: int, pages: int) -> sl.text.Message:
        total = len(self._claims)
        return tr(t"Page {page} of {pages} — {total} in total")

    async def _select_claim(self, event: sd.ChoiceEvent) -> None:
        self.selected_id = int(event.selected[0])
        self.reassign_armed = None

    async def _approve(self, event: sd.PressEvent) -> None:
        await self._decide(event, approve=True)

    async def _reject(self, event: sd.PressEvent) -> None:
        await self._decide(event, approve=False)

    async def _decide(self, event: sd.PressEvent, *, approve: bool) -> None:
        claim = self.selected
        if claim is None:
            return
        node = ACCOUNT_CLAIM_APPROVE if approve else ACCOUNT_CLAIM_REJECT
        if not await self._authorize(node):
            await event.notice(tr("You are no longer allowed to resolve creator claims."))
            return

        async def resolve(live: sd.ActionEvent, staff_account_id: int) -> None:
            await self._resolve(live, claim, staff_account_id, approve=approve)

        await with_consented_account(event, self._accounts, resolve)

    async def _resolve(
        self,
        event: sl.ActionEvent,
        claim: AliasClaim,
        staff_account_id: int,
        *,
        approve: bool,
    ) -> None:
        """Resolve the selected claim or arm explicit reassignment after an ownership conflict."""
        try:
            if approve:
                resolved = await self._accounts.approve_alias_claim(
                    claim.id,
                    staff_account_id=staff_account_id,
                    reassign=self.reassign_armed == claim.id,
                )
            else:
                resolved = await self._accounts.reject_alias_claim(claim.id, staff_account_id=staff_account_id)
        except AliasAlreadyClaimedError as conflict:
            self.reassign_armed = claim.id
            await event.notice(_conflict_text(conflict), visibility=sl.interactions.Visibility.PUBLIC)
            return
        await self._reload()
        name = resolved.alias_name
        claimant = sl.md(present_claimant(resolved))
        message = (
            tr(t"Credited **{name}** to {claimant}.")
            if approve
            else tr(t"Closed {claimant}'s claim on **{name}** without crediting it.")
        )
        await event.notice(message, visibility=sl.interactions.Visibility.PUBLIC)

    async def _reload(self) -> None:
        self._claims = tuple(await self._accounts.pending_alias_claims(with_claimants=True))
        self.selected_id = None
        self.reassign_armed = None


def _claim_entry(claim: AliasClaim) -> sl.TextLike:
    claim_id = claim.id
    name = claim.alias_name
    heading = tr(t"Claim #{claim_id} — {name}")
    claimant = sl.md(present_claimant(claim))
    age = sl.md(t"{sl.timestamp(claim.created_at.to_stdlib(), style=sl.semantic.TimeStyle.RELATIVE)}")
    detail = tr(t"{claimant} — opened {age}")
    return tr(t"**{heading}**\n{detail}")


def _claim_label(claim: AliasClaim) -> sl.text.Message:
    claim_id = claim.id
    name = claim.alias_name
    return tr(t"Claim #{claim_id} — {name}")


def _conflict_text(conflict: AliasAlreadyClaimedError) -> sl.TextLike:
    """Explain the second deliberate approval click."""
    return conflict.message + " " + tr("Approving again takes the name from them.")
