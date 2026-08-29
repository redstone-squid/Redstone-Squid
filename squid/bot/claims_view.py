"""The semantic review workspace for creator credit claims."""

from collections.abc import Awaitable, Callable, Sequence
from typing import cast

import squid_ui as sl
from squid.accounts.application import AccountService
from squid.accounts.domain import AliasClaim, IdentityProvider
from squid.accounts.errors import AliasAlreadyClaimedError
from squid.bot.consent import with_consented_account
from squid.bot.ui import DISCORD_BLUE, L
from squid.permissions.domain import PermissionNode
from squid.permissions.domain.catalogue import ACCOUNT_CLAIM_APPROVE, ACCOUNT_CLAIM_REJECT

REVIEW_SECONDS = 300
CLAIMS_PER_PAGE = 5
type ClaimAuthorizer = Callable[[PermissionNode], Awaitable[bool]]


class ClaimReviewComponent(sl.Component[sl.ComponentsV2Target]):
    """A mounted claim queue whose choices and decisions share one semantic surface."""

    selected_id: int | None = sl.state(None)
    reassign_armed: int | None = sl.state(None)
    closed: bool = sl.state(default=False)

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

    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        if self.closed:
            return (
                sl.section(
                    sl.heading(L(t"Claims closed")),
                    sl.paragraph(L(t"This review queue is closed.")),
                ),
            )
        entries = tuple(_claim_entry(claim) for claim in self._claims)
        body: sl.primitives.Node = (
            sl.primitives.Lines(
                entries,
                join="\n\n",
                overflow=sl.primitives.Paginate(key="claims", footer=self._page_footer),
            )
            if entries
            else sl.primitives.Text(L(t"No creator credit claims are awaiting review."))
        )
        choices: sl.primitives.Node | None = None
        if self._claims:
            choices = cast(
                sl.primitives.Node,
                sl.choices(
                    *(
                        sl.choice(
                            _claim_label(claim),
                            key=str(claim.id),
                            description=_claimant(claim, mention=False),
                        )
                        for claim in self._claims
                    ),
                    key="claim",
                    selection=sl.controlled(
                        (str(self.selected_id),) if self.selected_id is not None else (), self._select_claim
                    ),
                    minimum=1,
                    maximum=1,
                ),
            )
        buttons: list[sl.semantic.ActionControl] = []
        if self._can_approve:
            buttons.append(
                sl.action_control(
                    L(t"Take the name")
                    if self.selected_id is not None and self.reassign_armed == self.selected_id
                    else L(t"Approve"),
                    self._approve,
                    key="approve",
                    tone=sl.Tone.DANGER if self.reassign_armed == self.selected_id else sl.Tone.SUCCESS,
                    available=self.selected is not None,
                )
            )
        if self._can_reject:
            buttons.append(
                sl.action_control(
                    L(t"Reject"),
                    self._reject,
                    key="reject",
                    available=self.selected is not None,
                )
            )
        buttons.append(sl.action_control(L(t"Close"), self._close, key="close"))
        return (
            sl.primitives.Panel(
                (
                    sl.primitives.Heading(L(t"Creator credit claims awaiting review")),
                    body,
                    *((choices,) if choices is not None else ()),
                ),
                accent=DISCORD_BLUE,
            ),
            sl.action_controls(*buttons, key="claim-actions"),
        )

    def _page_footer(self, page: int, pages: int) -> sl.text.Message:
        total = len(self._claims)
        return L(t"Page {page} of {pages} — {total} in total")

    async def _select_claim(self, event: sl.ChoiceEvent) -> None:
        self.selected_id = int(event.selected[0])
        self.reassign_armed = None

    async def _approve(self, event: sl.PressEvent) -> None:
        await self._decide(event, approve=True)

    async def _reject(self, event: sl.PressEvent) -> None:
        await self._decide(event, approve=False)

    async def _decide(self, event: sl.PressEvent, *, approve: bool) -> None:
        claim = self.selected
        if claim is None:
            return
        node = ACCOUNT_CLAIM_APPROVE if approve else ACCOUNT_CLAIM_REJECT
        if not await self._authorize(node):
            await event.notice(L(t"You are no longer allowed to resolve creator claims."))
            return

        async def resolve(live: sl.ActionEvent, staff_account_id: int) -> None:
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
        claimant = _claimant(resolved)
        message = (
            L(t"Credited **{name}** to {claimant}.")
            if approve
            else L(t"Closed {claimant}'s claim on **{name}** without crediting it.")
        )
        await event.notice(message, visibility=sl.interactions.Visibility.PUBLIC)

    async def _reload(self) -> None:
        self._claims = tuple(await self._accounts.pending_alias_claims(with_claimants=True))
        self.selected_id = None
        self.reassign_armed = None

    async def _close(self, event: sl.PressEvent) -> None:
        self.closed = True
        await event.finish()


def _claimant(claim: AliasClaim, *, mention: bool = True) -> sl.TextLike:
    claimant = claim.claimant
    if claimant is not None:
        discord = claimant.identity(IdentityProvider.DISCORD)
        if mention and discord is not None and discord.discord_id is not None:
            return sl.md(t"<@{discord.discord_id}>")
        java = claimant.identity(IdentityProvider.JAVA)
        if java is not None and java.display_name is not None:
            return java.display_name
        if claimant.public_creator_id is not None:
            creator_id = claimant.public_creator_id
            return L(t"creator `{creator_id}`")
        if discord is not None and discord.discord_id is not None:
            discord_id = discord.discord_id
            return L(t"Discord user `{discord_id}`")
    account_id = claim.account_id
    return L(t"unidentified account (internal ID `{account_id}`)")


def _claim_entry(claim: AliasClaim) -> sl.TextLike:
    claim_id = claim.id
    name = claim.alias_name
    heading = L(t"Claim #{claim_id} — {name}")
    claimant = _claimant(claim)
    age = sl.md(t"{sl.timestamp(claim.created_at.to_stdlib(), style=sl.semantic.TimeStyle.RELATIVE)}")
    detail = L(t"{claimant} — opened {age}")
    return L(t"**{heading}**\n{detail}")


def _claim_label(claim: AliasClaim) -> sl.text.Message:
    claim_id = claim.id
    name = claim.alias_name
    return L(t"Claim #{claim_id} — {name}")


def _conflict_text(conflict: AliasAlreadyClaimedError) -> sl.TextLike:
    """Explain the second deliberate approval click."""
    held = L(conflict.message, **conflict.message_params)
    action = L(t"Approving again takes the name from them.")
    return L(t"{held} {action}")
