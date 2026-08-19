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
from squid.bot.consent import prompt_for_consent
from squid.bot.errors import ErrorHandledModal, ExpiringLayoutView
from squid.bot.i18n import t
from squid.bot.profile_render import identity_label, own_profile_avatar, own_profile_fields
from squid.bot.utils.components import (
    DISCORD_BLUE,
    CardField,
    card_container,
    edit_interaction_layout,
    no_mentions,
    reply_layout,
    text_layout,
)
from squid.core.errors import ValidationError
from squid.core.i18n import _

SESSION_SECONDS = 300

MAX_LISTED = 25
"""A select holds 25 options, and only a long merge history reaches even a handful."""


class AccountPanelView(ExpiringLayoutView):
    """Every linked account on one screen, with the controls that used to be three commands.

    Holds the service rather than a snapshot, like the settings and notification panels: the
    panel exists to write, and every write has to show its result.
    """

    def __init__(
        self,
        *,
        accounts: AccountService,
        account_id: int,
        author_id: int,
        locale: str | None = None,
        timeout: float = SESSION_SECONDS,
    ) -> None:
        super().__init__(timeout=timeout)
        self._accounts = accounts
        self._account_id = account_id
        self._author_id = author_id
        self.locale = locale
        self._identities: tuple[AccountIdentity, ...] = ()
        self._profile = AccountProfile.empty(account_id)
        self._needs_consent = False
        self._selected_id: int | None = None
        self._unlink_armed: int | None = None

    async def load(self) -> None:
        """Re-read the account this panel is about, then render it."""
        account = await self._accounts.get_account_by_id(self._account_id)
        if account is None:
            raise AccountNotFoundError(self._account_id)
        self._identities = account.identities
        self._needs_consent = account.needs_consent_refresh
        self._profile = await self._accounts.get_profile(self._account_id)
        if not any(identity.id == self._selected_id for identity in self.identities):
            self._selected_id = None
            self._unlink_armed = None
        self.render()

    @override
    async def interaction_check(self, interaction: discord.Interaction[discord.Client], /) -> bool:
        if interaction.user.id == self._author_id:
            return True
        await interaction.response.send_message(
            t(self.locale, _("These account controls belong to someone else.")),
            ephemeral=True,
            allowed_mentions=no_mentions(),
        )
        return False

    @property
    def identities(self) -> tuple[AccountIdentity, ...]:
        """The listed identities, capped at what one select can offer."""
        return self._identities[:MAX_LISTED]

    @property
    def selected(self) -> AccountIdentity | None:
        """The identity the buttons act on."""
        return next((identity for identity in self.identities if identity.id == self._selected_id), None)

    @property
    def selected_id(self) -> int | None:
        """Which identity the select shows as picked."""
        return self._selected_id

    @property
    def page_hidden(self) -> bool:
        """Whether the creator page is withheld from everyone but its owner."""
        return self._profile.hidden

    @property
    def unlink_armed(self) -> bool:
        """Whether unlinking is asking for the second click."""
        return self._unlink_armed is not None and self._unlink_armed == self._selected_id

    def render(self) -> None:
        self.clear_items()
        self.add_item(
            card_container(
                self._profile.display_name or t(self.locale, _("Your account")),
                self._profile.bio,
                accent_colour=DISCORD_BLUE,
                fields=self._fields(),
                footer=self._footer(),
                media=own_profile_avatar(self._profile, self._identities),
            )
        )
        if self.identities:
            self.add_item(discord.ui.ActionRow(IdentitySelect(self)))
            self.add_item(discord.ui.ActionRow(IdentityVisibilityButton(self), UnlinkIdentityButton(self)))
        self.add_item(discord.ui.ActionRow(EditPageButton(self), PageVisibilityButton(self), ClosePanelButton(self)))

    def select(self, identity_id: int | None) -> None:
        """Point the buttons at an identity, disarming an unlink aimed at a different one."""
        if identity_id != self._selected_id:
            self._unlink_armed = None
        self._selected_id = identity_id
        self.render()

    async def toggle_identity(self, interaction: discord.Interaction[Any]) -> None:
        """Show or hide the picked account on the creator page."""
        identity = self.selected
        if identity is None or identity.id is None:
            return
        if not await self._consented(interaction):
            return
        await self._accounts.set_identity_visibility(self._account_id, identity.id, is_public=not identity.is_public)
        await self._reload(interaction)

    async def toggle_page(self, interaction: discord.Interaction[Any]) -> None:
        """Publish or withhold the creator page as a whole."""
        if not await self._consented(interaction):
            return
        await self._accounts.update_profile(self._account_id, ProfileUpdate(hidden=not self.page_hidden))
        await self._reload(interaction)

    async def unlink(self, interaction: discord.Interaction[Any]) -> None:
        """Drop the picked identity, asking once before it goes.

        Unlinking is the one control here that cannot be clicked back, so it arms rather than
        acts — the same second-click shape 5.4 gave a claim transfer, instead of a confirmation
        view that would replace the panel it was launched from.
        """
        identity = self.selected
        if identity is None or identity.id is None:
            return
        if not self.unlink_armed:
            self._unlink_armed = identity.id
            self.render()
            await edit_interaction_layout(interaction, self)
            return

        # No consent gate: unlinking stores nothing new, and someone withdrawing an identity is
        # the last person who should be asked to accept a notice first.
        await interaction.response.defer()
        removed = await self._accounts.unlink_identity(self._account_id, identity.id)
        await self._reload(interaction)
        await reply_layout(
            interaction,
            text_layout(
                t(
                    self.locale,
                    _("Unlinked {identity}. Any build credit you hold is unaffected."),
                    identity=identity_label(removed, self.locale),
                )
            ),
        )

    async def edit_page(self, interaction: discord.Interaction[Any]) -> None:
        """Open the page editor on this interaction, which a modal needs unspent.

        Showing the notice spends it, so a caller who has yet to accept the current one is asked
        here and pressed the button again afterwards. `profile-edit` had the same two-step, except
        that step two was retyping the command.
        """
        if self._needs_consent:
            consent = await prompt_for_consent(interaction, user_id=self._author_id, locale=self.locale)
            if consent is None:
                await reply_layout(interaction, text_layout(t(self.locale, _("Cancelled. Nothing was changed."))))
                return
            await self._accounts.grant_current_consent(self._account_id)
            self._needs_consent = False
            await reply_layout(
                interaction,
                text_layout(t(self.locale, _("Thanks. Press **Edit page** again to open the editor."))),
            )
            return
        await interaction.response.send_modal(ProfileEditModal(self, self._profile, locale=self.locale))

    async def save_profile(self, interaction: discord.Interaction[Any], update: ProfileUpdate) -> None:
        """Write what the editor collected and show the page it produced."""
        await self._accounts.update_profile(self._account_id, update)
        await self._reload(interaction)
        await reply_layout(interaction, text_layout(t(self.locale, _("Your creator page has been updated."))))

    def disable_controls(self) -> None:
        for child in self.walk_children():
            if isinstance(child, discord.ui.Button | discord.ui.Select):
                child.disabled = True
        self.stop()

    async def _consented(self, interaction: discord.Interaction[Any]) -> bool:
        """Make sure the notice covering a published change has been accepted.

        Deferred first so the prompt and the panel's own redraw fit in one click. The receipt is
        granted to the account the panel is about rather than to whoever `get_or_create_identity`
        would find, which after unlinking your own Discord identity is nobody.
        """
        await interaction.response.defer()
        if not self._needs_consent:
            return True
        consent = await prompt_for_consent(interaction, user_id=self._author_id, locale=self.locale)
        if consent is None:
            await reply_layout(interaction, text_layout(t(self.locale, _("Cancelled. Nothing was changed."))))
            return False
        await self._accounts.grant_current_consent(self._account_id)
        self._needs_consent = False
        return True

    async def _reload(self, interaction: discord.Interaction[Any]) -> None:
        await self.load()
        await edit_interaction_layout(interaction, self)

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
        """What the old list said, minus the id nobody has to type any more."""
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
        """Whatever the next click needs said first."""
        identity = self.selected
        if self.unlink_armed and identity is not None:
            warning = t(
                self.locale,
                _("Click **Unlink** again to remove {identity}."),
                identity=identity_label(identity, self.locale),
            )
            if identity.provider is IdentityProvider.DISCORD and identity.discord_id == self._author_id:
                # Legal but startling, so it is said before the click rather than after.
                warning += " " + t(
                    self.locale,
                    _("This is the Discord account you are using now. The bot will stop recognising you here."),
                )
            return warning
        if self.page_hidden:
            return t(
                self.locale,
                _(
                    "A hidden page still lists the creator names you hold, because that credit is "
                    "what attributes your builds."
                ),
            )
        return None


