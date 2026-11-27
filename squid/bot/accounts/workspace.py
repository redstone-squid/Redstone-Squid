"""Canonical account workflow combining identity, consent, claims, and merge operations."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any, Literal, Self, cast, override

import anyio
import discord

import squid_ui as sl
import squid_ui.ext.discord as sd
import squid_ui_widgets as sp
from squid.bot.accounts.claims import ClaimReviewComponent
from squid.bot.accounts.link_confirmation import LINK_CONFIRMATION_TIMEOUT_SECONDS, request_link_confirmation
from squid.bot.accounts.overview import AccountOverview, LinkConfirmationAnswer, LinkConfirmationRequest
from squid.bot.accounts.presentation import link_conflict, link_message, refresh_message
from squid.bot.app import RedstoneSquid
from squid.bot.catalogue.browse import BuildBrowseScreen
from squid.bot.lookup import pick
from squid.bot.notifications_view import NotificationPanel
from squid.bot.routes import AppRequest, AppRoutes, ScreenUnavailable
from squid.bot.submission.indexing import open_indexing_settings
from squid.bot.ui import tr
from squid.bot.utils.permissions import Grants, requires_grant
from squid.core.accounts.application import AccountService
from squid.core.accounts.application.edits import account_target
from squid.core.accounts.domain import IdentityProvider, LinkPreview, PrivacyNoticeReceipt
from squid.core.accounts.errors import AccountAlreadyLinkedError, AccountNotFoundError
from squid.core.accounts.models import Account
from squid.core.actors import ActingContext, Actor
from squid.core.edits.application import EditRunner
from squid.core.notifications import NotificationService
from squid.core.permissions.domain.catalogue import (
    ACCOUNT_CLAIM_APPROVE,
    ACCOUNT_CLAIM_LIST,
    ACCOUNT_CLAIM_REJECT,
    ACCOUNT_IDENTITY_REFRESH,
    ACCOUNT_IDENTITY_REFRESH_ANY,
)
from squid.core.suggestions.domain import Suggestion
from squid_i18n import md_tr
from squid_ui.ext.discord.modal import EntityField
from squid_ui.ext.discord.target_types import ComponentsV2Target

router = AppRoutes()


type AccountTab = Literal["overview", "accounts", "credits", "notifications", "review"]


class AccountWorkspace(sd.Screen[None, Any, RedstoneSquid]):
    """Manage the user's profile, linked accounts, build credits, and notifications."""

    policy = sd.ResponseSpec(timeout=300, session=sd.SessionSpec("account"))

    _account: Account | None = sl.state(None, opaque=True)
    _pending_focus: AccountTab | None = sl.state(None)
    _overview: AccountOverview | None = sl.state(None, opaque=True)
    _notifications: NotificationPanel | None = sl.state(None, opaque=True)
    _tabs: sp.Tabs[ComponentsV2Target, AppRequest] | None = sl.state(None, opaque=True)
    _merge_code: str | None = sl.state(None)
    _merge_decision: sp.ComponentDriver[sp.DecisionState, ComponentsV2Target, AppRequest] | None = sl.state(
        None, opaque=True
    )

    def __init__(
        self,
        *,
        accounts: AccountService,
        notifications: NotificationService,
        edits: EditRunner,
        actor: Callable[[], Awaitable[Actor]],
        actor_id: int,
        account: Account | None,
        request_link_confirmation: LinkConfirmationRequest,
        grants: Grants,
        discord_username: str | None = None,
    ) -> None:
        self._accounts = accounts
        self._notification_service = notifications
        self._edits = edits
        self._edit_actor = actor
        self._actor_id = actor_id
        self._discord_username = discord_username
        self._account = account
        self._request_link_confirmation = request_link_confirmation
        self.grants = grants

    @classmethod
    async def create(cls, request: AppRequest) -> Self:
        """Build the workspace for the requesting user, refreshing their stored Discord avatar."""
        actor = request.user
        account = await request.client.services.accounts.get_account_by_identity(
            IdentityProvider.DISCORD, str(actor.id)
        )
        if account is not None:
            await refresh_discord_avatar_key(request, account, actor)

        async def open_consent(
            event: sd.ActionEvent,
            answered: LinkConfirmationAnswer,
            *,
            preview: LinkPreview,
            on_abandon: Callable[[], Awaitable[None]] | None = None,
            timeout: float = 120.0,
        ) -> bool:
            press = event.context
            message_root = press.root
            assert message_root is not None, "a press always arrives from a mounted message"

            async def completed(prompt: sd.ActionEvent, consent: PrivacyNoticeReceipt | None) -> None:
                await answered(prompt, consent)
                if consent is not None:
                    await message_root.schedule()

            return await request_link_confirmation(
                event,
                user_id=actor.id,
                on_answer=completed,
                preview=preview,
                on_abandon=on_abandon,
                timeout=timeout,
            )

        async def edit_actor() -> Actor:
            return await request.client.actors.resolve(request, refresh=True)

        return cls(
            accounts=request.client.services.accounts,
            notifications=request.client.services.notifications,
            edits=request.client.services.edits,
            actor=edit_actor,
            actor_id=actor.id,
            discord_username=actor.name,
            account=account,
            request_link_confirmation=open_consent,
            grants=await Grants.load(
                request,
                ACCOUNT_CLAIM_LIST,
                ACCOUNT_CLAIM_APPROVE,
                ACCOUNT_CLAIM_REJECT,
                ACCOUNT_IDENTITY_REFRESH,
                ACCOUNT_IDENTITY_REFRESH_ANY,
            ),
        )

    @router.route("/account", fragment="tab")
    @classmethod
    async def open_account(cls, request: AppRequest, tab: AccountTab | None = None) -> Self:
        """Open the opener's account, at `tab` when they can use it."""
        screen = await cls.create(request)
        if tab is not None:
            try:
                screen.focus(tab)
            except ValueError as error:
                raise ScreenUnavailable(tr(t"navigation-tab-unavailable = That account tab is unavailable.")) from error
        return screen

    def focus_targets(self) -> frozenset[AccountTab]:
        tabs: set[AccountTab] = {"overview", "accounts", "credits", "notifications"}
        if ACCOUNT_CLAIM_LIST in self.grants:
            tabs.add("review")
        return frozenset(tabs)

    def focus(self, target: AccountTab) -> None:
        """Reveal an account tab, leaving any merge preview.

        Before loading, keep the latest request. If a reload removes the requested
        or selected tab, fall back to the first available tab.
        """
        if target not in self.focus_targets():
            message = f"Unavailable account tab: {target!r}"
            raise ValueError(message)
        if self._tabs is None:
            self._pending_focus = target
        else:
            self._tabs.focus(target)
        self._merge_code = None
        self._merge_decision = None

    @override
    async def on_load(self) -> None:
        await self._rebuild()

    async def _rebuild(self, *, changed_account_id: int | None = None) -> None:
        """Reload the account and rebuild tabs from its current identity and consent state."""
        self._account = await self._accounts.get_account_by_identity(IdentityProvider.DISCORD, str(self._actor_id))
        account_id = None if self._account is None else self._account.id
        if self._overview is not None and changed_account_id is not None:
            self._overview.edits.forget({account_target(changed_account_id).identity})
        if account_id is None:
            self._overview = None
        elif self._overview is None or self._overview._account_id != account_id:
            self._overview = AccountOverview(
                accounts=self._accounts,
                edits=self._edits,
                actor=self._edit_actor,
                account_id=account_id,
                actor_id=self._actor_id,
                discord_username=self._discord_username,
                on_access_changed=self._rebuild,
            )
        if self._overview is not None:
            await self._overview.on_load()
        account_missing = self._account is None or account_id is None
        if account_missing:
            self._notifications = None
        elif self._notifications is None or self._notifications.account_id != account_id:
            assert account_id is not None
            self._notifications = NotificationPanel(
                notifications=self._notification_service,
                edits=self._edits,
                actor=self._edit_actor,
                account_id=account_id,
            )
        setup = self._setup_nodes(
            tr(t"account-workspace-set-up-profile = Set up your account"),
            tr(t"account-workspace-profile-setup-help = Create an account linked to your Discord ID."),
        )
        credit = (
            self._claim_nodes(self._account)
            if self._account is not None and not account_missing
            else self._setup_nodes(
                tr(t"account-claim-heading = Claim credit for your builds"),
                tr(t"account-workspace-credit-setup-help = Create an account to claim build credit."),
            )
        )
        notifications = self._notifications or self._setup_nodes(
            tr(t"account-workspace-notifications = Notifications"),
            tr(
                t"account-workspace-notifications-setup-help = Create an account to access your inbox, follows, and delivery settings."
            ),
        )
        tabs: list[sp.Tab[ComponentsV2Target, AppRequest]] = [
            sp.Tab("overview", tr(t"account-workspace-overview = Overview"), self._overview or setup),
            sp.Tab("accounts", tr(t"account-workspace-accounts = Accounts"), self._identity_nodes()),
            sp.Tab("credits", tr(t"account-workspace-credits = Credits"), credit),
            sp.Tab("notifications", tr(t"account-workspace-notifications = Notifications"), notifications),
        ]
        if ACCOUNT_CLAIM_LIST in self.grants:
            claims = await self._accounts.pending_alias_claims(
                with_claimants=True, context=ActingContext.for_actor(await self._edit_actor())
            )
            review = ClaimReviewComponent(
                self._accounts, claims, actor=self._edit_actor, author_id=self._actor_id, grants=self.grants
            )
            tabs.append(sp.Tab("review", tr(t"account-workspace-review-claims = Review claims"), review))
        selected: str | None = self._pending_focus
        if selected is None and self._tabs is not None:
            selected = self._tabs.machine_state.selected
        initial = selected if selected in {tab.key for tab in tabs} else None
        self._pending_focus = None
        self._tabs = sp.Tabs(
            tabs, initial=initial, key="account-tabs", title=tr(t"account-workspace-account = Account")
        )

    @override
    def render(self) -> tuple[sl.LayoutNode[ComponentsV2Target, AppRequest], ...]:
        if self._merge_code is not None and self._merge_decision is not None:
            return (self._merge_decision,)
        if self._tabs is None:
            return (sl.status(tr(t"account-workspace-loading-account = Loading account.")),)
        return (self._tabs,)

    def _privacy_control(self) -> sl.semantic.ActionControl[sd.Request]:
        return sl.action_control(
            tr(t"account-workspace-review-privacy-notice = Create account"), self._create_account, key="create-account"
        )

    def _setup_nodes(
        self, title: sl.TextLike, help_text: sl.TextLike
    ) -> tuple[sl.LayoutNode[ComponentsV2Target, AppRequest], ...]:
        return (
            sl.section(sl.heading(title), sl.paragraph(help_text)),
            sl.action_controls(self._privacy_control(), key="account-setup"),
        )

    def _identity_nodes(self) -> list[sl.LayoutNode[ComponentsV2Target, AppRequest]]:
        account = self._account
        can_refresh = (
            account is not None
            and account.id is not None  # pyright: ignore[reportUnnecessaryComparison]
            and ACCOUNT_IDENTITY_REFRESH in self.grants
            and bool(account.identities_for(IdentityProvider.JAVA))
        )
        header = sl.section(
            sl.heading(tr(t"account-link-heading = Connect Minecraft")),
            sl.paragraph(
                md_tr(
                    t"account-link-help = Run `/link` in Minecraft on the community server, then enter the code here."
                )
            ),
            can_refresh
            and sl.note(tr(t"account-refresh-help = Refresh Minecraft after you change your in-game name or skin.")),
        )
        controls: list[sl.semantic.ActionControl[sd.Request] | sl.semantic.FormTrigger[sd.Request]] = []
        controls.append(
            sl.form(
                tr(t"account-workspace-link-minecraft-account = Link Minecraft account"),
                _LinkForm().spec(),
                key="link",
                on_submit=self._link,
            )
        )
        if can_refresh:
            controls.append(
                sl.action_control(
                    tr(t"account-workspace-refresh-minecraft = Refresh Minecraft"),
                    self._refresh_identity,
                    key="refresh",
                )
            )
        if ACCOUNT_IDENTITY_REFRESH_ANY in self.grants:
            controls.append(
                sl.form(
                    tr(t"account-refresh-member-action = Refresh member"),
                    _RefreshMemberForm().spec(),
                    key="staff-refresh",
                    on_submit=self._refresh_member_identity,
                )
            )
        nodes: list[sl.LayoutNode[ComponentsV2Target, AppRequest]] = [
            header,
            sl.action_controls(
                sl.routed_action_control(
                    tr(t"discord-indexing-manage = Manage automatic indexing"),
                    open_indexing_settings.custom_id(),
                    key="indexing",
                ),
                key="indexing-settings",
            ),
            sl.action_controls(*controls, key="identity-tools", display=sl.semantic.ControlDisplay.INDIVIDUAL),
        ]
        if account is not None and account.id is not None:  # pyright: ignore[reportUnnecessaryComparison]
            nodes.extend(self._merge_nodes())
        return nodes

    def _claim_nodes(self, account: Account) -> tuple[sl.LayoutNode[ComponentsV2Target, AppRequest], ...]:
        return (
            sl.section(
                sl.heading(tr(t"account-claim-heading = Claim credit for your builds")),
                sl.paragraph(
                    tr(
                        t"account-claim-help = If your builds are credited to another name you used, "
                        t"submit a claim. Staff review it before adding the credit to your account."
                    )
                ),
            ),
            sl.action_controls(
                sl.routed_action_control(
                    tr(t"account-credits-builds = Your builds"),
                    BuildBrowseScreen.open_creator_builds.custom_id(public_id=account.public_account_id),
                    key="builds",
                ),
                sl.action_control(
                    tr(t"account-search-credit-names = Search credited names"),
                    self._choose_claim,
                    key="search-claims",
                ),
                sl.form(
                    tr(t"account-workspace-claim-creator-credit = Enter a name manually"),
                    _ClaimForm().spec(),
                    key="claim",
                    on_submit=self._claim,
                ),
                key="claim-actions",
                display=sl.semantic.ControlDisplay.INDIVIDUAL,
            ),
        )

    def _merge_nodes(self) -> tuple[sl.LayoutNode[ComponentsV2Target, AppRequest], ...]:
        return (
            sl.section(
                sl.heading(tr(t"account-merge-heading = Merge Squid accounts")),
                sl.paragraph(
                    tr(
                        t"account-merge-help = Create a code on the account you're moving. "
                        t"Enter it on the account you want to keep to preview the transfer."
                    )
                ),
            ),
            sl.action_controls(
                sl.action_control(
                    tr(t"account-workspace-create-merge-code = Create merge code"),
                    self._create_merge_code,
                    key="merge-code",
                ),
                sl.form(
                    tr(t"account-merge-preview-action = Enter merge code"),
                    _MergeForm().spec(),
                    key="merge",
                    on_submit=self._request_merge,
                ),
                key="merge-code-actions",
                display=sl.semantic.ControlDisplay.INDIVIDUAL,
            ),
        )

    async def _create_account(self, event: sd.PressEvent) -> None:
        await self._accounts.get_or_create_identity(IdentityProvider.DISCORD, str(self._actor_id))
        await self._rebuild()

    async def _link(self, event: sd.SubmitEvent) -> None:
        """Reserve the link code while the user reviews its identity and credit effects.

        The continuation consumes the reservation on acceptance. Cancellation,
        abandonment, or a failed link releases it; `settled` prevents duplicate cleanup.
        """
        account = self._account
        form = _LinkForm.from_submission(event)
        code = form.code
        context = ActingContext.for_actor(await self._edit_actor())
        reservation = await self._accounts.reserve_minecraft_link(
            code,
            context=context,
            ttl_seconds=int(LINK_CONFIRMATION_TIMEOUT_SECONDS),
        )
        conflict = (
            None
            if account is None
            else link_conflict(reservation.preview, account.identities_for(IdentityProvider.JAVA))
        )
        if conflict is not None:
            await self._accounts.release_minecraft_link(code, reservation, context=context)
            raise AccountAlreadyLinkedError(
                account_id=account.id if account is not None else None, minecraft_uuid=conflict
            )

        settled = False
        settlement = asyncio.Lock()

        async def release() -> None:
            nonlocal settled
            with anyio.CancelScope(shield=True):
                async with settlement:
                    if settled:
                        return
                    await self._accounts.release_minecraft_link(code, reservation, context=context)
                    settled = True

        async def consented_account_id(consent: PrivacyNoticeReceipt) -> int:
            linked_account = account
            if linked_account is None or linked_account.id is None:  # pyright: ignore[reportUnnecessaryComparison]
                linked_account = await self._accounts.get_or_create_identity(
                    IdentityProvider.DISCORD,
                    str(self._actor_id),
                    consent=consent,
                )
            account_id = linked_account.id
            if account_id is None:  # pyright: ignore[reportUnnecessaryComparison]
                raise AccountNotFoundError(provider=IdentityProvider.DISCORD, subject=str(self._actor_id))
            return account_id

        async def answered(prompt: sd.ActionEvent, consent: PrivacyNoticeReceipt | None) -> None:
            nonlocal settled
            if consent is None:
                await release()
                return
            try:
                async with settlement:
                    if settled:
                        return
                    linked_account_id = await consented_account_id(consent)
                    refresh = await self._accounts.link_minecraft_account(
                        code,
                        consent=consent,
                        context=ActingContext.for_actor(
                            replace((await self._edit_actor()).require_user(), account_id=linked_account_id)
                        ),
                        reservation=reservation,
                    )
                    settled = True
            except BaseException:
                await release()
                raise
            await self._rebuild(changed_account_id=linked_account_id)
            await prompt.notice(link_message(refresh))

        try:
            opened = await self._request_link_confirmation(
                event,
                answered,
                preview=reservation.preview,
                on_abandon=release,
                timeout=LINK_CONFIRMATION_TIMEOUT_SECONDS,
            )
        except BaseException:
            # Failed or cancelled presentation never transfers the reservation to a live child.
            with anyio.CancelScope(shield=True):
                await release()
            raise
        if not opened:
            await release()

    @requires_grant(
        ACCOUNT_IDENTITY_REFRESH,
        denied=tr(t"account-refresh-forbidden = You are no longer allowed to refresh your Minecraft identity."),
    )
    async def _refresh_identity(self, event: sd.PressEvent) -> None:
        account_id = self._account_id()
        refresh = await self._accounts.refresh_java_identity(
            account_id, context=ActingContext.for_actor(await self._edit_actor())
        )
        await self._rebuild(changed_account_id=account_id)
        await event.notice(refresh_message(refresh))

    @requires_grant(
        ACCOUNT_IDENTITY_REFRESH_ANY,
        denied=tr(
            t"account-workspace-refresh-denied-again = You are no longer allowed to refresh another member's identity."
        ),
    )
    async def _refresh_member_identity(self, event: sd.SubmitEvent) -> None:
        form = _RefreshMemberForm.from_submission(event)
        member = form.user
        assert isinstance(member, discord.abc.Snowflake)
        account = await self._accounts.get_account_by_identity(IdentityProvider.DISCORD, str(member.id))
        if account is None or account.id is None:  # pyright: ignore[reportUnnecessaryComparison]
            raise AccountNotFoundError(provider=IdentityProvider.DISCORD, subject=str(member.id))
        refresh = await self._accounts.refresh_java_identity(
            account.id, context=ActingContext.for_actor(await self._edit_actor())
        )
        await self._rebuild(changed_account_id=account.id)
        await event.notice(refresh_message(refresh))

    async def _claim(self, event: sd.SubmitEvent) -> None:
        form = _ClaimForm.from_submission(event)
        await self._submit_claim(event, form.name)

    async def _choose_claim(self, event: sd.PressEvent) -> None:
        await pick(
            cast(sd.PressEvent[RedstoneSquid], event),
            "creators",
            tr(t"account-search-credit-names = Search credited names"),
            self._claim_picked,
            confirm=tr(t"account-submit-claim = Submit claim"),
        )

    async def _claim_picked(self, event: sd.ActionEvent, item: Suggestion) -> None:
        await self._submit_claim(event, item.value)

    async def _submit_claim(self, event: sd.ActionEvent, name: str) -> None:
        claim = await self._accounts.request_alias_claim(
            name, context=ActingContext.for_actor(await self._edit_actor())
        )
        claim_id = claim.id
        alias_name = claim.alias_name
        await event.notice(
            md_tr(
                t"account-claim-pending-review = Claim #{claim_id!s} for **{alias_name}** is awaiting staff approval.",
            )
        )

    async def _create_merge_code(self, event: sd.PressEvent) -> None:
        code, ticket = await self._accounts.create_merge_code(context=ActingContext.for_actor(await self._edit_actor()))
        expiry = sl.md(discord.utils.format_dt(ticket.expires_at.to_stdlib(), style="R"))
        await event.notice(
            md_tr(
                t"account-merge-code-warning = Merge code `{code}` expires {expiry}. "
                t"Keep it private. Anyone with the code can merge this account into theirs.",
            )
        )

    async def _request_merge(self, event: sd.SubmitEvent) -> None:
        """Preview the merge and retain its code until the user confirms or cancels."""
        form = _MergeForm.from_submission(event)
        code = form.code
        preview = await self._accounts.preview_merge(code, context=ActingContext.for_actor(await self._edit_actor()))
        aliases = len(preview.alias_names)
        identities = preview.identity_count
        builds = preview.build_count
        self._merge_code = code
        self._merge_decision = sp.Decision[ComponentsV2Target, AppRequest](
            md_tr(
                t"account-merge-transfer-preview = Move {aliases} creator "
                t"names, {identities} identities, and {builds} build credits here? This cannot be undone.",
            ),
            (
                sp.DecisionOption("confirm", tr(t"account-workspace-merge-accounts = Merge accounts"), sl.Tone.DANGER),
                sp.DecisionOption("cancel", tr(t"account-workspace-cancel = Cancel")),
            ),
            key="merge-account",
        ).build_component(on_decide=self._finish_merge)

    async def _finish_merge(self, event: sp.TransitionEvent[sp.DecisionState], choice: str) -> None:
        code = self._merge_code
        if code is None or choice == "cancel":
            self._merge_code = None
            self._merge_decision = None
            return
        result = await self._accounts.complete_merge(code, context=ActingContext.for_actor(await self._edit_actor()))
        self._merge_code = None
        self._merge_decision = None
        await self._rebuild(changed_account_id=result.surviving_account_id)
        await event.source.notice(
            tr(t"account-merge-complete = The other account's creator page now redirects to yours.")
        )

    def _account_id(self) -> int:
        account = self._account
        if account is None or account.id is None:  # pyright: ignore[reportUnnecessaryComparison]
            message = "account workflow requires a persisted account"
            raise RuntimeError(message)
        return account.id


