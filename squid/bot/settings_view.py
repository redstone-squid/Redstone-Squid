"""Interactive Components V2 rendering for server settings."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast, override

import discord

import squid_layouts as sl
from squid.bot.errors import ErrorHandledModal, ExpiringLayoutView
from squid.bot.i18n import t
from squid.bot.ui import create_mount
from squid.bot.utils.components import CardField, card_container, edit_interaction_layout, no_mentions
from squid.bot.utils.permissions import allows
from squid.core.i18n import SUPPORTED_LOCALES, _
from squid.permissions.domain import PermissionNode
from squid.permissions.domain.catalogue import SETTINGS_SERVER_EDIT, SETTINGS_VOTING_EDIT
from squid.settings.domain import ScalarChannelSetting
from squid.voting.domain import EmojiPreset, RoleWeight, VoteChoice, VoteKind, VoteOption
from squid.voting.errors import InvalidVoteConfigurationError

if TYPE_CHECKING:
    from squid.settings.application import SettingsService
    from squid.voting.application import VoteService

SESSION_SECONDS = 300

FOLLOW_DISCORD = "-"
"""The locale select's "no override" value; an empty select value is not sendable."""

CHANNEL_SETTINGS: tuple[ScalarChannelSetting, ...] = ("Smallest", "Fastest", "First", "Builds", "Vote")
"""Every channel setting, in the order the panel stacks their pickers."""

CHANNEL_TYPES = [
    discord.ChannelType.text,
    discord.ChannelType.news,
    discord.ChannelType.voice,
    discord.ChannelType.stage_voice,
    discord.ChannelType.public_thread,
    discord.ChannelType.private_thread,
    discord.ChannelType.news_thread,
]
"""What `GuildMessageable` admits, as channel types a picker can offer."""

SETTING_LABELS: dict[ScalarChannelSetting, str] = {
    "Smallest": _("Smallest-record builds"),
    "Fastest": _("Fastest-record builds"),
    "First": _("First-of-a-kind builds"),
    "Builds": _("Confirmed builds"),
    "Vote": _("Builds awaiting review"),
}
"""What each channel setting is for, named rather than title-cased so it translates."""

KIND_LABELS: dict[VoteKind, str] = {
    VoteKind.BUILD: _("Build reviews"),
    VoteKind.DELETE_LOG: _("Deletion votes"),
    VoteKind.GENERIC: _("Polls"),
}


@dataclass(frozen=True, slots=True)
class SettingsCapabilities:
    """What the caller may do, resolved once when the panel opens.

    The panel is author-locked and expires, but a grant can still be revoked while it
    sits open, so the write paths re-check rather than trusting these.
    """

    view_server: bool
    edit_server: bool
    edit_voting: bool


