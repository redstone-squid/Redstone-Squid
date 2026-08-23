"""Interactive Components V2 rendering for server settings."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast, override

import discord

import squid_layouts as sl
from squid.bot.errors import ErrorHandledModal
from squid.bot.i18n import t
from squid.bot.ui import L, create_mount, localization_for
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
"""Every channel setting, in the order the panel offers them."""

CHANNEL_TYPES = (
    sl.entity.ChannelType.TEXT,
    sl.entity.ChannelType.ANNOUNCEMENT,
    sl.entity.ChannelType.VOICE,
    sl.entity.ChannelType.STAGE_VOICE,
    sl.entity.ChannelType.PUBLIC_THREAD,
    sl.entity.ChannelType.PRIVATE_THREAD,
    sl.entity.ChannelType.ANNOUNCEMENT_THREAD,
)
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
    """A semantic, mount-owned settings workspace."""

    history: sl.runtime.History = sl.runtime.history(limit=10)
    """Undo for the server page's writes; see `docs/plans/squid-layouts-redesign/28-history.md`."""

    page: str = sl.state("server")
    kind: VoteKind = sl.state(VoteKind.BUILD)
    confirming_reset: bool = sl.state(default=False)
    locale: str | None = sl.state(None, persist=False)
    # Refreshed from the services by open_server/open_voting, so a snapshot would only restore
    # them stale.
    _channels: Mapping[ScalarChannelSetting, int | None] = sl.state(dict.fromkeys(CHANNEL_SETTINGS), persist=False)
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
        self._mount: sl.discord.Mount | None = None

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
            L(t"Change as many as you like; an emptied picker clears that setting.")
            if self._capabilities.edit_server
            else None
        )
        children: list[sl.LayoutNode] = [
            sl.section(
                sl.heading(L(t"Server settings")),
                description and sl.truncate(sl.paragraph(description)),
                sl.fields(*(sl.field(field.name, field.value) for field in self._server_fields())),
            )
        ]
        if self._capabilities.edit_server:
            for setting in CHANNEL_SETTINGS:
                selected = self.channel_id(setting)

                async def change(event: sl.EntityEvent, current: ScalarChannelSetting = setting) -> None:
                    await self._channel_changed(event, current)

                children.append(
                    sl.entities(
                        key=f"channel-{setting}",
                        entity_type=sl.entity.EntityType.CHANNEL,
                        selection=sl.controlled(
                            () if selected is None else (sl.entity.EntityRef(sl.entity.EntityKind.CHANNEL, selected),),
                            change,
                        ),
                        minimum=0,
                        maximum=1,
                        channel_types=CHANNEL_TYPES,
                        placeholder=L(SETTING_LABELS[setting]),
                    )
                )
            children.append(
                sl.semantic.Choices(
                    key="locale",
                    choices=tuple(
                        sl.semantic.Choice(
                            tag,
                            L(t"Follow Discord") if tag == FOLLOW_DISCORD else tag,
                        )
                        for tag in (FOLLOW_DISCORD, *sorted(SUPPORTED_LOCALES))
                    ),
                    selection=sl.controlled((self._locale_override or FOLLOW_DISCORD,), self._locale_changed),
                )
            )
        actions: list[sl.primitives.Button] = []
        # Only once there is something to reverse: an always-present disabled pair would be
        # two dead controls on a panel most readers never undo anything on.
        if self._capabilities.edit_server and self.history.can_undo:
            actions.append(sl.primitives.Button(L(t"Undo"), self._undo, "undo"))
        if self._capabilities.edit_server and self.history.can_redo:
            actions.append(sl.primitives.Button(L(t"Redo"), self._redo, "redo"))
        if self.shows_voting:
            actions.append(sl.primitives.Button(L(t"Voting"), self._show_voting, "voting"))
        actions.append(
            sl.primitives.Button(
                L(t"Close"),
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
                sl.heading(L("Voting — {kind}", kind=L(KIND_LABELS[self.kind]))),
                sl.fields(*(sl.field(field.name, field.value) for field in self._voting_fields())),
                scope_note and sl.note(scope_note),
            ),
            sl.semantic.Choices(
                key="vote-kind",
                choices=tuple(sl.semantic.Choice(kind.value, L(label), available=True) for kind, label in KIND_LABELS.items()),
                selection=sl.controlled((self.kind.value,), self._kind_changed),
            ),
        ]
        if self._capabilities.edit_voting:
            children.append(
                sl.entities(
                    key="role-weight",
                    entity_type=sl.entity.EntityType.ROLE,
                    selection=sl.controlled((), self._role_changed),
                    minimum=1,
                    maximum=1,
                    placeholder=L(t"Choose a role"),
                )
            )
        actions: list[sl.primitives.Button] = []
        if self._capabilities.edit_voting:
            actions.extend(
                (
                    sl.primitives.Button(
                        L(t"Edit emojis"),
                        self._edit_emojis,
                        "edit-emojis",
                        style=sl.primitives.ActionStyle.PRIMARY,
                    ),
                    sl.primitives.Button(
                        L(t"Confirm reset") if self.confirming_reset else L(t"Reset"),
                        self._reset,
                        "reset",
                        style=sl.primitives.ActionStyle.DANGER
                        if self.confirming_reset
                        else sl.primitives.ActionStyle.SECONDARY,
                    ),
                )
            )
        if self.shows_server:
            actions.append(sl.primitives.Button(L(t"Back"), self._show_server, "server"))
        actions.append(
            sl.primitives.Button(
                L(t"Close"),
                self._close,
                "close",
                style=sl.primitives.ActionStyle.SECONDARY,
            )
        )
        children.append(sl.primitives.Row(tuple(actions)))
        return children

    async def _channel_changed(self, event: sl.EntityEvent, setting: ScalarChannelSetting) -> None:
        if not await self._may_event(event, SETTINGS_SERVER_EDIT):
            return
        await self.set_channel(setting, event.selected[0].id if event.selected else None)

    async def _locale_changed(self, event: sl.ChoiceEvent) -> None:
        if not await self._may_event(event, SETTINGS_SERVER_EDIT):
            return
        await self.set_locale(None if event.selected[0] == FOLLOW_DISCORD else event.selected[0])

    async def _kind_changed(self, event: sl.ChoiceEvent) -> None:
        await self.open_voting(VoteKind(event.selected[0]))

    async def _role_changed(self, event: sl.EntityEvent) -> None:
        if not await self._may_event(event, SETTINGS_VOTING_EDIT):
            return
        role_id = event.selected[0].id
        role = self._guild.get_role(role_id)
        if role is None:
            await event.notice(L(t"That role has been deleted."))
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

    async def _undo(self, event: sl.PressEvent) -> None:
        # Undo is an ordinary write, so it repeats the check of the action it reverses:
        # a moderator who lost the permission mid-session may not reverse their own change.
        if not await self._may_event(event, SETTINGS_SERVER_EDIT):
            return
        if (entry := await self.history.undo()) is not None:
            await event.notice(L("Undid: {change}", change=entry.label))

    async def _redo(self, event: sl.PressEvent) -> None:
        if not await self._may_event(event, SETTINGS_SERVER_EDIT):
            return
        if (entry := await self.history.redo()) is not None:
            await event.notice(L("Redid: {change}", change=entry.label))

    async def _show_voting(self, event: sl.PressEvent) -> None:
        await self.open_voting()

    async def _show_server(self, event: sl.PressEvent) -> None:
        await self.open_server()

    async def _close(self, event: sl.PressEvent) -> None:
        await event.finish()

    async def _may_event(self, event: sl.ActionEvent, node: PermissionNode) -> bool:
        if await allows(sl.discord.native(event), node):
            return True
        await event.notice(L(t"You are no longer allowed to change this."))
        return False

    async def set_channel(self, setting: ScalarChannelSetting, channel_id: int | None) -> None:
        previous = self._channels[setting]
        await self._write_channel(setting, channel_id)
        self._channels = {**self._channels, setting: channel_id}
        self.history.record(
            L("Changed {setting}", setting=L(SETTING_LABELS[setting])),
            undo=lambda: self._write_channel(setting, previous),
            redo=lambda: self._write_channel(setting, channel_id),
        )

    async def _write_channel(self, setting: ScalarChannelSetting, channel_id: int | None) -> None:
        """The stored half of a channel change; `_channels` is the framework's to restore."""
        if channel_id is None:
            await self._settings.clear(self._guild.id, setting)
        else:
            await self._settings.set_channel(self._guild.id, setting, channel_id)

    async def set_locale(self, locale: str | None) -> None:
        previous_override, previous_locale = self._locale_override, self.locale
        await self._write_locale(locale, locale or previous_locale)
        self._locale_override = locale
        self.locale = locale or self.locale
        self.history.record(
            L(t"Changed the bot language"),
            undo=lambda: self._write_locale(previous_override, previous_locale),
            redo=lambda: self._write_locale(locale, locale or previous_locale),
        )

    async def _write_locale(self, override: str | None, effective: str | None) -> None:
        """The stored locale and the mount's, neither of which is component state.

        Both halves of the effective locale are captured at the call site rather than read
        back here, because an inverse runs before the framework restores `locale` and
        `_locale_override` -- so reading them here would see the values being reversed.
        """
        await self._settings.set_locale(self._guild.id, override)
        if self._mount is not None:
            self._mount.localize(localization_for(effective))

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
            CardField(L(SETTING_LABELS[setting]), self._channel_display(self._channels[setting]))
            for setting in CHANNEL_SETTINGS
        ]
        language = (
            self._locale_override
            if self._locale_override is not None
            else L(t"Following this server's Discord language")
        )
        fields.append(CardField(L(t"Bot language"), language))
        return fields

    def _channel_display(self, channel_id: int | None) -> sl.TextLike:
        if channel_id is None:
            return L(t"_Not set_")
        if self._guild.get_channel_or_thread(channel_id) is None:
            return L("_Not found_ ({id})", id=channel_id)
        return sl.md("{mention}", mention=sl.raw_md(f"<#{channel_id}>"))

    def _voting_fields(self) -> list[CardField]:
        preset = self._preset
        emojis = (
            "\n".join(f"{option.emoji} — {option.choice.value}" for option in preset.options)
            if preset is not None and preset.options
            else L(t"_None_")
        )
        weights = "\n".join(
            f"{self._role_display(weight.role_id)} — {weight.multiplier:g}x" for weight in self._weights
        ) or L(t"_None_")
        return [
            CardField(L(t"Emojis"), emojis),
            CardField(L(t"Role multipliers"), weights),
        ]

    def _role_display(self, role_id: int) -> str:
        role = self._guild.get_role(role_id)
        if role is None:
            return t(self.locale, _("_Deleted role_ ({id})"), id=role_id)
        return role.name

    def _scope_note(self) -> sl.TextLike | None:
        if self.kind is not VoteKind.BUILD or self._owner_guild_id in (None, self._guild.id):
            return None
        return L(t"Build reviews are weighted by the network's own server, so these multipliers do not apply here.")

    def mount(self) -> sl.discord.Mount:
        """Create the production mount with author lock and shared error handling."""
        self._mount = create_mount(
            self, access=sl.discord.Owner(self._author_id), locale=self.locale, timeout=SESSION_SECONDS
        )
        return self._mount


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
