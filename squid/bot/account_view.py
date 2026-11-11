"""Render account identities, visibility controls, and an editable creator profile.

Identity and profile data are reloaded from the account service. Changes that
need current consent continue from a separate prompt so the panel does not hold
its UI action transaction open while the user reads the notice.
"""

from collections.abc import Awaitable, Callable
from typing import Protocol, override

import discord

import squid_ui as sl
import squid_ui_discord as sd
import squid_ui_widgets as sp
from squid.accounts.application import AccountService
from squid.accounts.application.edits import PROFILE_EDIT, UNLINK_EDIT, VISIBILITY_EDIT, ProfileValues, account_target
from squid.accounts.domain import (
    AccountConsent,
    IdentityProvider,
    LinkPreview,
    ProfileUpdate,
)
from squid.accounts.models import AccountIdentity, AccountProfile
from squid.bot.edit_controller import EditController
from squid.bot.profile_editor import ProfileEditor
from squid.bot.profile_render import identity_label as _identity_label
from squid.bot.profile_render import own_profile_avatar, own_profile_fields
from squid.bot.ui import CardField, tr
from squid.core.actors import Actor
from squid.edits.application import EditRunner
from squid.edits.domain import EditCommand, EditValues, Revision, RevisionConflictError

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


class AccountOverview(sl.Component[sl.ComponentsV2Target, sd.Request]):
    """Edit one account's profile and choose which linked identities appear publicly.

    Used as the overview within AccountWorkspace. Identity visibility changes are
    saved immediately; profile fields stay in the editor until Save profile.
    """

    history: sl.runtime.History = sl.runtime.history(limit=20)

    selected_id: int | None = sl.state(None)
    _revision: int = sl.state(1, persist=False)
    # Reload these values on mount instead of restoring stale service data from a snapshot.
    _identities: tuple[AccountIdentity, ...] = sl.state((), persist=False)
    _needs_consent: bool = sl.state(default=False, persist=False)
    # No default: the empty profile needs this instance's account id.
    _profile: AccountProfile = sl.state(persist=False)
    _profile_editor: ProfileEditor | None = sl.state(None, persist=False, opaque=True)

    def __init__(
        self,
        *,
        accounts: AccountService,
        edits: EditRunner,
        actor: Callable[[], Awaitable[Actor]],
        account_id: int,
        actor_id: int,
        request_consent: ConsentRequest,
    ) -> None:
        self._accounts = accounts
        self._account_id = account_id
        self._actor_id = actor_id
        self._request_consent = request_consent
        self._profile = AccountProfile.empty(account_id)
        self._profile_editor = None
        self.edits = EditController(edits, actor, self._reload, history=self.history)

    @override
    async def on_load(self) -> None:
        await self._refresh()

    async def _refresh(self) -> None:
        """Reload account and profile data, clearing a selection that is no longer displayed."""
        snapshot = await self._accounts.editor_snapshot(self._account_id)
        self._revision = snapshot.account.revision
        self._identities = snapshot.account.identities
        self._needs_consent = snapshot.account.needs_consent_refresh
        self._profile = snapshot.profile
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
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        if self._profile_editor is not None:
            return (self._profile_editor,)
        fields = tuple(sl.field(field.name, field.value) for field in self._fields())
        footer = self._footer()
        media = own_profile_avatar(self._profile, self._identities)
        extra_media = media[1:]
        nodes: list[sl.LayoutNode[sl.ComponentsV2Target, sd.Request]] = [
            sl.section(
                sl.heading(self._profile.display_name or tr(t"account-view-your-account = Your account")),
                # Shorten the bio before reducing the identity fields or footer.
                self._profile.bio and sl.truncate(sl.paragraph(self._profile.bio)),
                sl.fields(*fields),
                bool(extra_media) and sl.media(*extra_media, key="media"),
                footer and sl.note(footer),
                thumbnail=media[0] if media else None,
            )
        ]
        if self.identities:
            nodes.append(
                sl.choices(
                    *(
                        sl.choice(
                            _identity_label(identity),
                            key=str(identity.id),
                            description=self.identity_detail(identity),
                        )
                        for identity in self.identities
                        if identity.id is not None
                    ),
                    key="identity",
                    selection=sl.controlled(
                        (str(self.selected_id),) if self.selected_id is not None else (), self._selection_changed
                    ),
                )
            )
        identity = self.selected
        nodes.extend(
            (
                sl.toggle(
                    tr(t"account-view-selected-identity = Selected identity"),
                    key="identity_visibility",
                    on=sl.controlled(identity is not None and identity.is_public, self._toggle_identity),
                    on_label=tr(t"account-view-shown-on-page = Shown on page"),
                    off_label=tr(t"account-view-hidden-from-page = Hidden from page"),
                    available=identity is not None,
                ),
                sl.toggle(
                    tr(t"account-view-creator-page = Creator page"),
                    key="page_visibility",
                    on=sl.controlled(not self.page_hidden, self._toggle_page),
                    on_label=tr(t"account-view-shown = Shown"),
                    off_label=tr(t"account-view-hidden-2 = Hidden"),
                ),
            )
        )
        nodes.append(
            sl.action_controls(
                sl.action_control(
                    tr(t"account-view-unlink = Unlink"),
                    self._unlink,
                    key="unlink",
                    tone=sl.Tone.DANGER,
                    available=self.selected is not None,
                    guard=sp.guards.confirm(self._unlink_warning()),
                ),
                sl.action_control(
                    tr(t"account-view-edit-page = Edit page"),
                    self._edit_page,
                    key="edit_page",
                    emphasis=sl.semantic.Emphasis.STRONG,
                ),
                key="account-actions",
            )
        )
        nodes.extend(self.edits.controls())
        return tuple(nodes)

    async def _selection_changed(self, event: sd.ChoiceEvent) -> None:
        self.selected_id = int(event.selected[0])

    async def _toggle_identity(self, event: sd.ToggleEvent) -> None:
        identity = self.selected
        if identity is None or identity.id is None:
            return
        identity_id, is_public = identity.id, event.value

        async def apply() -> None:
            await self.edits.execute(
                EditCommand(
                    VISIBILITY_EDIT,
                    (Revision(account_target(self._account_id), self._revision),),
                    EditValues.of({"identity_id": identity_id, "is_public": is_public}),
                ),
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
        if identity is None or identity.id is None:
            return
        await event.acknowledge()
        await self.edits.execute(
            EditCommand(
                UNLINK_EDIT,
                (Revision(account_target(self._account_id), self._revision),),
                EditValues.of({"identity_id": identity.id}),
            ),
            tr(t"account-history-unlink = Unlink identity"),
        )
        removed_label = _identity_label(identity)
        await event.notice(
            tr(t"account-identity-unlinked = Unlinked {removed_label}. Any build credit you hold is unaffected.")
        )

    async def _edit_page(self, event: sd.PressEvent) -> None:
        async def apply() -> None:
            self._profile_editor = ProfileEditor(
                self._profile, save=self._save_profile, close=self._close_profile_editor
            )

        await self._with_consent(event, apply)

    def _close_profile_editor(self) -> None:
        self._profile_editor = None

    async def _save_profile(self, update: ProfileUpdate) -> None:
        await self.edits.execute(
            EditCommand(
                PROFILE_EDIT,
                (Revision(account_target(self._account_id), self._revision),),
                ProfileValues.patch(update),
            ),
            tr(t"account-history-profile = Edit profile"),
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
            granted = await self._accounts.grant_current_consent(self._account_id)
            expected = Revision(account_target(self._account_id), self._revision + 1)
            if granted.revision != expected.value:
                await self.edits.discard(frozenset({account_target(self._account_id).identity}))
                raise RevisionConflictError(expected, granted.revision)
            await self.edits.discard(frozenset({account_target(self._account_id).identity}))
            self._revision = granted.revision
            self._needs_consent = False
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
        fields.append(
            CardField(
                tr(t"account-view-creator-page = Creator page"),
                tr(t"account-view-hidden-2 = Hidden") if self.page_hidden else tr(t"account-view-public = Public"),
            )
        )
        return fields

    def identity_detail(self, identity: AccountIdentity) -> sl.TextLike:
        visibility = (
            tr(t"account-view-shown-publicly = shown publicly")
            if identity.is_public
            else tr(t"account-view-hidden = hidden")
        )
        age = (
            sl.md(discord.utils.format_dt(identity.verified_at.to_stdlib(), style="R"))
            if identity.verified_at is not None
            else tr(t"account-view-unknown = unknown")
        )
        return tr(t"account-view-verified = {visibility} · verified {age}")

    def _unlink_warning(self) -> sl.TextLike:
        """What the reader is agreeing to, asked before the press rather than after it."""
        identity = self.selected
        if identity is None:
            return tr(t"account-view-remove-this-linked-account = Remove this linked account?")
        identity_label = _identity_label(identity)
        warning = tr(
            t"account-linked-identity-remove-warning = Remove {identity_label}? Any build credit you hold "
            t"is unaffected."
        )
        if identity.provider is IdentityProvider.DISCORD and identity.discord_id == self._actor_id:
            consequence = tr(
                t"account-discord-unlink-warning = This is the Discord account you "
                t"are using now. The bot will stop recognising you here."
            )
            return tr(t"account-view-value = {warning} {consequence}")
        return warning

    def _footer(self) -> sl.TextLike | None:
        if self.page_hidden:
            return tr(
                t"account-hidden-page-credit-note = A hidden page still lists the "
                t"creator names you hold, because that credit is what attributes your builds."
            )
        return None