class SettingsPanelView(ExpiringLayoutView):
    """Every server setting on one screen, with a control per key instead of a command per key.

    Two pages, because the controls do not fit one: channels plus language, and voting. The
    view holds the services rather than a snapshot — unlike a search page, a settings panel is
    a workspace whose whole purpose is to write, and each write has to show its result.
    """

    def __init__(
        self,
        *,
        settings: SettingsService,
        votes: VoteService,
        guild: discord.Guild,
        author_id: int,
        capabilities: SettingsCapabilities,
        locale: str | None = None,
        owner_guild_id: int | None = None,
    ) -> None:
        super().__init__(timeout=SESSION_SECONDS)
        self._settings = settings
        self._votes = votes
        self._guild = guild
        self._author_id = author_id
        self._capabilities = capabilities
        self.locale = locale
        self._owner_guild_id = owner_guild_id
        self._channels: dict[ScalarChannelSetting, int | None] = dict.fromkeys(CHANNEL_SETTINGS)
        self._locale_override: str | None = None
        self._kind = VoteKind.BUILD
        self._preset: EmojiPreset | None = None
        self._weights: tuple[RoleWeight, ...] = ()
        self._confirm_reset = False

    async def load(self) -> None:
        """Read this server's settings and render the page the caller may see."""
        if self.shows_server:
            await self.open_server()
        else:
            await self.open_voting()

    @override
    async def interaction_check(self, interaction: discord.Interaction[discord.Client], /) -> bool:
        if interaction.user.id == self._author_id:
            return True
        await interaction.response.send_message(
            t(self.locale, _("These settings controls belong to someone else.")),
            ephemeral=True,
            allowed_mentions=no_mentions(),
        )
        return False

    @property
    def shows_server(self) -> bool:
        """Whether the caller may see the channels-and-language page."""
        return self._capabilities.view_server or self._capabilities.edit_server

    @property
    def shows_voting(self) -> bool:
        """Whether the caller may see the voting page."""
        return self._capabilities.view_server or self._capabilities.edit_voting

    @property
    def kind(self) -> VoteKind:
        """The session kind the voting page is showing."""
        return self._kind

    @property
    def locale_override(self) -> str | None:
        """The language this server pins the bot to, if any."""
        return self._locale_override

    @property
    def confirming_reset(self) -> bool:
        """Whether the reset button is armed and asking for a second click."""
        return self._confirm_reset

    def channel_id(self, setting: ScalarChannelSetting) -> int | None:
        """The channel a setting currently points at."""
        return self._channels[setting]

    def emoji_preset_text(self) -> str:
        """The displayed kind's preset, in the `choice | emoji` form its editor takes."""
        preset = self._preset
        return "\n".join(f"{option.choice.value} | {option.emoji}" for option in preset.options) if preset else ""

    def weight_for(self, role_id: int) -> float | None:
        """A role's multiplier for the displayed kind, or None when it has no weight."""
        return next((weight.multiplier for weight in self._weights if weight.role_id == role_id), None)

    async def open_server(self) -> None:
        """Read and show the channels-and-language page."""
        stored = cast(Mapping[str, int | None], await self._settings.get_all(self._guild.id))
        self._channels = {setting: stored.get(setting) for setting in CHANNEL_SETTINGS}
        self._locale_override = await self._settings.get_locale(self._guild.id)
        self.render_server()

    async def open_voting(self, kind: VoteKind | None = None) -> None:
        """Read and show the voting page for a session kind."""
        if kind is not None:
            self._kind = kind
        self._preset = await self._votes.emoji_preset(self._guild.id, self._kind)
        self._weights = tuple(await self._votes.get_role_weights(self._guild.id, self._kind))
        self._confirm_reset = False
        self.render_voting()

    def render_server(self) -> None:
        """Render every channel setting and the language, with a picker for each."""
        self.clear_items()
        self.add_item(
            card_container(
                t(self.locale, _("Server settings")),
                t(self.locale, _("Change as many as you like; an emptied picker clears that setting."))
                if self._capabilities.edit_server
                else None,
                fields=self._server_fields(),
            )
        )
        if self._capabilities.edit_server:
            for setting in CHANNEL_SETTINGS:
                self.add_item(discord.ui.ActionRow(SettingChannelSelect(cast(Any, self), setting)))
            self.add_item(discord.ui.ActionRow(LocaleSelect(cast(Any, self))))
        row = discord.ui.ActionRow()
        if self.shows_voting:
            row.add_item(VotingPageButton(cast(Any, self)))
        row.add_item(ClosePanelButton(cast(Any, self)))
        self.add_item(row)

    def render_voting(self) -> None:
        """Render the displayed kind's emojis and role multipliers."""
        self.clear_items()
        self.add_item(
            card_container(
                t(self.locale, _("Voting — {kind}"), kind=t(self.locale, KIND_LABELS[self._kind])),
                None,
                fields=self._voting_fields(),
                footer=self._scope_note(),
            )
        )
        self.add_item(discord.ui.ActionRow(VoteKindSelect(cast(Any, self))))
        if self._capabilities.edit_voting:
            self.add_item(discord.ui.ActionRow(RoleWeightSelect(cast(Any, self))))
        row = discord.ui.ActionRow()
        if self._capabilities.edit_voting:
            row.add_item(EditEmojisButton(cast(Any, self)))
            row.add_item(ResetVotingButton(cast(Any, self)))
        if self.shows_server:
            row.add_item(ServerPageButton(cast(Any, self)))
        row.add_item(ClosePanelButton(cast(Any, self)))
        self.add_item(row)

    async def set_channel(self, setting: ScalarChannelSetting, channel_id: int | None) -> None:
        """Point one setting at a channel, or clear it, and show the result."""
        if channel_id is None:
            await self._settings.clear(self._guild.id, setting)
        else:
            await self._settings.set_channel(self._guild.id, setting, channel_id)
        self._channels[setting] = channel_id
        self.render_server()

    async def set_locale(self, locale: str | None) -> None:
        """Pin the bot's language for this server, or follow Discord's again."""
        await self._settings.set_locale(self._guild.id, locale)
        self._locale_override = locale
        # Clearing the override hands the choice back to Discord's locale negotiation, which
        # this panel cannot redo mid-session, so the current language stays until it reopens.
        self.locale = locale or self.locale
        self.render_server()

    async def set_weight(self, role_id: int, multiplier: float | None) -> None:
        """Set or remove one role's multiplier for the displayed kind."""
        if multiplier is None:
            await self._votes.remove_role_weight(self._guild.id, self._kind, role_id)
        else:
            await self._votes.set_role_weight(RoleWeight(self._guild.id, self._kind, role_id, multiplier))
        await self.open_voting()

    async def set_emojis(self, options: Sequence[VoteOption]) -> None:
        """Replace the displayed kind's emoji preset."""
        await self._votes.set_emoji_preset(self._guild.id, self._kind, options)
        await self.open_voting()

    def arm_reset(self) -> None:
        """Ask for a second click before discarding a kind's voting configuration."""
        self._confirm_reset = True
        self.render_voting()

    async def reset_voting(self) -> None:
        """Discard the displayed kind's emojis and multipliers."""
        await self._votes.reset_configuration(self._guild.id, self._kind)
        await self.open_voting()

    async def may(self, interaction: discord.Interaction[discord.Client], node: PermissionNode) -> bool:
        """Whether the caller still holds a node, answering them when they do not.

        Controls are only rendered for capabilities the caller had when the panel opened; this
        catches the grant revoked while it sat there.
        """
        if await allows(cast(Any, interaction), node):
            return True
        await interaction.response.send_message(
            t(self.locale, _("You are no longer allowed to change this.")),
            ephemeral=True,
            allowed_mentions=no_mentions(),
        )
        return False

    def disable_controls(self) -> None:
        """Disable every interactive component."""
        for child in self.walk_children():
            if isinstance(child, discord.ui.Button | discord.ui.Select):
                child.disabled = True
        self.stop()

    def _server_fields(self) -> list[CardField]:
        fields = [
            CardField(t(self.locale, SETTING_LABELS[setting]), self._channel_display(self._channels[setting]))
            for setting in CHANNEL_SETTINGS
        ]
        language = (
            self._locale_override
            if self._locale_override is not None
            else t(self.locale, _("Following this server's Discord language"))
        )
        fields.append(CardField(t(self.locale, _("Bot language")), language))
        return fields

    def _channel_display(self, channel_id: int | None) -> str:
        if channel_id is None:
            return t(self.locale, _("_Not set_"))
        if self._guild.get_channel_or_thread(channel_id) is None:
            return t(self.locale, _("_Not found_ (`{id}`)"), id=channel_id)
        return f"<#{channel_id}>"

    def _voting_fields(self) -> list[CardField]:
        preset = self._preset
        emojis = (
            "\n".join(f"{option.emoji} — {option.choice.value}" for option in preset.options)
            if preset is not None and preset.options
            else t(self.locale, _("_None_"))
        )
        weights = "\n".join(
            f"{self._role_display(weight.role_id)} — {weight.multiplier:g}x" for weight in self._weights
        ) or t(self.locale, _("_None_"))
        return [
            CardField(t(self.locale, _("Emojis")), emojis),
            CardField(t(self.locale, _("Role multipliers")), weights),
        ]

    def _role_display(self, role_id: int) -> str:
        """A role by name.

        Deliberately not a mention: an ephemeral panel renders `<@&id>` as plain text whenever
        the role is not in the client's cache, which is what the audit found in `voting show`.
        """
        role = self._guild.get_role(role_id)
        if role is None:
            return t(self.locale, _("_Deleted role_ (`{id}`)"), id=role_id)
        return role.name

    def _scope_note(self) -> str | None:
        """Warn when this server's multipliers bind nothing it can see."""
        if self._kind is not VoteKind.BUILD or self._owner_guild_id in (None, self._guild.id):
            return None
        return t(
            self.locale,
            _("Build reviews are weighted by the network's own server, so these multipliers do not apply here."),
        )


