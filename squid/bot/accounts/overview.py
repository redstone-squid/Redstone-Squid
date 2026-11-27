"""Render account identities, visibility controls, and an editable creator profile.

Identity and profile data are reloaded from the account service. Changes that
need current consent continue from a separate prompt so the panel does not hold
its UI action transaction open while the user reads the notice.
"""

from collections.abc import Awaitable, Callable
from typing import Protocol, override
from uuid import UUID

import discord

import squid_ui as sl
import squid_ui.ext.discord as sd
import squid_ui_widgets as sp
from squid.bot.accounts.creator_page import CreatorPage
from squid.bot.accounts.profile_editor import ProfileEditor
from squid.bot.accounts.profile_render import identity_label as _identity_label
from squid.bot.accounts.profile_render import own_profile_avatar, own_profile_fields, provider_label
from squid.bot.edit_controller import EditController
from squid.bot.ui import CardField, tr
from squid.core.accounts.application import AccountService
from squid.core.accounts.application.edits import (
    PROFILE_EDIT,
    UNLINK_EDIT,
    VISIBILITY_EDIT,
    ProfileValues,
    account_target,
)
from squid.core.accounts.domain import (
    AccountConsent,
    IdentityProvider,
    LinkPreview,
    ProfileUpdate,
)
from squid.core.accounts.models import AccountIdentity, AccountProfile
from squid.core.actors import ActingContext, Actor
from squid.core.edits.application import EditRunner
from squid.core.edits.domain import EditValues, Revision, RevisionConflictError
from squid_i18n import md_tr
from squid_ui.ext.discord.target_types import ComponentsV2Target

MAX_LISTED = 25
"""Maximum identities offered by one Discord select."""

type ConsentAnswer = Callable[[sd.ActionEvent, AccountConsent | None], Awaitable[None]]


class ConsentRequest(Protocol):
    """Open a consent prompt whose mount owns any unanswered cleanup."""

    def __call__(
        self,
        event: sd.ActionEvent,
        answered: ConsentAnswer,
        *,
        preview: LinkPreview | None = None,
        on_abandon: Callable[[], Awaitable[None]] | None = None,
        timeout: float = 120.0,
    ) -> Awaitable[bool]: ...


