"""The semantic review workspace for creator credit claims."""

from collections.abc import Awaitable, Callable, Sequence
from functools import partial
from itertools import batched
from typing import Any, override
from uuid import UUID

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.bot.accounts.identity import with_account
from squid.bot.accounts.profile_render import present_claimant
from squid.bot.ui import tr
from squid.bot.utils.permissions import Grants
from squid.core.accounts.application import AccountService
from squid.core.accounts.errors import AliasAlreadyClaimedError
from squid.core.accounts.models import AliasClaim
from squid.core.actors import ActingContext, Actor
from squid.core.errors import AuthorizationError
from squid.core.permissions.domain.catalogue import ACCOUNT_CLAIM_APPROVE, ACCOUNT_CLAIM_REJECT
from squid_i18n import md_tr
from squid_ui.ext.discord import primitives
from squid_ui.ext.discord.target_types import ComponentsV2Target

REVIEW_SECONDS = 300
CLAIMS_PER_PAGE = 5


class ClaimReviewComponent(sl.Component[ComponentsV2Target, sd.Request]):
    """Review selected creator-credit claims with authorization checks before decisions.

    An approval that conflicts with an existing owner arms a second approval for
    that claim. Changing selection clears that confirmation state.
    """

    selected_id: int | None = sl.state(None)
    included: tuple[str, ...] | None = sl.state(None)
    reassign_armed: int | None = sl.state(None)
    # Pending claims as last fetched from the account service. A decision reloads them inside
    # its action, so they must be state to roll back with it and to trigger the repaint.
    _claims: tuple[AliasClaim, ...] = sl.state(())

    def __init__(
        self,
        accounts: AccountService,
        claims: Sequence[AliasClaim],
        *,
        actor: Callable[[], Awaitable[Actor]],
        author_id: int,
        grants: Grants,
        timeout: float = REVIEW_SECONDS,
    ) -> None:
        self._accounts = accounts
        self._actor = actor
        self._claims = tuple(claims)
        self._author_id = author_id
        self.grants = grants
        self._timeout = timeout

    @property
    def claims(self) -> tuple[AliasClaim, ...]:
        return self._claims

    @property
    def selected(self) -> AliasClaim | None:
        return next((claim for claim in self._claims if claim.id == self.selected_id), None)

    @override
    def render(self) -> tuple[sl.LayoutNode[ComponentsV2Target, sd.Request], ...]:
        total = len(self._claims)

        def page_footer(ctx: sl.LayoutContext) -> sl.TextLike:
            region = ctx.region()
            selection = region.selection.require()
            assert selection is not None
            page, pages = selection.number.require(), region.page_count.require()
            return md_tr(t"claims-view-page-of-in-total = Page {page} of {pages} — {total} in total")

        entries = tuple(_claim_entry(claim) for claim in self._claims)
        body: sl.LayoutNode[ComponentsV2Target, sd.Request] = (
            sd.v2.lines(
                *entries,
                join="\n\n",
                overflow=primitives.Paginate(key="claims", footer=page_footer),
            )
            if entries
            else sd.v2.text(tr(t"claims-review-empty = No creator credit claims are awaiting review."))
        )
        choices: sl.LayoutNode[ComponentsV2Target, sd.Request] | None = None
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
                selection=(str(self.selected_id),) if self.selected_id is not None else (),
                on_change=self._select_claim,
                minimum=1,
                maximum=1,
            )
        credits = None
        if self.selected is not None and self.selected.selected_credits:
            targets = self.selected.selected_credits
            included = set(targets if self.included is None else self.included)
            pages = [
                sl.choices(
                    *(sl.choice(f"Build #{targets[key]['build_id']}", key=key, description=key) for key in page),
                    key=f"selected-credits-{index}",
                    selection=tuple(key for key in page if key in included),
                    on_change=partial(self._select_credits, page),
                    minimum=0,
                    maximum=len(page),
                )
                for index, page in enumerate(batched(targets, 25, strict=False))
            ]
            credits = sl.region(*pages, key="credit-pages", title="", page_size=1)
        buttons: list[sl.semantic.ActionControl[sd.Request]] = []
        if ACCOUNT_CLAIM_APPROVE in self.grants:
            buttons.append(
                sl.action_control(
                    tr(t"claims-view-take-the-name = Reassign credits")
                    if self.selected_id is not None and self.reassign_armed == self.selected_id
                    else tr(t"claims-view-approve = Approve"),
                    self._approve,
                    key="approve",
                    tone=sl.Tone.DANGER if self.reassign_armed == self.selected_id else sl.Tone.SUCCESS,
                    available=self.selected is not None and self.included != (),
                )
            )
        if ACCOUNT_CLAIM_REJECT in self.grants:
            buttons.append(
                sl.action_control(
                    tr(t"claims-view-reject = Reject"),
                    self._reject,
                    key="reject",
                    available=self.selected is not None,
                )
            )
        panel = sl.block(
            sd.v2.heading(tr(t"claims-review-heading = Creator credit claims awaiting review")),
            body,
            choices,
            credits,
            accent=sl.AccentRole.BRAND,
        )
        if not self._claims:
            # With no claims there is nothing to select, so the actions could never be used.
            return (panel,)
        return (panel, sl.action_controls(*buttons, key="claim-actions"))

    async def _select_claim(self, event: sd.ChoiceEvent) -> None:
        self.included = None
        self.selected_id = int(event.selected[0])
        self.reassign_armed = None

    async def _select_credits(self, page: tuple[str, ...], event: sd.ChoiceEvent) -> None:
        assert self.selected is not None
        previous = self.selected.selected_credits if self.included is None else self.included
        self.included = tuple(key for key in previous if key not in page) + tuple(event.selected)
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
        denied = tr(t"claims-resolve-forbidden = You are no longer allowed to resolve creator claims.")
        if not await self.grants.confirm(event, node, denied=denied):
            return

        async def resolve(live: sd.ActionEvent, staff_account_id: int) -> None:
            await self._resolve(live, claim, staff_account_id, approve=approve)

        await with_account(event, self._accounts, resolve)

    async def _resolve(
        self,
        event: sl.ActionEvent[Any],
        claim: AliasClaim,
        staff_account_id: int,
        *,
        approve: bool,
    ) -> None:
        """Resolve the selected claim or arm explicit reassignment after an ownership conflict."""
        context = ActingContext.for_actor(await self._actor())
        if context.effective.require_account_id() != staff_account_id:
            raise AuthorizationError
        try:
            if approve:
                resolved = await self._accounts.approve_alias_claim(
                    claim.id,
                    reassign=self.reassign_armed == claim.id,
                    context=context,
                    credit_ids=[UUID(key) for key in self.included] if self.included is not None else None,
                )
            else:
                resolved = await self._accounts.reject_alias_claim(claim.id, context=context)
        except AliasAlreadyClaimedError as conflict:
            self.reassign_armed = claim.id
            await event.notice(_conflict_text(conflict), visibility=sl.interactions.Visibility.PUBLIC)
            return
        await self._reload()
        name = resolved.alias_name
        claimant = present_claimant(resolved)
        message = (
            md_tr(t"claims-view-credited-to = Approved the selected **{name}** credits for {claimant}.")
            if approve
            else md_tr(
                t"claims-closed-uncredited = Closed {claimant}'s claim on **{name}** without crediting it.",
            )
        )
        await event.notice(message, visibility=sl.interactions.Visibility.PUBLIC)

    async def _reload(self) -> None:
        self._claims = tuple(
            await self._accounts.pending_alias_claims(
                with_claimants=True, context=ActingContext.for_actor(await self._actor())
            )
        )
        self.included = None
        self.selected_id = None
        self.reassign_armed = None


def _claim_entry(claim: AliasClaim) -> sl.TextLike:
    claim_id = claim.id
    name = claim.alias_name
    heading = md_tr(t"claims-view-claim = Claim #{claim_id!s} — {name}")
    claimant = present_claimant(claim)
    age = sl.text.Text(t"{sl.timestamp(claim.created_at.to_stdlib(), style=sl.semantic.TimeStyle.RELATIVE)}")
    detail = md_tr(t"claims-view-opened = {claimant} — opened {age}")
    return md_tr(
        t"""
        bot-claims-view-value =
            {{ "*" }}*{heading}**
            {detail}
        """,
    )


def _claim_label(claim: AliasClaim) -> sl.text.Text:
    claim_id = claim.id
    name = claim.alias_name
    return md_tr(t"claims-view-claim = Claim #{claim_id!s} — {name}")


def _conflict_text(conflict: AliasAlreadyClaimedError) -> sl.TextLike:
    """Explain the second deliberate approval click."""
    warning = tr(t"claims-reapproval-warning = Approving again reassigns the selected credits from them.")
    return sl.text.Text(t"{conflict.message} {warning}")