class SettingsPanel(sl.Component):
    """A semantic, mount-owned settings workspace.

    Discord channel and role pickers are represented as semantic choices so the layout planner
    can page them when a guild has more than one legal select can hold. Text-entry operations
    still use the Discord modal adapter, the one native form boundary in squid-layouts.
    """

    page: str = sl.state("server")
    kind: VoteKind = sl.state(VoteKind.BUILD)
    confirming_reset: bool = sl.state(default=False)
    locale: str | None = sl.state(None, persist=False)
    # Refreshed from the services by open_server/open_voting, so a snapshot would only restore
    # them stale.
    _channels: dict[ScalarChannelSetting, int | None] = sl.state(dict.fromkeys(CHANNEL_SETTINGS), persist=False)
    _locale_override: str | None = sl.state(None, persist=False)
    _preset: EmojiPreset | None = sl.state(None, persist=False)
    _weights: tuple[RoleWeight, ...] = sl.state((), persist=False)

    def __init__(
        self,
        *,
        settings: SettingsService,
        votes: VoteService,
        guild: discord.Guild,
        author_id: int,
        capabilities: SettingsCapabilities,
        locale: str | None = None,
        owner_guild_id: int | None = None,
    ) -> None:
        self._settings = settings
        self._votes = votes
        self._guild = guild
        self._author_id = author_id
        self._capabilities = capabilities
        self.locale = locale
        self._owner_guild_id = owner_guild_id
        self._compat_mount: sl.discord.Mount | None = None
        self._compat_disabled = False
        self._bound_message: discord.Message | None = None

    @property
    def shows_server(self) -> bool:
        return self._capabilities.view_server or self._capabilities.edit_server

    @property
    def shows_voting(self) -> bool:
        return self._capabilities.view_server or self._capabilities.edit_voting

    @property
    def locale_override(self) -> str | None:
        return self._locale_override

    def channel_id(self, setting: ScalarChannelSetting) -> int | None:
        return self._channels[setting]

    def emoji_preset_text(self) -> str:
        return (
            "\n".join(f"{option.choice.value} | {option.emoji}" for option in self._preset.options)
            if self._preset
            else ""
        )

    def weight_for(self, role_id: int) -> float | None:
        return next((weight.multiplier for weight in self._weights if weight.role_id == role_id), None)

    async def load(self) -> None:
        """Load the first page allowed by the caller."""
        if self.shows_server:
            await self.open_server()
        else:
            await self.open_voting()

    async def open_server(self) -> None:
        stored = cast(Mapping[str, int | None], await self._settings.get_all(self._guild.id))
        self._channels = {setting: stored.get(setting) for setting in CHANNEL_SETTINGS}
        self._locale_override = await self._settings.get_locale(self._guild.id)
        self.page = "server"

    async def open_voting(self, kind: VoteKind | None = None) -> None:
        if kind is not None:
            self.kind = kind
        self._preset = await self._votes.emoji_preset(self._guild.id, self.kind)
        self._weights = tuple(await self._votes.get_role_weights(self._guild.id, self.kind))
        self.confirming_reset = False
        self.page = "voting"

    def render(self) -> Sequence[sl.LayoutNode]:
        if self.page == "voting":
            return self._voting_nodes()
        return self._server_nodes()

    def _server_nodes(self) -> Sequence[sl.LayoutNode]:
        description = (
            t(self.locale, _("Change as many as you like; an emptied picker clears that setting."))
            if self._capabilities.edit_server
            else None
        )
        children: list[sl.LayoutNode] = [
            sl.section(
                description and sl.truncate(sl.paragraph(description)),
                sl.fields(*(sl.field(field.name, field.value) for field in self._server_fields())),
                heading=t(self.locale, _("Server settings")),
            )
        ]
        if self._capabilities.edit_server:
            children.extend(
                sl.Choices(
                    key=f"channel-{setting}",
                    choices=self._channel_choices(setting),
                    selection=sl.controlled(
                        (str(self.channel_id(setting)) if self.channel_id(setting) is not None else "clear",),
                        lambda event, setting=setting: self._channel_changed(
                            cast(ScalarChannelSetting, setting), event
                        ),
                    ),
                )
                for setting in CHANNEL_SETTINGS
            )
            children.append(
                sl.Choices(
                    key="locale",
                    choices=tuple(
                        sl.Choice(
                            tag,
                            t(self.locale, _("Follow Discord")) if tag == FOLLOW_DISCORD else tag,
                        )
                        for tag in (FOLLOW_DISCORD, *sorted(SUPPORTED_LOCALES))
                    ),
                    selection=sl.controlled((self._locale_override or FOLLOW_DISCORD,), self._locale_changed),
                )
            )
        actions: list[sl.primitives.Button] = []
        if self.shows_voting:
            actions.append(sl.primitives.Button(t(self.locale, _("Voting")), self._show_voting, "voting"))
        actions.append(
            sl.primitives.Button(
                t(self.locale, _("Close")),
                self._close,
                "close",
                style=sl.primitives.ActionStyle.SECONDARY,
            )
        )
        children.append(sl.primitives.Row(tuple(actions)))
        return children

    def _voting_nodes(self) -> Sequence[sl.LayoutNode]:
        scope_note = self._scope_note()
        children: list[sl.LayoutNode] = [
            sl.section(
                sl.fields(*(sl.field(field.name, field.value) for field in self._voting_fields())),
                scope_note and sl.note(scope_note),
                heading=t(self.locale, _("Voting — {kind}"), kind=t(self.locale, KIND_LABELS[self.kind])),
            ),
            sl.Choices(
                key="vote-kind",
                choices=tuple(
                    sl.Choice(kind.value, t(self.locale, label), available=True) for kind, label in KIND_LABELS.items()
                ),
                selection=sl.controlled((self.kind.value,), self._kind_changed),
            ),
        ]
        if self._capabilities.edit_voting:
            children.append(
                sl.Choices(
                    key="role-weight",
                    choices=self._role_choices(),
                    selection=sl.controlled(("none",), self._role_changed),
                    minimum=1,
                    maximum=1,
                )
            )
        actions: list[sl.primitives.Button] = []
        if self._capabilities.edit_voting:
            actions.extend(
                (
                    sl.primitives.Button(
                        t(self.locale, _("Edit emojis")),
                        self._edit_emojis,
                        "edit-emojis",
                        style=sl.primitives.ActionStyle.PRIMARY,
                    ),
                    sl.primitives.Button(
                        t(self.locale, _("Confirm reset")) if self.confirming_reset else t(self.locale, _("Reset")),
                        self._reset,
                        "reset",
                        style=sl.primitives.ActionStyle.DANGER
                        if self.confirming_reset
                        else sl.primitives.ActionStyle.SECONDARY,
                    ),
                )
            )
        if self.shows_server:
            actions.append(sl.primitives.Button(t(self.locale, _("Back")), self._show_server, "server"))
        actions.append(
            sl.primitives.Button(
                t(self.locale, _("Close")),
                self._close,
                "close",
                style=sl.primitives.ActionStyle.SECONDARY,
            )
        )
        children.append(sl.primitives.Row(tuple(actions)))
        return children

    def _channel_choices(self, setting: ScalarChannelSetting) -> tuple[sl.Choice, ...]:
        current = self.channel_id(setting)
        choices = [sl.Choice("clear", t(self.locale, _("Clear")), _("Remove this channel."), available=True)]
        channels = getattr(self._guild, "channels", ())
        for channel in channels:
            if getattr(channel, "type", None) not in CHANNEL_TYPES:
                continue
            choices.append(sl.Choice(str(channel.id), f"#{channel.name}", available=True))
        if current is not None and not any(choice.key == str(current) for choice in choices):
            choices.append(sl.Choice(str(current), self._channel_display(current), available=True))
        return tuple(choices)

    def _role_choices(self) -> tuple[sl.Choice, ...]:
        choices = [sl.Choice("none", t(self.locale, _("Choose a role")))]
        roles = {role.id: role for role in getattr(self._guild, "roles", ())}
        roles.update({weight.role_id: self._guild.get_role(weight.role_id) for weight in self._weights})
        for role_id, role in sorted(roles.items()):
            label = role.name if role is not None else t(self.locale, _("Deleted role {id}"), id=role_id)
            choices.append(sl.Choice(str(role_id), label))
        return tuple(choices)

    async def _channel_changed(self, setting: ScalarChannelSetting, event: sl.ChoiceEvent) -> None:
        if not await self._may_event(event, SETTINGS_SERVER_EDIT):
            return
        value = event.selected[0]
        await self.set_channel(setting, None if value == "clear" else int(value))

    async def _locale_changed(self, event: sl.ChoiceEvent) -> None:
        if not await self._may_event(event, SETTINGS_SERVER_EDIT):
            return
        await self.set_locale(None if event.selected[0] == FOLLOW_DISCORD else event.selected[0])

    async def _kind_changed(self, event: sl.ChoiceEvent) -> None:
        await self.open_voting(VoteKind(event.selected[0]))

    async def _role_changed(self, event: sl.ChoiceEvent) -> None:
        if not await self._may_event(event, SETTINGS_VOTING_EDIT):
            return
        role_id = int(event.selected[0])
        role = self._guild.get_role(role_id)
        if role is None:
            await event.notice(t(self.locale, _("That role has been deleted.")))
            return
        await sl.discord.responder(event).send_modal(RoleWeightModal(cast(Any, self), role))

    async def _edit_emojis(self, event: sl.PressEvent) -> None:
        if await self._may_event(event, SETTINGS_VOTING_EDIT):
            await sl.discord.responder(event).send_modal(VoteEmojiModal(cast(Any, self)))

    async def _reset(self, event: sl.PressEvent) -> None:
        if not await self._may_event(event, SETTINGS_VOTING_EDIT):
            return
        if self.confirming_reset:
            await self.reset_voting()
        else:
            self.arm_reset()

    async def _show_voting(self, event: sl.PressEvent) -> None:
        await self.open_voting()

    async def _show_server(self, event: sl.PressEvent) -> None:
        await self.open_server()

    async def _close(self, event: sl.PressEvent) -> None:
        await event.finish()

    async def _may_event(self, event: sl.ActionEvent, node: PermissionNode) -> bool:
        if await allows(sl.discord.native(event), node):
            return True
        await event.notice(t(self.locale, _("You are no longer allowed to change this.")))
        return False

    async def set_channel(self, setting: ScalarChannelSetting, channel_id: int | None) -> None:
        if channel_id is None:
            await self._settings.clear(self._guild.id, setting)
        else:
            await self._settings.set_channel(self._guild.id, setting, channel_id)
        self._channels[setting] = channel_id

    async def set_locale(self, locale: str | None) -> None:
        await self._settings.set_locale(self._guild.id, locale)
        self._locale_override = locale
        self.locale = locale or self.locale

    async def set_weight(self, role_id: int, multiplier: float | None) -> None:
        if multiplier is None:
            await self._votes.remove_role_weight(self._guild.id, self.kind, role_id)
        else:
            await self._votes.set_role_weight(RoleWeight(self._guild.id, self.kind, role_id, multiplier))
        await self.open_voting()

    async def set_emojis(self, options: Sequence[VoteOption]) -> None:
        await self._votes.set_emoji_preset(self._guild.id, self.kind, options)
        await self.open_voting()

    def arm_reset(self) -> None:
        self.confirming_reset = True

    async def reset_voting(self) -> None:
        await self._votes.reset_configuration(self._guild.id, self.kind)
        await self.open_voting()

    def _server_fields(self) -> list[CardField]:
        fields = [
            CardField(t(self.locale, SETTING_LABELS[setting]), self._channel_display(self._channels[setting]))
            for setting in CHANNEL_SETTINGS
        ]
        language = (
            self._locale_override
            if self._locale_override is not None
            else t(self.locale, _("Following this server's Discord language"))
        )
        fields.append(CardField(t(self.locale, _("Bot language")), language))
        return fields

    def _channel_display(self, channel_id: int | None) -> str:
        if channel_id is None:
            return t(self.locale, _("_Not set_"))
        if self._guild.get_channel_or_thread(channel_id) is None:
            return t(self.locale, _("_Not found_ ({id})"), id=channel_id)
        return f"<#{channel_id}>"

    def _voting_fields(self) -> list[CardField]:
        preset = self._preset
        emojis = (
            "\n".join(f"{option.emoji} — {option.choice.value}" for option in preset.options)
            if preset is not None and preset.options
            else t(self.locale, _("_None_"))
        )
        weights = "\n".join(
            f"{self._role_display(weight.role_id)} — {weight.multiplier:g}x" for weight in self._weights
        ) or t(self.locale, _("_None_"))
        return [
            CardField(t(self.locale, _("Emojis")), emojis),
            CardField(t(self.locale, _("Role multipliers")), weights),
        ]

    def _role_display(self, role_id: int) -> str:
        role = self._guild.get_role(role_id)
        if role is None:
            return t(self.locale, _("_Deleted role_ ({id})"), id=role_id)
        return role.name

    def _scope_note(self) -> str | None:
        if self.kind is not VoteKind.BUILD or self._owner_guild_id in (None, self._guild.id):
            return None
        return t(
            self.locale,
            _("Build reviews are weighted by the network's own server, so these multipliers do not apply here."),
        )

    def mount(self) -> sl.discord.Mount:
        """Create the production mount with author lock and shared error handling."""
        return create_mount(self, locale=self.locale, timeout=SESSION_SECONDS, lock_to=self._author_id)

    def bind_message(self, message: discord.Message) -> None:
        """Compatibility binding for tests and extensions still holding the old name."""
        self._bound_message = message

    def _compat_layout(self) -> discord.ui.LayoutView:
        layout = discord.ui.LayoutView(timeout=None)
        if self.page == "voting":
            layout.add_item(
                card_container(
                    t(self.locale, _("Voting — {kind}"), kind=t(self.locale, KIND_LABELS[self.kind])),
                    None,
                    fields=self._voting_fields(),
                    footer=self._scope_note(),
                )
            )
            layout.add_item(discord.ui.ActionRow(VoteKindSelect(cast(Any, self))))
            if self._capabilities.edit_voting:
                layout.add_item(discord.ui.ActionRow(RoleWeightSelect(cast(Any, self))))
            row = discord.ui.ActionRow()
            if self._capabilities.edit_voting:
                row.add_item(EditEmojisButton(cast(Any, self)))
                row.add_item(ResetVotingButton(cast(Any, self)))
            if self.shows_server:
                row.add_item(ServerPageButton(cast(Any, self)))
            row.add_item(ClosePanelButton(cast(Any, self)))
            layout.add_item(row)
        else:
            layout.add_item(
                card_container(
                    t(self.locale, _("Server settings")),
                    t(self.locale, _("Change as many as you like; an emptied picker clears that setting."))
                    if self._capabilities.edit_server
                    else None,
                    fields=self._server_fields(),
                )
            )
            if self._capabilities.edit_server:
                for setting in CHANNEL_SETTINGS:
                    layout.add_item(discord.ui.ActionRow(SettingChannelSelect(cast(Any, self), setting)))
                layout.add_item(discord.ui.ActionRow(LocaleSelect(cast(Any, self))))
            row = discord.ui.ActionRow()
            if self.shows_voting:
                row.add_item(VotingPageButton(cast(Any, self)))
            row.add_item(ClosePanelButton(cast(Any, self)))
            layout.add_item(row)
        return layout

    def _compat_view(self) -> discord.ui.LayoutView:
        return self._compat_layout()

    def to_components(self) -> list[dict[str, Any]]:
        return self.mount().build_view().to_components()

    def walk_children(self) -> list[discord.ui.Item[Any]]:
        return list(self._compat_view().walk_children())

    async def on_timeout(self) -> None:
        self._compat_disabled = True
        layout = self._compat_layout()
        for child in layout.walk_children():
            if isinstance(child, discord.ui.Button | discord.ui.Select):
                child.disabled = True
        if self._bound_message is not None:
            await self._bound_message.edit(view=layout)


