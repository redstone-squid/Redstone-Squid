"""Canonical account workflow combining identity, consent, claims, and merge operations."""

from collections.abc import Awaitable, Callable
from typing import cast

import discord

import squid_ui as sl
import squid_ui_discord as sd
import squid_ui_widgets as sp
from squid.accounts.application import AccountService
from squid.accounts.domain import Account, AccountConsent, IdentityProvider
from squid.accounts.errors import AccountAlreadyLinkedError, AccountNotFoundError
from squid.bot.account_presentation import link_conflict, link_message, refresh_message
from squid.bot.account_view import AccountScreen, ConsentRequest
from squid.bot.claims_view import ClaimReviewComponent
from squid.bot.consent import CONSENT_PROMPT_TIMEOUT_SECONDS
from squid.bot.ui import tr
from squid.permissions.domain import PermissionNode
from squid.permissions.domain.catalogue import ACCOUNT_IDENTITY_REFRESH, ACCOUNT_IDENTITY_REFRESH_ANY
from squid_ui_discord.modal import EntityField

type ClaimAuthorizer = Callable[[PermissionNode], Awaitable[bool]]


class AccountWorkspace(sd.Screen):
    """An account workspace that ends when closed, replaced, or timed out."""

    session = sd.SessionSpec("account")
    timeout = 300
    audience = "personal"

    def __init__(
        self,
        *,
        accounts: AccountService,
        actor_id: int,
        account: Account | None,
        request_consent: ConsentRequest,
        can_review_claims: bool,
        can_approve_claims: bool,
        can_reject_claims: bool,
        authorize_claim: ClaimAuthorizer,
        can_refresh_any: bool = False,
        can_refresh_identity: bool = False,
    ) -> None:
        self._accounts = accounts
        self._actor_id = actor_id
        self._account = account
        self._request_consent = request_consent
        self._can_review_claims = can_review_claims
        self._can_approve_claims = can_approve_claims
        self._can_reject_claims = can_reject_claims
        self._authorize_claim = authorize_claim
        self._can_refresh_any = can_refresh_any
        self._can_refresh_identity = can_refresh_identity
        self._overview: AccountScreen | None = None
        self._claims: ClaimReviewComponent | None = None
        self._tabs: sp.ComponentDriver[sp.TabsState, sl.ComponentsV2Target] | None = None
        self._merge_code: str | None = None
        self._merge_decision: sp.ComponentDriver[sp.DecisionState, sl.ComponentsV2Target] | None = None

    async def on_load(self) -> None:
        await self._rebuild()

    async def _rebuild(self) -> None:
        self._account = await self._accounts.get_account_by_identity(IdentityProvider.DISCORD, str(self._actor_id))
        account_id = None if self._account is None else self._account.id
        self._overview = None
        if account_id is not None:
            self._overview = AccountScreen(
                accounts=self._accounts,
                account_id=account_id,
                actor_id=self._actor_id,
                request_consent=self._request_consent,
            )
            await self._overview.on_load()
        self._claims = None
        if self._can_review_claims:
            claims = await self._accounts.pending_alias_claims(with_claimants=True)
            self._claims = ClaimReviewComponent(
                self._accounts,
                claims,
                author_id=self._actor_id,
                can_approve=self._can_approve_claims,
                can_reject=self._can_reject_claims,
                authorize=self._authorize_claim,
            )
        tabs: list[sp.Tab[sl.ComponentsV2Target]] = []
        if self._overview is not None:
            tabs.append(sp.Tab("overview", tr(t"Overview"), self._overview))
        tabs.append(sp.Tab("identity", tr(t"Link and refresh"), self._identity_nodes()))
        if account_id is not None:
            tabs.append(sp.Tab("claims", tr(t"Creator claims"), self._claim_nodes()))
            tabs.append(sp.Tab("merge", tr(t"Merge accounts"), self._merge_nodes()))
        if self._claims is not None:
            tabs.append(sp.Tab("review", tr(t"Review claims"), self._claims))
        self._tabs = sp.Tabs(tabs, key="account-tabs", title=tr(t"Account")).build_component()

    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        if self._merge_code is not None and self._merge_decision is not None:
            return (self.boundary(self._merge_decision, key="merge-decision"),)
        if self._tabs is None:
            return (sl.status(tr(t"Loading account.")),)
        return (
            self.boundary(self._tabs, key="tabs"),
            sl.action_controls(sl.action_control(tr(t"Close"), self._close, key="close"), key="account-actions"),
        )

    def _identity_nodes(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        account = self._account
        nodes: list[sl.LayoutNode[sl.ComponentsV2Target]] = []
        if account is None or account.needs_consent_refresh:
            nodes.append(
                sl.action_controls(
                    sl.action_control(tr(t"Review privacy notice"), self._consent, key="consent"),
                    key="consent-actions",
                )
            )
        nodes.append(
            sl.form(
                tr(t"Link Minecraft account"),
                sl.forms.FormSpec(
                    tr(t"Link Minecraft account"),
                    (sl.forms.TextField(key="code", label=tr(t"In-game link code"), maximum=100),),
                ),
                key="link",
                on_submit=self._link,
            )
        )
        if (
            account is not None
            and account.id is not None
            and not account.needs_consent_refresh
            and self._can_refresh_identity
        ):
            nodes.append(
                sl.action_controls(
                    sl.action_control(tr(t"Refresh Minecraft identity"), self._refresh_identity, key="refresh"),
                    key="refresh-actions",
                )
            )
        if self._can_refresh_any:
            nodes.append(
                sl.form(
                    tr(t"Refresh another member's Minecraft identity"),
                    sl.forms.FormSpec(
                        tr(t"Refresh member identity"),
                        (EntityField(key="user", label=tr(t"Discord member")),),
                    ),
                    key="staff-refresh",
                    on_submit=self._refresh_member_identity,
                )
            )
        return tuple(nodes)

    def _claim_nodes(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        return (
            sl.form(
                tr(t"Claim creator credit"),
                sl.forms.FormSpec(
                    tr(t"Claim an older creator name"),
                    (sl.forms.TextField(key="name", label=tr(t"Creator name"), maximum=100),),
                ),
                key="claim",
                on_submit=self._claim,
            ),
        )

    def _merge_nodes(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        return (
            sl.action_controls(
                sl.action_control(tr(t"Create merge code"), self._create_merge_code, key="merge-code"),
                key="merge-code-actions",
            ),
            sl.form(
                tr(t"Merge another account into this one"),
                sl.forms.FormSpec(
                    tr(t"Preview account merge"),
                    (sl.forms.TextField(key="code", label=tr(t"Merge code"), maximum=100),),
                ),
                key="merge",
                on_submit=self._request_merge,
            ),
        )

    async def _consent(self, event: sl.PressEvent) -> None:
        async def answered(_prompt: sl.PressEvent, consent: AccountConsent | None) -> None:
            if consent is None:
                return
            account = self._account
            if account is None or account.id is None:
                await self._accounts.get_or_create_identity(
                    IdentityProvider.DISCORD,
                    str(self._actor_id),
                    consent=consent,
                )
            else:
                await self._accounts.grant_current_consent(account.id)
            await self._rebuild()

        await self._request_consent(event, answered)

    async def _link(self, event: sl.SubmitEvent) -> None:
        account = self._account
        code = cast(str, event.values["code"])
        attempted_by = (IdentityProvider.DISCORD, str(self._actor_id))
        reservation = await self._accounts.reserve_minecraft_link(
            code,
            attempted_by=attempted_by,
            ttl_seconds=int(CONSENT_PROMPT_TIMEOUT_SECONDS),
        )
        conflict = (
            None
            if account is None
            else link_conflict(reservation.preview, account.identities_for(IdentityProvider.JAVA))
        )
        if conflict is not None:
            await self._accounts.release_minecraft_link(code, reservation)
            raise AccountAlreadyLinkedError(
                account_id=account.id if account is not None else None, minecraft_uuid=conflict
            )

        settled = False

        async def release() -> None:
            nonlocal settled
            if settled:
                return
            settled = True
            await self._accounts.release_minecraft_link(code, reservation)

        async def consented_account_id(consent: AccountConsent) -> int:
            linked_account = account
            if linked_account is None or linked_account.id is None:
                linked_account = await self._accounts.get_or_create_identity(
                    IdentityProvider.DISCORD,
                    str(self._actor_id),
                    consent=consent,
                )
            elif linked_account.consent is None or linked_account.needs_consent_refresh:
                linked_account = await self._accounts.grant_current_consent(linked_account.id)
            account_id = linked_account.id
            if account_id is None:
                raise AccountNotFoundError(provider=IdentityProvider.DISCORD, subject=str(self._actor_id))
            return account_id

        async def answered(prompt: sl.PressEvent, consent: AccountConsent | None) -> None:
            nonlocal settled
            if consent is None:
                await release()
                return
            try:
                linked_account_id = await consented_account_id(consent)
                refresh = await self._accounts.link_minecraft_account(
                    linked_account_id,
                    code,
                    consent=consent,
                    attempted_by=attempted_by,
                    reservation=reservation,
                )
            except Exception:
                await release()
                raise
            settled = True
            await self._rebuild()
            await prompt.notice(link_message(refresh))

        opened = await self._request_consent(
            event,
            answered,
            preview=reservation.preview,
            on_abandon=release,
            timeout=CONSENT_PROMPT_TIMEOUT_SECONDS,
        )
        if not opened:
            await release()

    async def _refresh_identity(self, event: sl.PressEvent) -> None:
        if not await self._authorize_claim(ACCOUNT_IDENTITY_REFRESH):
            await event.notice(tr(t"You are no longer allowed to refresh your Minecraft identity."))
            return
        account_id = self._account_id()
        refresh = await self._accounts.refresh_java_identity(account_id)
        await self._rebuild()
        await event.notice(refresh_message(refresh))

    async def _refresh_member_identity(self, event: sl.SubmitEvent) -> None:
        if not await self._authorize_claim(ACCOUNT_IDENTITY_REFRESH_ANY):
            await event.notice(tr(t"You are no longer allowed to refresh another member's identity."))
            return
        member = cast(discord.Member | discord.User, event.values["user"])
        account = await self._accounts.get_account_by_identity(IdentityProvider.DISCORD, str(member.id))
        if account is None or account.id is None:
            raise AccountNotFoundError(provider=IdentityProvider.DISCORD, subject=str(member.id))
        refresh = await self._accounts.refresh_java_identity(account.id)
        await self._rebuild()
        await event.notice(refresh_message(refresh))

    async def _claim(self, event: sl.SubmitEvent) -> None:
        claim = await self._accounts.request_alias_claim(self._account_id(), cast(str, event.values["name"]))
        claim_id = claim.id
        alias_name = claim.alias_name
        await event.notice(tr(t"Claim #{claim_id} for **{alias_name}** is awaiting staff approval."))

    async def _create_merge_code(self, event: sl.PressEvent) -> None:
        code, ticket = await self._accounts.create_merge_code(self._account_id())
        expiry = ticket.expires_at.to_stdlib().isoformat()
        await event.notice(
            tr(t"Merge code: `{code}`. It expires {expiry}. Keep it private: it hands this account over.")
        )

    async def _request_merge(self, event: sl.SubmitEvent) -> None:
        code = cast(str, event.values["code"])
        preview = await self._accounts.preview_merge(self._account_id(), code)
        aliases = len(preview.alias_names)
        identities = preview.identity_count
        builds = preview.build_count
        self._merge_code = code
        self._merge_decision = sp.Decision[sl.ComponentsV2Target](
            tr(
                t"Move {aliases} creator names, {identities} identities, and {builds} build credits here? "
                t"This cannot be undone."
            ),
            (
                sp.DecisionOption("confirm", tr(t"Merge accounts"), sl.Tone.DANGER),
                sp.DecisionOption("cancel", tr(t"Cancel")),
            ),
            key="merge-account",
        ).build_component(on_decide=self._finish_merge)

    async def _finish_merge(self, event: sp.TransitionEvent[sp.DecisionState], choice: str) -> None:
        code = self._merge_code
        if code is None or choice == "cancel":
            self._merge_code = None
            self._merge_decision = None
            return
        result = await self._accounts.complete_merge(self._account_id(), code)
        redirected = result.redirected_public_creator_id
        self._merge_code = None
        self._merge_decision = None
        await self._rebuild()
        await event.source.notice(tr(t"Merged. `{redirected}` now redirects to your creator page."))

    def _account_id(self) -> int:
        account = self._account
        if account is None or account.id is None:
            message = "account workflow requires a persisted account"
            raise RuntimeError(message)
        return account.id

    async def _close(self, event: sl.PressEvent) -> None:
        await event.finish()
