"""The review queue behind `/account claims`.

Reviewing a creator credit claim used to take three commands: `claims` printed the queue, and
`approve-claim` and `reject-claim` each took a claim id you read off that queue and typed back —
into an autocomplete that offered you the very list you were reading (audit C5's retyping half).
The queue is where the decision is made, so the decision lives on the queue.

The controls a reviewer holds are the controls they are shown, and every click is checked again
when it arrives: `hide_unless` and `requires` split the same way at the command level, because a
control you cannot use should not be offered and a control you can see is still not a gate.
"""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, cast, override

import discord

import squid_layouts as sl
from squid.accounts.application import AccountService
from squid.accounts.domain import AliasClaim
from squid.accounts.errors import AliasAlreadyClaimedError
from squid.bot.consent import ensure_consented_account
from squid.bot.i18n import t
from squid.bot.profile_render import present_claimant
from squid.bot.ui import create_mount
from squid.bot.utils.components import DISCORD_BLUE, edit_interaction_layout, reply_layout, text_layout
from squid.bot.utils.pagination import ListPaginator
from squid.bot.utils.permissions import enforce
from squid.core.i18n import _
from squid.permissions.domain.catalogue import ACCOUNT_CLAIM_APPROVE, ACCOUNT_CLAIM_REJECT

if TYPE_CHECKING:
    import squid.bot.app

REVIEW_SECONDS = 300

CLAIMS_PER_PAGE = 5
"""Claims per page. Fewer than the paginator's default because each one is two lines and the
select under the card has to offer every claim the page shows."""