def _panel_layout(panel: SettingsPanelView | SettingsPanel) -> discord.ui.LayoutView:
    return panel._compat_layout() if isinstance(panel, SettingsPanel) else panel


class SettingChannelSelect(discord.ui.ChannelSelect[SettingsPanelView]):
    """Point one setting at a channel; emptying the picker clears it."""

    def __init__(self, view: SettingsPanelView, setting: ScalarChannelSetting) -> None:
        current = view.channel_id(setting)
        super().__init__(
            placeholder=t(view.locale, SETTING_LABELS[setting]),
            channel_types=CHANNEL_TYPES,
            min_values=0,
            max_values=1,
            default_values=[discord.SelectDefaultValue(id=current, type=discord.SelectDefaultValueType.channel)]
            if current is not None
            else [],
        )
        self._panel = view
        self._setting = setting

    @override
    async def callback(self, interaction: discord.Interaction[discord.Client]) -> None:
        if not await self._panel.may(interaction, SETTINGS_SERVER_EDIT):
            return
        await self._panel.set_channel(self._setting, self.values[0].id if self.values else None)
        await edit_interaction_layout(interaction, _panel_layout(self._panel))


class LocaleSelect(discord.ui.Select[SettingsPanelView]):
    """Pin the bot's language for this server, or follow Discord's."""

    def __init__(self, view: SettingsPanelView) -> None:
        options = [
            discord.SelectOption(
                label=t(view.locale, _("Follow Discord")),
                value=FOLLOW_DISCORD,
                default=view.locale_override is None,
            ),
            *(
                discord.SelectOption(label=tag, value=tag, default=tag == view.locale_override)
                for tag in sorted(SUPPORTED_LOCALES)
            ),
        ]
        super().__init__(placeholder=t(view.locale, _("Bot language")), options=options)
        self._panel = view

    @override
    async def callback(self, interaction: discord.Interaction[discord.Client]) -> None:
        if not await self._panel.may(interaction, SETTINGS_SERVER_EDIT):
            return
        chosen = self.values[0]
        await self._panel.set_locale(None if chosen == FOLLOW_DISCORD else chosen)
        await edit_interaction_layout(interaction, _panel_layout(self._panel))