class AccountOverview(sl.Component[ComponentsV2Target, sd.Request]):
    """Edit one account's profile and choose which linked identities appear publicly.

    Used as the overview within AccountWorkspace. Identity visibility changes are
    saved immediately; profile fields stay in the editor until Save profile.
    """

    selected_id: int | None = sl.state(None)
    _revision: int = sl.state(1)
    # Reload these values on mount instead of restoring stale service data from a snapshot.
    _identities: tuple[AccountIdentity, ...] = sl.state(())
    _needs_consent: bool = sl.state(False)
    # No default: the empty profile needs this instance's account id.
    _profile: AccountProfile = sl.state()
    _public_id: UUID | None = sl.state(None)
    _profile_editor: ProfileEditor | None = sl.state(None, opaque=True)

    def __init__(
        self,
        *,
        accounts: AccountService,
        edits: EditRunner,
        actor: Callable[[], Awaitable[Actor]],
        account_id: int,
        actor_id: int,
        request_consent: ConsentRequest,
        discord_username: str | None = None,
        on_access_changed: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._accounts = accounts
        self._account_id = account_id
        self._actor_id = actor_id
        self._discord_username = discord_username
        self._on_access_changed = on_access_changed
        self._request_consent = request_consent
        self._profile = AccountProfile.empty(account_id)
        self._profile_editor = None
        self._edit_actor = actor
        self.edits = EditController(edits, actor, self._reload)

    @override
    async def on_load(self) -> None:
        await self._refresh()

    async def _refresh(self) -> None:
        """Reload account and profile data, clearing a selection that is no longer displayed."""
        snapshot = await self._accounts.editor_snapshot(
            self._account_id, context=ActingContext.for_actor(await self._edit_actor())
        )
        self._revision = snapshot.account.revision
        self._identities = snapshot.account.identities
        self._needs_consent = snapshot.account.needs_consent_refresh
        self._profile = snapshot.profile
        self._public_id = snapshot.account.public_account_id
        if self.selected is None:
            self.selected_id = None

    @property
    def identities(self) -> tuple[AccountIdentity, ...]:
        return self._identities[:MAX_LISTED]

    @property
    def selected(self) -> AccountIdentity | None:
        return next((identity for identity in self.identities if identity.id == self.selected_id), None)

    @property
    def page_hidden(self) -> bool:
        return self._profile.hidden

    @override
    def render(self) -> sl.DocumentLike[ComponentsV2Target, sd.Request]:
        if self._profile_editor is not None:
            return (self._profile_editor,)
        fields = tuple(sl.field(field.name, field.value) for field in self._fields())
        footer = self._footer()
        media = own_profile_avatar(self._profile, self._identities)
        extra_media = media[1:]
        nodes: list[sl.LayoutNode[ComponentsV2Target, sd.Request]] = [
            sl.section(
                sl.heading(self._profile.display_name or tr(t"account-view-creator-profile = Creator profile")),
                # Shorten the bio before reducing the identity fields or footer.
                self._profile.bio and sl.paragraph(self._profile.bio).truncate(),
                sl.fields(*fields),
                bool(extra_media) and sl.media(*extra_media, key="media"),
                footer and sl.note(footer),
                thumbnail=media[0] if media else None,
            )
        ]
        identity = self.selected
        if self.identities:
            nodes.extend(
                (
                    sl.note(
                        tr(
                            t"account-view-select-linked-account = Select a linked account to change its visibility or unlink it."
                        )
                    ),
                    sl.choices(
                        *(
                            sl.choice(
                                self._picker_label(linked),
                                key=str(linked.id),
                                description=self._identity_visibility(linked),
                            )
                            for linked in self.identities
                            # ORM identity columns may still be None before flush.
                            if linked.id is not None  # pyright: ignore[reportUnnecessaryComparison]
                        ),
                        key="identity",
                        selection=(str(self.selected_id),) if self.selected_id is not None else (),
                        on_change=self._selection_changed,
                    ),
                )
            )
        nodes.append(
            sl.action_controls(
                sl.action_control(
                    tr(t"account-view-edit-page = Edit profile"),
                    self._edit_page,
                    key="edit_page",
                    emphasis=sl.semantic.Emphasis.STRONG,
                ),
                sl.toggle(
                    tr(t"account-view-creator-page = Profile"),
                    key="page_visibility",
                    on=not self.page_hidden,
                    on_change=self._toggle_page,
                    on_label=tr(t"account-view-shown = Public"),
                    off_label=tr(t"account-view-hidden-2 = Hidden"),
                ),
                # Strangers see a filtered projection, so check visibility changes on the real page.
                self._public_id is not None
                and sl.routed_action_control(
                    tr(t"account-view-public-page = View public page"),
                    CreatorPage.open.custom_id(public_id=self._public_id),
                    key="public_page",
                ),
                identity is not None
                and sl.toggle(
                    tr(t"account-view-selected-identity = Account"),
                    key="identity_visibility",
                    on=identity.is_public,
                    on_change=self._toggle_identity,
                    on_label=tr(t"account-view-shown-on-page = Shown"),
                    off_label=tr(t"account-view-hidden-from-page = Hidden"),
                ),
                bool(self.identities)
                and sl.action_control(
                    tr(t"account-view-unlink = Unlink account"),
                    self._unlink,
                    key="unlink",
                    tone=sl.Tone.DANGER,
                    available=identity is not None,
                    guard=sp.guards.confirm(self._unlink_warning()),
                ),
                key="profile-actions",
                display=sl.semantic.ControlDisplay.INDIVIDUAL,
            )
        )
        nodes.extend(self.edits.refresh_controls())
        return nodes

    async def _selection_changed(self, event: sd.ChoiceEvent) -> None:
        self.selected_id = int(event.selected[0])

    async def _toggle_identity(self, event: sd.ToggleEvent) -> None:
        identity = self.selected
        if identity is None or identity.id is None:  # pyright: ignore[reportUnnecessaryComparison]
            return
        identity_id, is_public = identity.id, event.value

        async def apply() -> None:
            await self.edits.bind(VISIBILITY_EDIT, account_target(self._account_id), self._revision).save(
                EditValues.of({"identity_id": identity_id, "is_public": is_public}),
                tr(t"account-history-visibility = Change identity visibility"),
            )

        await self._with_consent(event, apply)

    async def _toggle_page(self, event: sd.ToggleEvent) -> None:
        hidden = not event.value

        async def apply() -> None:
            await self._save_profile(ProfileUpdate(hidden=hidden))

        await self._with_consent(event, apply)

    async def _unlink(self, event: sd.PressEvent) -> None:
        """Remove the selected identity. The reader has already agreed to this."""
        identity = self.selected
        if identity is None or identity.id is None:  # pyright: ignore[reportUnnecessaryComparison]
            return
        await event.acknowledge()
        await self.edits.bind(UNLINK_EDIT, account_target(self._account_id), self._revision).save(
            EditValues.of({"identity_id": identity.id}), tr(t"account-history-unlink = Unlink identity")
        )
        if identity.discord_id == self._actor_id and self._on_access_changed is not None:
            await self._on_access_changed()
        removed_label = _identity_label(identity)
        await event.notice(
            md_tr(
                t"account-identity-unlinked = Unlinked {removed_label}. Any build credit you hold is unaffected.",
            )
        )

    async def _edit_page(self, event: sd.PressEvent) -> None:
        async def apply() -> None:
            self._profile_editor = ProfileEditor(
                self._profile,
                save=self.edits.bind(PROFILE_EDIT, account_target(self._account_id), self._revision).writer(
                    ProfileValues.patch, tr(t"account-history-profile = Edit profile")
                ),
                close=self._close_profile_editor,
            )

        await self._with_consent(event, apply)

    def _close_profile_editor(self) -> None:
        self._profile_editor = None

    async def _save_profile(self, update: ProfileUpdate) -> None:
        await self.edits.bind(PROFILE_EDIT, account_target(self._account_id), self._revision).save(
            ProfileValues.patch(update), tr(t"account-history-profile = Edit profile")
        )

    async def _with_consent(self, event: sd.ActionEvent, work: Callable[[], Awaitable[None]]) -> None:
        """Run the change immediately or after acceptance in a separate consent prompt.

        The prompt is opened without waiting for its answer, releasing this panel's
        action transaction and dispatch lock. On acceptance, record consent and run
        the change in a fresh parent action. The injected consent requester arranges
        for the parent panel to redraw.
        """
        if not self._needs_consent:
            await work()
            return

        async def answered(_prompt: sd.ActionEvent, consent: AccountConsent | None) -> None:
            if consent is None:
                # Cancelling leaves the account and the displayed panel unchanged.
                return
            granted = await self._accounts.grant_current_consent(
                context=ActingContext.for_actor(await self._edit_actor())
            )
            # Consent is saved outside session history, so its entries for this account
            # now expect an old revision and could only fail. Drop them.
            self.edits.forget({account_target(self._account_id).identity})
            # Saving consent advances the revision by exactly one. A larger step means the
            # account also changed elsewhere since this panel loaded, so `work` may be stale.
            expected = Revision(account_target(self._account_id), self._revision + 1)
            if granted.revision != expected.value:
                raise RevisionConflictError(expected, granted.revision)
            self._revision = granted.revision
            self._needs_consent = False
            if self._on_access_changed is not None:
                await self._on_access_changed()
            await work()

        await self._request_consent(event, answered)

    async def _reload(self) -> None:
        await self._refresh()
        self.invalidate()

    def _fields(self) -> list[CardField]:
        fields = own_profile_fields(self._profile)
        fields += [CardField(_identity_label(identity), self.identity_detail(identity)) for identity in self.identities]
        if not fields:
            fields.append(
                CardField(
                    tr(t"account-view-linked-accounts = Linked accounts"),
                    tr(t"account-view-none-yet = _None yet._"),
                )
            )
        # The creator page's visibility is shown on its toggle, so it is not repeated here.
        return fields

    def _picker_label(self, identity: AccountIdentity) -> sl.TextLike:
        provider = provider_label(identity.provider)
        name = identity.display_name or identity.subject
        if identity.provider is IdentityProvider.DISCORD and identity.discord_id == self._actor_id:
            name = self._discord_username or identity.display_name
            if name is None:
                return tr(t"account-view-current-discord = Your Discord account")
        return md_tr(t"account-view-account-option = {provider} — {name}")

    @staticmethod
    def _identity_visibility(identity: AccountIdentity) -> sl.TextLike:
        return (
            tr(t"account-view-shown-publicly = Shown on your profile")
            if identity.is_public
            else tr(t"account-view-hidden = Hidden from your profile")
        )

    def identity_detail(self, identity: AccountIdentity) -> sl.TextLike:
        visibility = self._identity_visibility(identity)
        age = (
            sl.md(discord.utils.format_dt(identity.verified_at.to_stdlib(), style="R"))
            if identity.verified_at is not None
            else tr(t"account-view-unknown = unknown")
        )
        return md_tr(t"account-view-verified = {visibility} · verified {age}")

    def _unlink_warning(self) -> sl.TextLike:
        """What the reader is agreeing to, asked before the press rather than after it."""
        identity = self.selected
        if identity is None:
            return tr(t"account-view-remove-this-linked-account = Remove this linked account?")
        identity_label = _identity_label(identity)
        warning = md_tr(
            t"account-linked-identity-remove-warning = Remove {identity_label}? Any build credit you hold "
            t"is unaffected.",
        )
        if identity.provider is IdentityProvider.DISCORD and identity.discord_id == self._actor_id:
            consequence = tr(
                t"account-discord-unlink-warning = This is the Discord account you "
                t"are using now. The bot will stop recognising you here."
            )
            return md_tr(t"account-view-value = {warning} {consequence}")
        return warning

    def _footer(self) -> sl.TextLike | None:
        if self.page_hidden:
            return tr(
                t"account-hidden-page-credit-note = Your profile is hidden. Your credited names still appear on your builds."
            )
        return None