class ClaimReviewView(ListPaginator):
    """The pending claims, and the two decisions a reviewer can make about one of them.

    Holds the service rather than a snapshot, like the settings and notification panels: every
    decision changes the queue the panel is showing, so the panel has to be able to re-read it.
    """

    def __init__(
        self,
        accounts: AccountService,
        claims: Sequence[AliasClaim],
        *,
        author_id: int,
        locale: str | None = None,
        can_approve: bool,
        can_reject: bool,
        timeout: float = REVIEW_SECONDS,
    ) -> None:
        # Set before `super().__init__`, which renders, and rendering reads all of them.
        self._accounts = accounts
        self._claims = tuple(claims)
        self._can_approve = can_approve
        self._can_reject = can_reject
        self._selected_id: int | None = None
        self._reassign_armed: int | None = None
        super().__init__(
            t(locale, _("Creator credit claims awaiting review")),
            [_claim_entry(claim, locale) for claim in claims],
            author_id=author_id,
            empty=t(locale, _("No creator credit claims are awaiting review.")),
            locale=locale,
            page_size=CLAIMS_PER_PAGE,
            accent_colour=DISCORD_BLUE,
            timeout=timeout,
        )

    @property
    def page_claims(self) -> tuple[AliasClaim, ...]:
        """The claims the current page lists, which are the ones the select offers."""
        start = self.page * self.page_size
        return self._claims[start : start + self.page_size]

    @property
    def selected_id(self) -> int | None:
        """Which claim the select shows as picked."""
        return self._selected_id

    @property
    def selected(self) -> AliasClaim | None:
        """The claim the buttons act on, if one is picked and still pending."""
        return next((claim for claim in self._claims if claim.id == self._selected_id), None)

    @property
    def reassign_armed(self) -> bool:
        """Whether approving again would take the name from whoever holds it now."""
        return self._reassign_armed is not None and self._reassign_armed == self._selected_id

    @override
    def render(self) -> None:
        super().render()
        if not self._claims:
            return
        self.add_item(discord.ui.ActionRow(ClaimSelect(self)))
        controls: list[discord.ui.Button[Any]] = []
        if self._can_approve:
            controls.append(ApproveClaimButton(self))
        if self._can_reject:
            controls.append(RejectClaimButton(self))
        if controls:
            self.add_item(discord.ui.ActionRow(*controls))

    @override
    async def go_to(self, interaction: discord.Interaction[Any], page: int) -> None:
        # A selection the page no longer shows would act on a claim the reviewer cannot see.
        self.select(None)
        await super().go_to(interaction, page)

    def select(self, claim_id: int | None) -> None:
        """Point the buttons at a claim, disarming a transfer aimed at a different one."""
        if claim_id != self._selected_id:
            self._reassign_armed = None
        self._selected_id = claim_id
        self.render()

    async def approve(self, interaction: discord.Interaction[squid.bot.app.RedstoneSquid]) -> None:
        """Credit the selected claim, arming a transfer if the name is held elsewhere."""
        await enforce(interaction, ACCOUNT_CLAIM_APPROVE)
        claim = self.selected
        if claim is None:
            return
        reassign = self.reassign_armed
        staff_account_id = await self._reviewer(interaction)
        if staff_account_id is None:
            return
        try:
            resolved = await self._accounts.approve_alias_claim(
                claim.id, staff_account_id=staff_account_id, reassign=reassign
            )
        except AliasAlreadyClaimedError as conflict:
            # A transfer takes a second, deliberate click rather than a flag nobody read: the
            # first one is now the question, and the button says what the second one would do.
            self._reassign_armed = claim.id
            await self._redraw(interaction)
            await reply_layout(interaction, text_layout(_conflict_text(conflict, self.locale)))
            return
        await self._reload(interaction)
        await self._announce(
            interaction,
            t(
                self.locale,
                _("Credited **{name}** to {claimant}."),
                name=resolved.alias_name,
                claimant=present_claimant(resolved, self.locale),
            ),
        )

    async def reject(self, interaction: discord.Interaction[squid.bot.app.RedstoneSquid]) -> None:
        """Close the selected claim, leaving the name credited as it is."""
        await enforce(interaction, ACCOUNT_CLAIM_REJECT)
        claim = self.selected
        if claim is None:
            return
        staff_account_id = await self._reviewer(interaction)
        if staff_account_id is None:
            return
        resolved = await self._accounts.reject_alias_claim(claim.id, staff_account_id=staff_account_id)
        await self._reload(interaction)
        await self._announce(
            interaction,
            t(
                self.locale,
                _("Closed {claimant}'s claim on **{name}** without crediting it."),
                name=resolved.alias_name,
                claimant=present_claimant(resolved, self.locale),
            ),
        )

    async def _reviewer(self, interaction: discord.Interaction[squid.bot.app.RedstoneSquid]) -> int | None:
        """The reviewer's account id, which resolving a claim records against it.

        Asked for here rather than when the queue is opened: reading the queue stores nothing, and
        nobody should gain an account row for looking at work they then leave to somebody else.
        The deferral is what lets the consent prompt and the panel redraw share one interaction.
        """
        await interaction.response.defer()
        return await ensure_consented_account(interaction, self._accounts, locale=self.locale)

    async def _reload(self, interaction: discord.Interaction[Any]) -> None:
        """Re-read the queue and redraw it, since a resolved claim has left it."""
        self._claims = tuple(await self._accounts.pending_alias_claims(with_claimants=True))
        self.entries = [_claim_entry(claim, self.locale) for claim in self._claims]
        self._selected_id = None
        self._reassign_armed = None
        self.page = min(self.page, self.page_count - 1)
        await self._redraw(interaction)

    async def _redraw(self, interaction: discord.Interaction[Any]) -> None:
        self.render()
        await edit_interaction_layout(interaction, self)

    async def _announce(self, interaction: discord.Interaction[Any], message: str) -> None:
        """Say what was decided where the channel can see it.

        A credit is a change to shared state — whose name is on which builds — so the decision
        leaves a public artifact, exactly as the two commands this panel replaced did. The panel
        itself stays private, because a review queue is a staff read.
        """
        await reply_layout(interaction, text_layout(message), ephemeral=False)


