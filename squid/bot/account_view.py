"""The panel behind `/account`.

Five commands used to answer what one screen shows: `identities` printed the linked accounts
and their ids, `unlink` took one of those ids back, `visibility` took it too — or nothing at all,
in which case it hid the whole creator page instead — and `profile` and `profile-edit` showed and
edited the card the rest of it hangs off. An identity is a thing you look at and then show, hide
or drop, so looking at it and acting on it belong to the same message (audit C5's retyping half,
the shape 5.3 and 5.4 already removed from notifications and claim review).
"""

from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import cast

import discord

import squid_layouts as sl
from squid.accounts.application import AccountService
from squid.accounts.domain import (
    MAX_BIO_LENGTH,
    MAX_DISPLAY_NAME_LENGTH,
    MAX_LINK_LABEL_LENGTH,
    MAX_LINK_URL_LENGTH,
    MAX_PROFILE_LINKS,
    MAX_PRONOUNS_LENGTH,
    AccountConsent,
    AccountIdentity,
    AccountProfile,
    IdentityProvider,
    ProfileLink,
    ProfileUpdate,
)
from squid.accounts.errors import AccountNotFoundError
from squid.bot.consent import request_consent
from squid.bot.i18n import t
from squid.bot.profile_render import identity_label, own_profile_avatar, own_profile_fields
from squid.bot.ui import DISCORD_BLUE, CardField, create_mount
from squid.core.errors import ValidationError
from squid.core.i18n import _

SESSION_SECONDS = 300

MAX_LISTED = 25
"""A select holds 25 options, and only a long merge history reaches even a handful."""