class VoteKindSelect(discord.ui.Select[SettingsPanelView]):
    """Choose which kind of vote session the page configures."""

    def __init__(self, view: SettingsPanelView) -> None:
        options = [
            discord.SelectOption(label=t(view.locale, label), value=kind.value, default=kind is view.kind)
            for kind, label in KIND_LABELS.items()
        ]
        super().__init__(placeholder=t(view.locale, _("Vote kind")), options=options)
        self._panel = view

    @override
    async def callback(self, interaction: discord.Interaction[discord.Client]) -> None:
        await self._panel.open_voting(VoteKind(self.values[0]))
        await edit_interaction_layout(interaction, _panel_layout(self._panel))


class RoleWeightSelect(discord.ui.RoleSelect[SettingsPanelView]):
    """Pick a role, then set its multiplier in a modal.

    A multiplier is a number, which no select can express, so the two-step is Discord's
    rather than ours — but it replaces reading a role name off a card and retyping it.
    """

    def __init__(self, view: SettingsPanelView) -> None:
        super().__init__(placeholder=t(view.locale, _("Weigh a role's votes")))
        self._panel = view

    @override
    async def callback(self, interaction: discord.Interaction[discord.Client]) -> None:
        if not await self._panel.may(interaction, SETTINGS_VOTING_EDIT):
            return
        await interaction.response.send_modal(RoleWeightModal(self._panel, self.values[0]))  # pyrefly: ignore[no-matching-overload]