class ClaimReviewComponent(sl.Component):
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
        locale: str | None = None,
        can_approve: bool,
        can_reject: bool,
        timeout: float = REVIEW_SECONDS,
    ) -> None:
        self._accounts = accounts
        self._claims = tuple(claims)
        self._author_id = author_id
        self.locale = locale
        self._can_approve = can_approve
        self._can_reject = can_reject
        self._timeout = timeout
        self._compat_mount: sl.discord.Mount | None = None

    @property
    def claims(self) -> tuple[AliasClaim, ...]:
        return self._claims

    @property
    def selected(self) -> AliasClaim | None:
        return next((claim for claim in self._claims if claim.id == self.selected_id), None)

    def render(self) -> Sequence[sl.LayoutNode]:
        if self.closed:
            return [
                sl.primitives.card(
                    t(self.locale, _("Claims closed")),
                    t(self.locale, _("This review queue is closed.")),
                    accent=DISCORD_BLUE,
                )
            ]
        entries = tuple(_claim_entry(claim, self.locale) for claim in self._claims)
        body: sl.LayoutNode = (
            sl.primitives.Lines(
                entries,
                join="\n\n",
                overflow=sl.primitives.Paginate(key="claims", footer=self._page_footer),
            )
            if entries
            else sl.primitives.Text(t(self.locale, _("No creator credit claims are awaiting review.")))
        )
        choices: sl.LayoutNode | None = None
        if self._claims:
            choices = sl.Choices(
                key="claim",
                choices=tuple(
                    sl.Choice(
                        str(claim.id),
                        t(self.locale, _("Claim #{id} — {name}"), id=claim.id, name=claim.alias_name),
                        present_claimant(claim, self.locale, mention=False),
                    )
                    for claim in self._claims
                ),
                selected=(str(self.selected_id),) if self.selected_id is not None else (),
                on_change=self._select_claim,
                minimum=1,
                maximum=1,
            )
        buttons: list[sl.primitives.Button] = []
        if self._can_approve:
            buttons.append(
                sl.primitives.Button(
                    t(self.locale, _("Take the name"))
                    if self.reassign_armed == self.selected_id
                    else t(self.locale, _("Approve")),
                    self._approve,
                    "approve",
                    style=sl.primitives.ActionStyle.DANGER
                    if self.reassign_armed == self.selected_id
                    else sl.primitives.ActionStyle.SUCCESS,
                    disabled=self.selected is None,
                )
            )
        if self._can_reject:
            buttons.append(
                sl.primitives.Button(
                    t(self.locale, _("Reject")),
                    self._reject,
                    "reject",
                    style=sl.primitives.ActionStyle.SECONDARY,
                    disabled=self.selected is None,
                )
            )
        buttons.append(
            sl.primitives.Button(
                t(self.locale, _("Close")),
                self._close,
                "close",
                style=sl.primitives.ActionStyle.SECONDARY,
            )
        )
        return (
            sl.primitives.Panel(
                (
                    sl.primitives.Heading(t(self.locale, _("Creator credit claims awaiting review"))),
                    body,
                    *((choices,) if choices is not None else ()),
                ),
                accent=DISCORD_BLUE,
            ),
            sl.primitives.Row(tuple(buttons)),
        )

    def _page_footer(self, page: int, pages: int) -> str:
        return t(
            self.locale,
            _("Page {page} of {pages} · {total} in total"),
            page=page,
            pages=pages,
            total=len(self._claims),
        )

    async def _select_claim(self, event: sl.ChoiceEvent) -> None:
        self.selected_id = int(event.selected[0])
        self.reassign_armed = None

    async def _approve(self, event: sl.PressEvent) -> None:
        await self._decide(event, approve=True)

    async def _reject(self, event: sl.PressEvent) -> None:
        await self._decide(event, approve=False)

    async def _decide(self, event: sl.PressEvent, *, approve: bool) -> None:
        interaction = self._interaction(event)
        claim = self.selected
        if interaction is None or claim is None:
            return
        await enforce(interaction, ACCOUNT_CLAIM_APPROVE if approve else ACCOUNT_CLAIM_REJECT)
        await interaction.response.defer()
        staff_account_id = await ensure_consented_account(interaction, self._accounts, locale=self.locale)
        if staff_account_id is None:
            return
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
            await event.notice(_conflict_text(conflict, self.locale), visibility=sl.Visibility.PUBLIC)
            return
        await self._reload()
        message = (
            t(
                self.locale,
                _("Credited **{name}** to {claimant}."),
                name=resolved.alias_name,
                claimant=present_claimant(resolved, self.locale),
            )
            if approve
            else t(
                self.locale,
                _("Closed {claimant}'s claim on **{name}** without crediting it."),
                name=resolved.alias_name,
                claimant=present_claimant(resolved, self.locale),
            )
        )
        await event.notice(message, visibility=sl.Visibility.PUBLIC)

    async def _reload(self) -> None:
        self._claims = tuple(await self._accounts.pending_alias_claims(with_claimants=True))
        self.selected_id = None
        self.reassign_armed = None

    async def _close(self, event: sl.PressEvent) -> None:
        self.closed = True
        await event.finish()

    @staticmethod
    def _interaction(event: sl.ActionEvent) -> discord.Interaction[Any] | None:
        interaction = getattr(event.responder, "interaction", None)
        return cast(discord.Interaction[Any], interaction) if interaction is not None else None

    def mount(self) -> sl.discord.Mount:
        return create_mount(
            self,
            locale=self.locale,
            timeout=self._timeout,
            lock_to=self._author_id,
        )