class IdentitySelect(discord.ui.Select[AccountPanelView]):
    """Pick the identity the buttons act on, instead of reading its id off a card."""

    def __init__(self, view: AccountPanelView) -> None:
        options = [
            discord.SelectOption(
                label=identity_label(identity, view.locale)[:100],
                value=str(identity.id),
                description=view.identity_detail(identity)[:100],
                default=identity.id == view.selected_id,
            )
            for identity in view.identities
            if identity.id is not None
        ]
        super().__init__(placeholder=t(view.locale, _("Pick a linked account…")), options=options)
        self._panel = view

    @override
    async def callback(self, interaction: discord.Interaction[discord.Client]) -> None:
        self._panel.select(int(self.values[0]))
        await edit_interaction_layout(interaction, self._panel)


class IdentityVisibilityButton(discord.ui.Button[AccountPanelView]):
    """Show or hide the picked account on the creator page."""

    def __init__(self, view: AccountPanelView) -> None:
        identity = view.selected
        super().__init__(
            label=(
                t(view.locale, _("Hide from page"))
                if identity is not None and identity.is_public
                else t(view.locale, _("Show on page"))
            ),
            style=discord.ButtonStyle.secondary,
            disabled=identity is None,
        )
        self._panel = view

    @override
    async def callback(self, interaction: discord.Interaction[discord.Client]) -> None:
        await self._panel.toggle_identity(interaction)