class _LinkForm(sl.forms.Form[sd.Request[Any, Any]]):
    title = tr(t"account-workspace-link-minecraft-account = Link Minecraft account")

    code = sl.forms.TextField(label=tr(t"account-workspace-in-game-link-code = In-game link code"), maximum=100)


class _RefreshMemberForm(sl.forms.Form[sd.Request[Any, Any]]):
    title = tr(t"account-workspace-refresh-member-identity = Refresh member identity")

    user = EntityField(label=tr(t"account-workspace-discord-member = Discord member"))


class _ClaimForm(sl.forms.Form[sd.Request[Any, Any]]):
    title = tr(t"account-workspace-claim-an-older-creator-name = Claim an older creator name")

    name = sl.forms.TextField(label=tr(t"account-workspace-creator-name = Creator name"), maximum=100)


class _MergeForm(sl.forms.Form[sd.Request[Any, Any]]):
    title = tr(t"account-workspace-preview-account-merge = Preview account merge")

    code = sl.forms.TextField(label=tr(t"account-workspace-merge-code = Merge code"), maximum=100)


async def refresh_discord_avatar_key(
    request: AppRequest, account: Account, user: discord.Member | discord.User
) -> None:
    """Persist the supplied Discord user's avatar hash when it differs from the stored identity.

    Opening the owner's account provides both the linked identity and a Discord
    user object; public profile rendering can then use the stored hash.
    """
    identity = account.identity_for(IdentityProvider.DISCORD, str(user.id))
    if account.id is None or identity is None or identity.id is None:  # pyright: ignore[reportUnnecessaryComparison]
        return
    key = user.avatar.key if user.avatar is not None else None
    if key != identity.avatar_key:
        await request.client.services.accounts.record_identity_avatar_key(
            identity.id, key, context=ActingContext.for_actor(await request.client.actors.resolve(request))
        )
