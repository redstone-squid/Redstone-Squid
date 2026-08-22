"""The panel behind `/account`.

Five commands used to answer what one screen shows: `identities` printed the linked accounts
and their ids, `unlink` took one of those ids back, `visibility` took it too — or nothing at all,
in which case it hid the whole creator page instead — and `profile` and `profile-edit` showed and
edited the card the rest of it hangs off. An identity is a thing you look at and then show, hide
or drop, so looking at it and acting on it belong to the same message (audit C5's retyping half,
the shape 5.3 and 5.4 already removed from notifications and claim review).
"""

from typing import Any, override

import discord

import squid_layouts as sl
from squid.accounts.application import AccountService
from squid.accounts.domain import (
    MAX_BIO_LENGTH,
    MAX_DISPLAY_NAME_LENGTH,
    MAX_PRONOUNS_LENGTH,
    AccountIdentity,
    AccountProfile,
    IdentityProvider,
    ProfileLink,
    ProfileUpdate,
)
from squid.accounts.errors import AccountNotFoundError
from squid.bot.consent import NOT_ASKED, prompt_for_consent
from squid.bot.errors import ErrorHandledModal
from squid.bot.i18n import t
from squid.bot.profile_render import identity_label, own_profile_avatar, own_profile_fields
from squid.bot.ui import create_mount
from squid.bot.utils.components import (
    DISCORD_BLUE,
    CardField,
)
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
            return (sl.section(sl.paragraph(t(self.locale, _("Account controls closed"))), accent=DISCORD_BLUE),)
        fields = tuple(sl.field(field.name, field.value) for field in self._fields())
        footer = self._footer()
        media = own_profile_avatar(self._profile, self._identities)
        extra_media = media[1:]
        nodes: list[sl.LayoutNode] = [
            sl.section(
                # The bio is the card's shock absorber: truncate lets it give up characters
                # under pressure before the fields or footer lose any.
                self._profile.bio and sl.truncate(sl.paragraph(self._profile.bio)),
                sl.fields(*fields),
                bool(extra_media) and sl.media(*extra_media, key="media"),
                footer and sl.note(footer),
                heading=self._profile.display_name or t(self.locale, _("Your account")),
                accent=DISCORD_BLUE,
                thumbnail=media[0] if media else None,
            )
        ]
        if self.identities:
            nodes.append(
                sl.Choices(
                    key="identity",
                    choices=tuple(
                        sl.Choice(
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
        if identity is None or identity.id is None or not await self._consented(event):
            return
        await self._accounts.set_identity_visibility(
            self._account_id,
            identity.id,
            is_public=event.value,
        )
        await self._reload()

    async def _toggle_page(self, event: sl.ToggleEvent) -> None:
        if not await self._consented(event):
            return
        await self._accounts.update_profile(self._account_id, ProfileUpdate(hidden=not event.value))
        await self._reload()

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
        interaction = sl.discord.native(event)
        if self._needs_consent:
            consent = await prompt_for_consent(
                interaction,
                user_id=self._author_id,
                locale=self.locale,
                parent=sl.discord.responder(event).mount,
            )
            if consent is NOT_ASKED:
                return
            if consent is None:
                await event.notice(t(self.locale, _("Cancelled. Nothing was changed.")))
                return
            await self._accounts.grant_current_consent(self._account_id)
            self._needs_consent = False
            await event.notice(t(self.locale, _("Thanks. Press **Edit page** again to open the editor.")))
            return
        await sl.discord.responder(event).send_modal(ProfileEditModal(self, self._profile, locale=self.locale))

    async def save_profile(self, interaction: discord.Interaction[Any], update: ProfileUpdate) -> None:
        await self._accounts.update_profile(self._account_id, update)
        await self._refresh()
        self.invalidate()
        if self._mount is None:
            return
        await self._mount.flush(interaction)

    async def _consented(self, event: sl.ActionEvent) -> bool:
        await event.acknowledge()
        if not self._needs_consent:
            return True
        consent = await prompt_for_consent(
            sl.discord.native(event),
            user_id=self._author_id,
            locale=self.locale,
            parent=sl.discord.responder(event).mount,
        )
        if consent is NOT_ASKED:
            return False
        if consent is None:
            await event.notice(t(self.locale, _("Cancelled. Nothing was changed.")))
            return False
        await self._accounts.grant_current_consent(self._account_id)
        self._needs_consent = False
        return True

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
            locale=self.locale,
            timeout=self._timeout,
            lock_to=self._author_id,
        )
        return self._mount


class ProfileEditModal(ErrorHandledModal):
    """Edit the free-text parts of a creator page.

    Links are one `label | url` per line rather than a repeated field because a modal allows five
    inputs total; the same domain validator parses them, so the bot and the API agree on what a
    valid link is.
    """

    def __init__(self, panel: AccountPanel, profile: AccountProfile, *, locale: str | None) -> None:
        super().__init__(title=t(locale, _("Edit your creator page")))
        self._panel = panel
        self._locale = locale
        self.display_name = discord.ui.TextInput(
            label=t(locale, _("Display name")),
            required=False,
            max_length=MAX_DISPLAY_NAME_LENGTH,
            default=profile.display_name,
        )
        self.pronouns = discord.ui.TextInput(
            label=t(locale, _("Pronouns")),
            required=False,
            max_length=MAX_PRONOUNS_LENGTH,
            default=profile.pronouns,
        )
        self.bio = discord.ui.TextInput(
            label=t(locale, _("Bio")),
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=MAX_BIO_LENGTH,
            default=profile.bio,
        )
        self.links = discord.ui.TextInput(
            label=t(locale, _("Links (one per line: Label | https://...)")),
            style=discord.TextStyle.paragraph,
            required=False,
            default="\n".join(f"{link.label} | {link.url}" for link in profile.links),
        )
        for item in (self.display_name, self.pronouns, self.bio, self.links):
            self.add_item(item)

    @override
    async def on_submit(self, interaction: discord.Interaction) -> None:
        update = ProfileUpdate(
            display_name=self.display_name.value or None,
            pronouns=self.pronouns.value or None,
            bio=self.bio.value or None,
            links=_parse_link_lines(self.links.value, self._locale),
        )
        await self._panel.save_profile(interaction, update)


def _parse_link_lines(raw: str, locale: str | None) -> tuple[ProfileLink, ...]:
    """Parse `Label | https://...` lines into links, refusing a line that is not one.

    Parsing only splits; `ProfileUpdate.validated` in the service is what accepts or rejects the
    URL itself, so the modal cannot end up with a laxer idea of a valid link than the API.
    """
    links: list[ProfileLink] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        label, separator, url = line.partition("|")
        if not separator:
            raise ValidationError(
                t(locale, _("Each link needs a label and a URL separated by `|`, got {line!r}.")),
                message_params={"line": line.strip()},
            )
        links.append(ProfileLink(label=label.strip(), url=url.strip()))
    return tuple(links)