class ClaimSelect(discord.ui.Select[ClaimReviewView]):
    """Pick the claim the buttons act on."""

    def __init__(self, view: ClaimReviewView) -> None:
        options = [
            discord.SelectOption(
                label=t(view.locale, _("Claim #{id} — {name}"), id=claim.id, name=claim.alias_name)[:100],
                value=str(claim.id),
                description=present_claimant(claim, view.locale, mention=False)[:100],
                default=claim.id == view.selected_id,
            )
            for claim in view.page_claims
        ]
        super().__init__(placeholder=t(view.locale, _("Pick a claim to review…")), options=options)
        self._panel = view

    @override
    async def callback(self, interaction: discord.Interaction[discord.Client]) -> None:
        self._panel.select(int(self.values[0]))
        await edit_interaction_layout(interaction, self._panel)


class ApproveClaimButton(discord.ui.Button[ClaimReviewView]):
    """Credit the selected claimant with the name they asked for."""

    def __init__(self, view: ClaimReviewView) -> None:
        armed = view.reassign_armed
        super().__init__(
            label=t(view.locale, _("Take the name")) if armed else t(view.locale, _("Approve")),
            style=discord.ButtonStyle.danger if armed else discord.ButtonStyle.success,
            disabled=view.selected is None,
        )
        self._panel = view

    @override
    async def callback(self, interaction: discord.Interaction[squid.bot.app.RedstoneSquid]) -> None:  # pyright: ignore [reportIncompatibleMethodOverride]  # pyrefly: ignore[bad-override]
        await self._panel.approve(interaction)


class RejectClaimButton(discord.ui.Button[ClaimReviewView]):
    """Close the selected claim without crediting it."""

    def __init__(self, view: ClaimReviewView) -> None:
        super().__init__(
            label=t(view.locale, _("Reject")),
            style=discord.ButtonStyle.secondary,
            disabled=view.selected is None,
        )
        self._panel = view

    @override
    async def callback(self, interaction: discord.Interaction[squid.bot.app.RedstoneSquid]) -> None:  # pyright: ignore [reportIncompatibleMethodOverride]  # pyrefly: ignore[bad-override]
        await self._panel.reject(interaction)


def _claim_entry(claim: AliasClaim, locale: str | None) -> str:
    heading = t(locale, _("Claim #{id} — {name}"), id=claim.id, name=claim.alias_name)
    detail = t(
        locale,
        _("{claimant} · opened {age}"),
        claimant=present_claimant(claim, locale),
        age=discord.utils.format_dt(claim.created_at.to_stdlib(), style="R"),
    )
    return f"**{heading}**\n{detail}"


def _conflict_text(conflict: AliasAlreadyClaimedError, locale: str | None) -> str:
    """Say who holds the name, and what a second click would do to them.

    The error already names the holder — the service resolves it on the error path — so this
    restates that rather than writing a second, differently-worded refusal. Its own
    `end_user_action` is dropped: it tells a claimant to ask staff, and staff are reading this.
    """
    held = t(locale, conflict.message, **conflict.message_params)
    return f"{held} {t(locale, _('Approving again takes the name from them.'))}"