class UnlinkIdentityButton(discord.ui.Button[AccountPanelView]):
    """Remove the picked account, on the second click."""

    def __init__(self, view: AccountPanelView) -> None:
        armed = view.unlink_armed
        super().__init__(
            label=t(view.locale, _("Unlink for good")) if armed else t(view.locale, _("Unlink")),
            style=discord.ButtonStyle.danger,
            disabled=view.selected is None,
        )
        self._panel = view

    @override
    async def callback(self, interaction: discord.Interaction[discord.Client]) -> None:
        await self._panel.unlink(interaction)


class EditPageButton(discord.ui.Button[AccountPanelView]):
    """Open the editor for the free-text half of the page."""

    def __init__(self, view: AccountPanelView) -> None:
        super().__init__(label=t(view.locale, _("Edit page")), style=discord.ButtonStyle.primary)
        self._panel = view

    @override
    async def callback(self, interaction: discord.Interaction[discord.Client]) -> None:
        await self._panel.edit_page(interaction)


class PageVisibilityButton(discord.ui.Button[AccountPanelView]):
    """Publish or withhold the whole creator page."""

    def __init__(self, view: AccountPanelView) -> None:
        super().__init__(
            label=t(view.locale, _("Show my page")) if view.page_hidden else t(view.locale, _("Hide my page")),
            style=discord.ButtonStyle.secondary,
        )
        self._panel = view

    @override
    async def callback(self, interaction: discord.Interaction[discord.Client]) -> None:
        await self._panel.toggle_page(interaction)


class ClosePanelButton(discord.ui.Button[AccountPanelView]):
    def __init__(self, view: AccountPanelView) -> None:
        super().__init__(label=t(view.locale, _("Close")), style=discord.ButtonStyle.secondary)
        self._panel = view

    @override
    async def callback(self, interaction: discord.Interaction[discord.Client]) -> None:
        self._panel.disable_controls()
        await edit_interaction_layout(interaction, self._panel)


class ProfileEditModal(ErrorHandledModal):
    """Edit the free-text parts of a creator page.

    Links are one `label | url` per line rather than a repeated field because a modal allows five
    inputs total; the same domain validator parses them, so the bot and the API agree on what a
    valid link is.
    """

    def __init__(self, panel: AccountPanelView, profile: AccountProfile, *, locale: str | None) -> None:
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