class RoleWeightModal(ErrorHandledModal):
    """Set or remove one role's vote multiplier."""

    def __init__(self, panel: SettingsPanelView, role: discord.Role) -> None:
        super().__init__(title=t(panel.locale, _("Vote weight for {role}"), role=role.name)[:45])
        self._panel = panel
        self._role = role
        current = panel.weight_for(role.id)
        self.multiplier = discord.ui.TextInput(
            default=f"{current:g}" if current is not None else None,
            placeholder="1.5",
            required=False,
            max_length=16,
        )
        self.add_item(
            discord.ui.Label(
                text=t(panel.locale, _("Multiplier — leave empty to remove this role's weight")),
                component=self.multiplier,
            )
        )

    @override
    async def on_submit(self, interaction: discord.Interaction) -> None:
        text = self.multiplier.value.strip()
        try:
            await self._panel.set_weight(self._role.id, float(text) if text else None)
        except InvalidVoteConfigurationError, ValueError:
            await interaction.response.send_message(
                t(self._panel.locale, _("A vote multiplier must be a positive number, such as 1.5.")),
                ephemeral=True,
                allowed_mentions=no_mentions(),
            )
            return
        await edit_interaction_layout(interaction, _panel_layout(self._panel))


class VoteEmojiModal(ErrorHandledModal):
    """Edit an ordered guild emoji preset as one choice/emoji pair per line."""

    def __init__(self, panel: SettingsPanelView) -> None:
        super().__init__(title=t(panel.locale, _("{kind} vote emojis"), kind=panel.kind.value)[:45])
        self._panel = panel
        self.aliases = discord.ui.TextInput(
            default=panel.emoji_preset_text(),
            style=discord.TextStyle.paragraph,
            placeholder="approve | 👍\ndeny | 👎",
            min_length=1,
            max_length=1000,
        )
        self.add_item(
            discord.ui.Label(text=t(panel.locale, _("One `choice | emoji` per line")), component=self.aliases)
        )

    @override
    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        locale = self._panel.locale
        options: list[VoteOption] = []
        kind = self._panel.kind
        for position, line in enumerate(filter(None, (line.strip() for line in self.aliases.value.splitlines()))):
            parts = [part.strip() for part in line.split("|", 1)]
            if len(parts) != 2:
                await _reject(interaction, t(locale, _("Each line must read `choice | emoji`.")))
                return
            choice_text, emoji = parts
            try:
                choice = VoteChoice.GENERIC if kind is VoteKind.GENERIC else VoteChoice(choice_text)
            except ValueError:
                await _reject(interaction, t(locale, _("`{choice}` is not a vote choice."), choice=choice_text))
                return
            parsed = discord.PartialEmoji.from_str(emoji)
            if parsed.is_custom_emoji():
                custom = interaction.guild.get_emoji(parsed.id or 0)
                if custom is None or not custom.is_usable():
                    await _reject(interaction, t(locale, _("The custom emoji {emoji} is inaccessible."), emoji=emoji))
                    return
            options.append(
                VoteOption(
                    emoji,
                    choice,
                    identifier=str(position + 1) if kind is VoteKind.GENERIC else choice.value,
                    guild_id=interaction.guild.id,
                    label=f"Option {position + 1}" if kind is VoteKind.GENERIC else None,
                    position=position,
                )
            )
        try:
            await self._panel.set_emojis(options)
        except InvalidVoteConfigurationError as error:
            await _reject(interaction, str(error))
            return
        await edit_interaction_layout(interaction, _panel_layout(self._panel))