class AccountPanel(sl.Component):
    """A mounted account workspace with semantic identity actions."""

    selected_id: int | None = sl.state(None)
    unlink_armed: int | None = sl.state(None)
    closed: bool = sl.state(default=False)
    # Refreshed from the service by load(), so a snapshot would only restore them stale.
    _identities: tuple[AccountIdentity, ...] = sl.state((), persist=False)
    _needs_consent: bool = sl.state(default=False, persist=False)
    # No default: the empty profile needs this instance's account id.
    _profile: AccountProfile = sl.state(persist=False)
    _profile_editor: sl.patterns.ComponentShell[sl.patterns.EditorState] | None = sl.state(
        None, persist=False, opaque=True
    )

    def __init__(
        self,
        *,
        accounts: AccountService,
        account_id: int,
        author_id: int,
        locale: str | None = None,
        timeout: float = SESSION_SECONDS,
    ) -> None:
        self._accounts = accounts
        self._account_id = account_id
        self._author_id = author_id
        self.locale = locale
        self._timeout = timeout
        self._profile = AccountProfile.empty(account_id)
        self._profile_editor = None
        self._mount: sl.discord.Mount | None = None

    async def on_load(self) -> None:
        await self._refresh()

    async def _refresh(self) -> None:
        """Re-read the account this panel is about. Also what a write calls to show its result."""
        account = await self._accounts.get_account_by_id(self._account_id)
        if account is None:
            raise AccountNotFoundError(self._account_id)
        self._identities = account.identities
        self._needs_consent = account.needs_consent_refresh
        self._profile = await self._accounts.get_profile(self._account_id)
        if self.selected is None:
            self.selected_id = None
            self.unlink_armed = None

    @property
    def identities(self) -> tuple[AccountIdentity, ...]:
        return self._identities[:MAX_LISTED]

    @property
    def selected(self) -> AccountIdentity | None:
        return next((identity for identity in self.identities if identity.id == self.selected_id), None)

    @property
    def page_hidden(self) -> bool:
        return self._profile.hidden

    def render(self) -> tuple[sl.LayoutNode, ...]:
        if self.closed:
            # DISCORD_BLUE is house chrome, not a Tone, so this needs sl.section's accent
            # rather than sl.status's fixed tone palette.
            return (sl.section(sl.heading(t(self.locale, _("Account controls closed"))), accent=DISCORD_BLUE),)
        if self._profile_editor is not None:
            return (
                self.boundary(self._profile_editor, key="profile-editor"),
                sl.primitives.Row(
                    (
                        sl.primitives.Button(
                            t(self.locale, _("Cancel")),
                            self._cancel_profile_edit,
                            "cancel-profile-edit",
                        ),
                    )
                ),
            )
        fields = tuple(sl.field(field.name, field.value) for field in self._fields())
        footer = self._footer()
        media = own_profile_avatar(self._profile, self._identities)
        extra_media = media[1:]
        nodes: list[sl.LayoutNode] = [
            sl.section(
                sl.heading(self._profile.display_name or t(self.locale, _("Your account"))),
                # The bio is the card's shock absorber: truncate lets it give up characters
                # under pressure before the fields or footer lose any.
                self._profile.bio and sl.truncate(sl.paragraph(self._profile.bio)),
                sl.fields(*fields),
                bool(extra_media) and sl.media(*extra_media, key="media"),
                footer and sl.note(footer),
                accent=DISCORD_BLUE,
                thumbnail=media[0] if media else None,
            )
        ]
        if self.identities:
            nodes.append(
                sl.semantic.Choices(
                    key="identity",
                    choices=tuple(
                        sl.semantic.Choice(
                            str(identity.id),
                            identity_label(identity, self.locale),
                            self.identity_detail(identity),
                        )
                        for identity in self.identities
                        if identity.id is not None
                    ),
                    selection=sl.controlled(
                        (str(self.selected_id),) if self.selected_id is not None else (), self._selection_changed
                    ),
                )
            )
        identity = self.selected
        nodes.extend(
            (
                sl.toggle(
                    t(self.locale, _("Selected identity")),
                    key="identity_visibility",
                    on=sl.controlled(identity is not None and identity.is_public, self._toggle_identity),
                    on_label=t(self.locale, _("Shown on page")),
                    off_label=t(self.locale, _("Hidden from page")),
                    available=identity is not None,
                ),
                sl.toggle(
                    t(self.locale, _("Creator page")),
                    key="page_visibility",
                    on=sl.controlled(not self.page_hidden, self._toggle_page),
                    on_label=t(self.locale, _("Shown")),
                    off_label=t(self.locale, _("Hidden")),
                ),
            )
        )
        nodes.append(
            sl.primitives.Row(
                (
                    sl.primitives.Button(
                        t(self.locale, _("Unlink for good"))
                        if self.unlink_armed == self.selected_id
                        else t(self.locale, _("Unlink")),
                        self._unlink,
                        "unlink",
                        style=sl.primitives.ActionStyle.DANGER,
                        disabled=self.selected is None,
                    ),
                    sl.primitives.Button(
                        t(self.locale, _("Edit page")),
                        self._edit_page,
                        "edit_page",
                        style=sl.primitives.ActionStyle.PRIMARY,
                    ),
                    sl.primitives.Button(t(self.locale, _("Close")), self._close, "close"),
                )
            )
        )
        return tuple(nodes)

    async def _selection_changed(self, event: sl.ChoiceEvent) -> None:
        self.selected_id = int(event.selected[0])
        self.unlink_armed = None

    async def _toggle_identity(self, event: sl.ToggleEvent) -> None:
        identity = self.selected
        if identity is None or identity.id is None:
            return
        identity_id, is_public = identity.id, event.value

        async def apply() -> None:
            await self._accounts.set_identity_visibility(self._account_id, identity_id, is_public=is_public)
            await self._reload()

        await self._with_consent(event, apply)

    async def _toggle_page(self, event: sl.ToggleEvent) -> None:
        hidden = not event.value

        async def apply() -> None:
            await self._accounts.update_profile(self._account_id, ProfileUpdate(hidden=hidden))
            await self._reload()

        await self._with_consent(event, apply)

    async def _unlink(self, event: sl.PressEvent) -> None:
        identity = self.selected
        if identity is None or identity.id is None:
            return
        if self.unlink_armed != identity.id:
            self.unlink_armed = identity.id
            return
        await event.acknowledge()
        removed = await self._accounts.unlink_identity(self._account_id, identity.id)
        self.unlink_armed = None
        await self._reload()
        await event.notice(
            t(
                self.locale,
                _("Unlinked {identity}. Any build credit you hold is unaffected."),
                identity=identity_label(removed, self.locale),
            )
        )

    async def _edit_page(self, event: sl.PressEvent) -> None:
        async def apply() -> None:
            self._profile_editor = self._build_profile_editor()

        await self._with_consent(event, apply)

    def _build_profile_editor(self) -> sl.patterns.ComponentShell[sl.patterns.EditorState]:
        profile_section = sl.patterns.EditorSection.form(
            "profile",
            t(self.locale, _("Profile")),
            sl.forms.FormSpec(
                t(self.locale, _("Edit profile")),
                (
                    sl.forms.TextField(
                        key="display_name",
                        label=t(self.locale, _("Display name")),
                        required=False,
                        maximum=MAX_DISPLAY_NAME_LENGTH,
                    ),
                    sl.forms.TextField(
                        key="pronouns",
                        label=t(self.locale, _("Pronouns")),
                        required=False,
                        maximum=MAX_PRONOUNS_LENGTH,
                    ),
                    sl.forms.TextAreaField(
                        key="bio",
                        label=t(self.locale, _("Bio")),
                        required=False,
                        maximum=MAX_BIO_LENGTH,
                    ),
                ),
            ),
        )
        links = sl.patterns.CollectionEditor(
            t(self.locale, _("Links")),
            create=sl.forms.FormSpec(
                t(self.locale, _("Profile link")),
                (
                    sl.forms.TextField(
                        key="label",
                        label=t(self.locale, _("Label")),
                        maximum=MAX_LINK_LABEL_LENGTH,
                    ),
                    sl.forms.TextField(
                        key="url",
                        label=t(self.locale, _("HTTPS URL")),
                        maximum=MAX_LINK_URL_LENGTH,
                    ),
                ),
                validator=self._validate_link,
            ),
            label=lambda value: str(value["label"]),
            minimum=0,
            maximum=MAX_PROFILE_LINKS,
        )
        links_section = sl.patterns.EditorSection.pattern(
            "links",
            t(self.locale, _("Links")),
            links,
            load=lambda value: links.initial_from(cast(Iterable[Mapping[str, object]], value)),
            dump=links.values,
            summary=lambda value: t(self.locale, _("{count} links"), count=len(value)),
            issues=lambda state: (sl.forms.FormError(message) for message in links.errors(state)),
        )
        editor = sl.patterns.Editor(
            t(self.locale, _("Edit your creator page")),
            (profile_section, links_section),
            preview=self._profile_preview,
            commit_label=t(self.locale, _("Save profile")),
            validate=self._validate_profile_editor,
        )
        initial: sl.patterns.EditorValues = {
            "profile": {
                "display_name": self._profile.display_name,
                "pronouns": self._profile.pronouns,
                "bio": self._profile.bio,
            },
            "links": tuple({"label": link.label, "url": link.url} for link in self._profile.links),
        }
        return editor.component(initial=initial, on_commit=self._profile_committed)

    def _validate_link(self, values: Mapping[str, object]) -> tuple[sl.forms.FormIssue, ...]:
        try:
            ProfileLink.parse(str(values["label"]), str(values["url"]))
        except ValidationError as error:
            return (sl.forms.FormError(error.localized_public_detail(self.locale)),)
        return ()

    def _raw_profile_update(self, values: sl.patterns.EditorValues) -> ProfileUpdate:
        profile = cast(Mapping[str, object], values["profile"])
        links = cast(Iterable[Mapping[str, object]], values["links"])
        return ProfileUpdate(
            display_name=cast(str | None, profile["display_name"]),
            pronouns=cast(str | None, profile["pronouns"]),
            bio=cast(str | None, profile["bio"]),
            links=tuple(ProfileLink(str(link["label"]), str(link["url"])) for link in links),
        )

    def _profile_update(self, values: sl.patterns.EditorValues) -> ProfileUpdate:
        return self._raw_profile_update(values).validated()

    def _validate_profile_editor(self, values: sl.patterns.EditorValues) -> tuple[sl.forms.FormIssue, ...]:
        try:
            self._profile_update(values)
        except ValidationError as error:
            return (sl.forms.FormError(error.localized_public_detail(self.locale)),)
        return ()

    def _profile_preview(self, values: sl.patterns.EditorValues) -> sl.LayoutNode:
        draft = self._raw_profile_update(values).apply(self._profile)
        fields = tuple(sl.field(field.name, field.value) for field in own_profile_fields(draft, self.locale))
        return sl.section(
            sl.heading(draft.display_name or t(self.locale, _("Your account"))),
            draft.bio and sl.truncate(sl.paragraph(draft.bio)),
            sl.fields(*fields) if fields else None,
            accent=DISCORD_BLUE,
        )

    async def _profile_committed(
        self,
        event: sl.patterns.PatternEvent[sl.patterns.EditorState],
        values: sl.patterns.EditorValues,
        _changed: frozenset[str],
    ) -> None:
        await self._accounts.update_profile(self._account_id, self._profile_update(values))
        await self._refresh()
        self._profile_editor = None
        await event.source.notice(t(self.locale, _("Profile saved.")))

    async def _cancel_profile_edit(self, _event: sl.PressEvent) -> None:
        self._profile_editor = None

    async def _with_consent(self, event: sl.ActionEvent, work: Callable[[], Awaitable[None]]) -> None:
        """Run `work` now, or once the reader has agreed to be recorded.

        Opening the notice ends this press: `request_consent` returns as soon as it is on
        screen, so the panel's transaction closes and its dispatch lock is released rather
        than being held for as long as the reader takes to read. `work` then runs inside the
        prompt's own press, and the panel redraws through its own handle -- never through the
        prompt's interaction, which addresses the prompt's message rather than the panel's.
        """
        if not self._needs_consent:
            await work()
            return
        mount = sl.discord.responder(event).mount

        async def answered(_prompt: sl.PressEvent, consent: AccountConsent | None) -> None:
            if consent is None:
                # Cancelled. The notice said agreeing is what stores anything, and the prompt
                # closing is the whole answer; the panel already shows the unchanged truth.
                return
            await self._accounts.grant_current_consent(self._account_id)
            self._needs_consent = False
            await work()
            await mount.refresh()

        await request_consent(
            sl.discord.native(event),
            user_id=self._author_id,
            on_answer=answered,
            locale=self.locale,
            parent=mount,
        )

    async def _reload(self) -> None:
        await self._refresh()
        self.invalidate()

    async def _close(self, event: sl.PressEvent) -> None:
        self.closed = True
        await event.finish()

    def _fields(self) -> list[CardField]:
        fields = own_profile_fields(self._profile, self.locale)
        fields += [
            CardField(identity_label(identity, self.locale), self.identity_detail(identity))
            for identity in self.identities
        ]
        if not fields:
            fields.append(CardField(t(self.locale, _("Linked accounts")), t(self.locale, _("_None yet._"))))
        fields.append(
            CardField(
                t(self.locale, _("Creator page")),
                t(self.locale, _("Hidden")) if self.page_hidden else t(self.locale, _("Public")),
            )
        )
        return fields

    def identity_detail(self, identity: AccountIdentity) -> str:
        return t(
            self.locale,
            _("{visibility} · verified {age}"),
            visibility=(t(self.locale, _("shown publicly")) if identity.is_public else t(self.locale, _("hidden"))),
            age=(
                discord.utils.format_dt(identity.verified_at.to_stdlib(), style="R")
                if identity.verified_at is not None
                else t(self.locale, _("unknown"))
            ),
        )

    def _footer(self) -> str | None:
        identity = self.selected
        if self.unlink_armed == self.selected_id and identity is not None:
            warning = t(
                self.locale,
                _("Click **Unlink** again to remove {identity}."),
                identity=identity_label(identity, self.locale),
            )
            if identity.provider is IdentityProvider.DISCORD and identity.discord_id == self._author_id:
                warning += " " + t(
                    self.locale,
                    _("This is the Discord account you are using now. The bot will stop recognising you here."),
                )
            return warning
        if self.page_hidden:
            return t(
                self.locale,
                "A hidden page still lists the creator names you hold, because that credit is what attributes your builds.",
            )
        return None

    def mount(self) -> sl.discord.Mount:
        self._mount = create_mount(
            self,
            access=sl.discord.Owner(self._author_id),
            locale=self.locale,
            timeout=self._timeout,
        )
        return self._mount
