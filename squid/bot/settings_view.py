"""Interactive Components V2 rendering for server settings."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast, override

import discord

import squid_layouts as sl
from squid.bot.errors import ErrorHandledModal
from squid.bot.i18n import t
from squid.bot.ui import create_mount
from squid.bot.utils.components import CardField, no_mentions
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

    async def on_load(self) -> None:
        """Open the first page allowed by the caller.

        Both branches are the same methods the page buttons call, so nothing here is a
        lifecycle hook masquerading as a re-fetch API.
        """
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
        responder = sl.discord.responder(event)
        await responder.send_modal(RoleWeightModal(self, role, mount=responder.mount))

    async def _edit_emojis(self, event: sl.PressEvent) -> None:
        if await self._may_event(event, SETTINGS_VOTING_EDIT):
            responder = sl.discord.responder(event)
            await responder.send_modal(VoteEmojiModal(self, mount=responder.mount))

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


class RoleWeightModal(ErrorHandledModal):
    """Set or remove one role's vote multiplier."""

    def __init__(self, panel: SettingsPanel, role: discord.Role, *, mount: sl.discord.Mount) -> None:
        super().__init__(title=t(panel.locale, _("Vote weight for {role}"), role=role.name)[:45])
        self._panel = panel
        self._mount = mount
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
        await self._mount.flush(interaction)


class VoteEmojiModal(ErrorHandledModal):
    """Edit an ordered guild emoji preset as one choice/emoji pair per line."""

    def __init__(self, panel: SettingsPanel, *, mount: sl.discord.Mount) -> None:
        super().__init__(title=t(panel.locale, _("{kind} vote emojis"), kind=panel.kind.value)[:45])
        self._panel = panel
        self._mount = mount
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
        await self._mount.flush(interaction)


async def _reject(interaction: discord.Interaction, message: str) -> None:
    """Answer a rejected edit privately, leaving the panel as it was."""
    await interaction.response.send_message(message, ephemeral=True, allowed_mentions=no_mentions())