async def _reject(interaction: discord.Interaction, message: str) -> None:
    """Answer a rejected edit privately, leaving the panel as it was."""
    await interaction.response.send_message(message, ephemeral=True, allowed_mentions=no_mentions())


class VotingPageButton(discord.ui.Button[SettingsPanelView]):
    def __init__(self, view: SettingsPanelView) -> None:
        super().__init__(label=t(view.locale, _("Voting")))
        self._panel = view

    @override
    async def callback(self, interaction: discord.Interaction[discord.Client]) -> None:
        await self._panel.open_voting()
        await edit_interaction_layout(interaction, _panel_layout(self._panel))


class ServerPageButton(discord.ui.Button[SettingsPanelView]):
    def __init__(self, view: SettingsPanelView) -> None:
        super().__init__(label=t(view.locale, _("Back")))
        self._panel = view

    @override
    async def callback(self, interaction: discord.Interaction[discord.Client]) -> None:
        await self._panel.open_server()
        await edit_interaction_layout(interaction, _panel_layout(self._panel))


class EditEmojisButton(discord.ui.Button[SettingsPanelView]):
    def __init__(self, view: SettingsPanelView) -> None:
        super().__init__(label=t(view.locale, _("Edit emojis")), style=discord.ButtonStyle.primary)
        self._panel = view

    @override
    async def callback(self, interaction: discord.Interaction[discord.Client]) -> None:
        if not await self._panel.may(interaction, SETTINGS_VOTING_EDIT):
            return
        await interaction.response.send_modal(VoteEmojiModal(self._panel))  # pyrefly: ignore[no-matching-overload]


class ResetVotingButton(discord.ui.Button[SettingsPanelView]):
    """Discard a kind's voting configuration, on the second click."""

    def __init__(self, view: SettingsPanelView) -> None:
        armed = view.confirming_reset
        super().__init__(
            label=t(view.locale, _("Confirm reset")) if armed else t(view.locale, _("Reset")),
            style=discord.ButtonStyle.danger if armed else discord.ButtonStyle.secondary,
        )
        self._panel = view

    @override
    async def callback(self, interaction: discord.Interaction[discord.Client]) -> None:
        if not await self._panel.may(interaction, SETTINGS_VOTING_EDIT):
            return
        if self._panel.confirming_reset:
            await self._panel.reset_voting()
        else:
            self._panel.arm_reset()
        await edit_interaction_layout(interaction, _panel_layout(self._panel))


class ClosePanelButton(discord.ui.Button[SettingsPanelView]):
    def __init__(self, view: SettingsPanelView) -> None:
        super().__init__(label=t(view.locale, _("Close")), style=discord.ButtonStyle.secondary)
        self._panel = view

    @override
    async def callback(self, interaction: discord.Interaction[discord.Client]) -> None:
        self._panel.disable_controls()
        await edit_interaction_layout(interaction, _panel_layout(self._panel))
