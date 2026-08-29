"""Interactive Components V2 rendering for server settings."""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import discord

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.i18n import localization_for
from squid.bot.ui import CardField, tr
from squid.core.i18n import SUPPORTED_LOCALES
from squid.permissions.domain import PermissionNode
from squid.permissions.domain.catalogue import SETTINGS_SERVER_EDIT, SETTINGS_VOTING_EDIT
from squid.settings.domain import ScalarChannelSetting
from squid.voting.domain import EmojiPreset, RoleWeight, VoteChoice, VoteKind, VoteOption
from squid.voting.errors import InvalidVoteConfigurationError
from squid_ui.text import Message

if TYPE_CHECKING:
    from squid.settings.application import SettingsService
    from squid.voting.application import VoteService

SESSION_SECONDS = 300

FOLLOW_DISCORD = "-"
"""The locale select's "no override" value; an empty select value is not sendable."""

CHANNEL_SETTINGS: tuple[ScalarChannelSetting, ...] = ("Smallest", "Fastest", "First", "Builds", "Vote")
"""Every channel setting, in the order the panel offers them."""

CONVERSATION_TYPES = (
    sl.entity.ConversationType.GUILD_TEXT,
    sl.entity.ConversationType.GUILD_ANNOUNCEMENT,
    sl.entity.ConversationType.GUILD_VOICE,
    sl.entity.ConversationType.GUILD_STAGE_VOICE,
    sl.entity.ConversationType.GUILD_PUBLIC_THREAD,
    sl.entity.ConversationType.GUILD_PRIVATE_THREAD,
    sl.entity.ConversationType.GUILD_ANNOUNCEMENT_THREAD,
)
"""What `GuildMessageable` admits, as channel types a picker can offer."""

SETTING_LABELS: dict[ScalarChannelSetting, Message] = {
    "Smallest": tr(t"Smallest-record builds"),
    "Fastest": tr(t"Fastest-record builds"),
    "First": tr(t"First-of-a-kind builds"),
    "Builds": tr(t"Confirmed builds"),
    "Vote": tr(t"Builds awaiting review"),
}
"""What each channel setting is for, named rather than title-cased so it translates."""

KIND_LABELS: dict[VoteKind, Message] = {
    VoteKind.BUILD: tr(t"Build reviews"),
    VoteKind.DELETE_LOG: tr(t"Deletion votes"),
    VoteKind.GENERIC: tr(t"Polls"),
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


type SettingsAuthorizer = Callable[[PermissionNode], Awaitable[bool]]


class SettingsPanel(sd.Screen):
    """A semantic, mount-owned settings workspace."""

    session_name = "settings"
    scope = sd.ScopeKind.USER_GUILD
    timeout = SESSION_SECONDS

    history: sl.runtime.History = sl.runtime.history(limit=10)
    """Undo for the server page's writes; see `docs/plans/squid-ui-redesign/28-history.md`."""

    page: str = sl.state("server")
    kind: VoteKind = sl.state(VoteKind.BUILD)
    confirming_reset: bool = sl.state(default=False)
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
        capabilities: SettingsCapabilities,
        authorize: SettingsAuthorizer,
        owner_guild_id: int | None = None,
    ) -> None:
        self._settings = settings
        self._votes = votes
        self._guild = guild
        self._capabilities = capabilities
        self._authorize = authorize
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

    def render(self) -> Sequence[sl.LayoutNode[sl.ComponentsV2Target]]:
        if self.page == "voting":
            return self._voting_nodes()
        return self._server_nodes()

    def _server_nodes(self) -> Sequence[sl.LayoutNode[sl.ComponentsV2Target]]:
        description = (
            tr(t"Change as many as you like; an emptied picker clears that setting.")
            if self._capabilities.edit_server
            else None
        )
        children: list[sl.LayoutNode[sl.ComponentsV2Target]] = [
            sl.section(
                sl.heading(tr(t"Server settings")),
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
                        entity_type=sl.entity.EntityType.CONVERSATION,
                        selection=sl.controlled(
                            ()
                            if selected is None
                            else (sl.entity.EntityRef(sl.entity.EntityKind.CONVERSATION, selected),),
                            change,
                        ),
                        minimum=0,
                        maximum=1,
                        conversation_types=CONVERSATION_TYPES,
                        placeholder=tr(SETTING_LABELS[setting]),
                    )
                )
            children.append(
                sl.choices(
                    *(
                        sl.choice(
                            tr(t"Follow Discord") if tag == FOLLOW_DISCORD else tag,
                            key=tag,
                        )
                        for tag in (FOLLOW_DISCORD, *sorted(SUPPORTED_LOCALES))
                    ),
                    key="locale",
                    selection=sl.controlled((self._locale_override or FOLLOW_DISCORD,), self._locale_changed),
                )
            )
        actions: list[sl.semantic.ActionControl] = []
        # Only once there is something to reverse: an always-present disabled pair would be
        # two dead controls on a panel most readers never undo anything on.
        if self._capabilities.edit_server and self.history.can_undo:
            actions.append(sl.action_control(tr(t"Undo"), self._undo, key="undo"))
        if self._capabilities.edit_server and self.history.can_redo:
            actions.append(sl.action_control(tr(t"Redo"), self._redo, key="redo"))
        if self.shows_voting:
            actions.append(sl.action_control(tr(t"Voting"), self._show_voting, key="voting"))
        actions.append(
            sl.action_control(
                tr(t"Close"),
                self._close,
                key="close",
            )
        )
        children.append(sl.action_controls(*actions, key="server-actions"))
        return children

    def _voting_nodes(self) -> Sequence[sl.LayoutNode[sl.ComponentsV2Target]]:
        scope_note = self._scope_note()
        kind_label = tr(KIND_LABELS[self.kind])
        children: list[sl.LayoutNode[sl.ComponentsV2Target]] = [
            sl.section(
                sl.heading(tr(t"Voting — {kind_label}")),
                sl.fields(*(sl.field(field.name, field.value) for field in self._voting_fields())),
                scope_note and sl.note(scope_note),
            ),
            sl.choices(
                *(sl.choice(tr(label), key=kind.value) for kind, label in KIND_LABELS.items()),
                key="vote-kind",
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
                    placeholder=tr(t"Choose a role"),
                )
            )
        actions: list[sl.semantic.ActionControl] = []
        if self._capabilities.edit_voting:
            actions.extend(
                (
                    sl.action_control(
                        tr(t"Edit emojis"),
                        self._edit_emojis,
                        key="edit-emojis",
                        emphasis=sl.semantic.Emphasis.STRONG,
                    ),
                    sl.action_control(
                        tr(t"Confirm reset") if self.confirming_reset else tr(t"Reset"),
                        self._reset,
                        key="reset",
                        tone=sl.Tone.DANGER if self.confirming_reset else sl.Tone.NEUTRAL,
                    ),
                )
            )
        if self.shows_server:
            actions.append(sl.action_control(tr(t"Back"), self._show_server, key="server"))
        actions.append(
            sl.action_control(
                tr(t"Close"),
                self._close,
                key="close",
            )
        )
        children.append(sl.action_controls(*actions, key="voting-actions"))
        return children

    async def _channel_changed(self, event: sl.EntityEvent, setting: ScalarChannelSetting) -> None:
        if not await self._may_event(event, SETTINGS_SERVER_EDIT):
            return
        channel_id = event.selected[0].id if event.selected else None
        if channel_id is not None and not isinstance(channel_id, int):
            await event.notice(tr(t"That channel selection is invalid."))
            return
        await self.set_channel(setting, channel_id)

    async def _locale_changed(self, event: sl.ChoiceEvent) -> None:
        if not await self._may_event(event, SETTINGS_SERVER_EDIT):
            return
        await self.set_locale(
            None if event.selected[0] == FOLLOW_DISCORD else event.selected[0],
            message_root=sd.responder(event).message_root,
        )

    async def _kind_changed(self, event: sl.ChoiceEvent) -> None:
        await self.open_voting(VoteKind(event.selected[0]))

    async def _role_changed(self, event: sl.EntityEvent) -> None:
        if not await self._may_event(event, SETTINGS_VOTING_EDIT):
            return
        role_id = event.selected[0].id
        if not isinstance(role_id, int):
            await event.notice(tr(t"That role selection is invalid."))
            return
        role = self._guild.get_role(role_id)
        if role is None:
            await event.notice(tr(t"That role has been deleted."))
            return
        role_name = role.name
        await event.present_form(
            sl.forms.FormSpec(
                tr(t"Vote weight for {role_name}"),
                (
                    sl.forms.TextField(
                        key="multiplier",
                        label=tr(t"Multiplier; leave empty to remove this role's weight"),
                        placeholder="1.5",
                        default=(f"{current:g}" if (current := self.weight_for(role.id)) is not None else ""),
                        required=False,
                        maximum=16,
                    ),
                ),
            ),
            key="role-weight",
            on_submit=lambda form_event: self._role_weight_submitted(form_event, role.id),
        )

    async def _edit_emojis(self, event: sl.PressEvent) -> None:
        if await self._may_event(event, SETTINGS_VOTING_EDIT):
            kind = self.kind.value
            await event.present_form(
                sl.forms.FormSpec(
                    tr(t"{kind} vote emojis"),
                    (
                        sl.forms.TextAreaField(
                            key="aliases",
                            label=tr(t"One `choice | emoji` per line"),
                            placeholder="approve | ✅\ndeny | ❌",
                            default=self.emoji_preset_text(),
                            minimum=1,
                            maximum=1000,
                        ),
                    ),
                ),
                key="vote-emojis",
                on_submit=self._emoji_form_submitted,
            )

    async def _role_weight_submitted(self, event: sl.SubmitEvent, role_id: int) -> None:
        text = cast(str, event.values["multiplier"]).strip()
        try:
            await self.set_weight(role_id, float(text) if text else None)
        except InvalidVoteConfigurationError, ValueError:
            await event.notice(tr(t"A vote multiplier must be a positive number, such as 1.5."))

    async def _emoji_form_submitted(self, event: sl.SubmitEvent) -> None:
        text = cast(str, event.values["aliases"])
        options: list[VoteOption] = []
        for position, line in enumerate(filter(None, (line.strip() for line in text.splitlines()))):
            parts = [part.strip() for part in line.split("|", 1)]
            if len(parts) != 2:
                await event.notice(tr(t"Each line must read `choice | emoji`."))
                return
            choice_text, emoji = parts
            try:
                choice = VoteChoice.GENERIC if self.kind is VoteKind.GENERIC else VoteChoice(choice_text)
            except ValueError:
                await event.notice(tr(t"`{choice_text}` is not a vote choice."))
                return
            parsed = discord.PartialEmoji.from_str(emoji)
            if parsed.is_custom_emoji():
                custom = self._guild.get_emoji(parsed.id or 0)
                if custom is None or not custom.is_usable():
                    await event.notice(tr(t"The custom emoji {emoji} is inaccessible."))
                    return
            options.append(
                VoteOption(
                    emoji,
                    choice,
                    identifier=str(position + 1) if self.kind is VoteKind.GENERIC else choice.value,
                    guild_id=self._guild.id,
                    label=f"Option {position + 1}" if self.kind is VoteKind.GENERIC else None,
                    position=position,
                )
            )
        try:
            await self.set_emojis(options)
        except InvalidVoteConfigurationError as error:
            await event.notice(str(error))

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
        result = await self.history.undo()
        if result.applied and result.entry is not None:
            change = result.entry.label
            await event.notice(tr(t"Undid: {change}"))
        elif result.status is sl.runtime.HistoryResultStatus.CONFLICT:
            await event.notice(tr(t"That change was modified elsewhere and cannot be undone safely."))

    async def _redo(self, event: sl.PressEvent) -> None:
        if not await self._may_event(event, SETTINGS_SERVER_EDIT):
            return
        result = await self.history.redo()
        if result.applied and result.entry is not None:
            change = result.entry.label
            await event.notice(tr(t"Redid: {change}"))
        elif result.status is sl.runtime.HistoryResultStatus.CONFLICT:
            await event.notice(tr(t"That change was modified elsewhere and cannot be redone safely."))

    async def _show_voting(self, event: sl.PressEvent) -> None:
        await self.open_voting()

    async def _show_server(self, event: sl.PressEvent) -> None:
        await self.open_server()

    async def _close(self, event: sl.PressEvent) -> None:
        await event.finish()

    async def _may_event(self, event: sl.ActionEvent, node: PermissionNode) -> bool:
        if await self._authorize(node):
            return True
        await event.notice(tr(t"You are no longer allowed to change this."))
        return False

    async def set_channel(self, setting: ScalarChannelSetting, channel_id: int | None) -> None:
        previous = self._channels[setting]
        await self._write_channel(setting, channel_id)
        self._channels = {**self._channels, setting: channel_id}
        setting_label = tr(SETTING_LABELS[setting])
        self.history.record(
            tr(t"Changed {setting_label}"),
            compensate=sl.runtime.CompensationSpec(
                lambda _key: self._write_channel(setting, previous),
                lambda commit: f"settings:{self._guild.id}:channel:{setting}:{commit.context.action_id}",
            ),
        )

    async def _write_channel(self, setting: ScalarChannelSetting, channel_id: int | None) -> None:
        """The stored half of a channel change; `_channels` is the framework's to restore."""
        if channel_id is None:
            await self._settings.clear(self._guild.id, setting)
        else:
            await self._settings.set_channel(self._guild.id, setting, channel_id)

    async def set_locale(self, locale: str | None, *, message_root: sd.MessageRoot) -> None:
        previous_override = self._locale_override
        previous_locale = message_root.localization.locale
        await self._write_locale(locale, locale or previous_locale, message_root)
        self._locale_override = locale
        self.history.record(
            tr(t"Changed the bot language"),
            compensate=sl.runtime.CompensationSpec(
                lambda _key: self._write_locale(previous_override, previous_locale, message_root),
                lambda commit: f"settings:{self._guild.id}:locale:{commit.context.action_id}",
            ),
        )

    async def _write_locale(self, override: str | None, effective: str | None, message_root: sd.MessageRoot) -> None:
        """The stored locale and the mount's, neither of which is component state.

        Both halves of the effective locale are captured at the call site rather than read
        back here, because an inverse runs before the framework restores `locale` and
        `_locale_override` -- so reading them here would see the values being reversed.
        """
        await self._settings.set_locale(self._guild.id, override)
        message_root.localize(localization_for(effective))

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
            CardField(tr(SETTING_LABELS[setting]), self._channel_display(self._channels[setting]))
            for setting in CHANNEL_SETTINGS
        ]
        language = (
            self._locale_override
            if self._locale_override is not None
            else tr(t"Following this server's Discord language")
        )
        fields.append(CardField(tr(t"Bot language"), language))
        return fields

    def _channel_display(self, channel_id: int | None) -> sl.TextLike:
        if channel_id is None:
            return tr(t"_Not set_")
        if self._guild.get_channel_or_thread(channel_id) is None:
            return tr(t"_Not found_ ({channel_id})")
        return sl.md("{mention}", mention=sl.raw_md(f"<#{channel_id}>"))

    def _voting_fields(self) -> list[CardField]:
        preset = self._preset
        emojis = (
            "\n".join(f"{option.emoji} — {option.choice.value}" for option in preset.options)
            if preset is not None and preset.options
            else tr(t"_None_")
        )
        weight_params: dict[str, object] = {}
        weight_lines: list[str] = []
        for index, weight in enumerate(self._weights):
            key = f"role_{index}"
            weight_params[key] = self._role_display(weight.role_id)
            weight_lines.append(f"{{{key}}} — {weight.multiplier:g}x")
        weights = sl.text.Message("\n".join(weight_lines), weight_params) if weight_lines else tr(t"_None_")
        return [
            CardField(tr(t"Emojis"), emojis),
            CardField(tr(t"Role multipliers"), weights),
        ]

    def _role_display(self, role_id: int) -> sl.TextLike:
        role = self._guild.get_role(role_id)
        if role is None:
            return tr(t"_Deleted role_ ({role_id})")
        return role.name

    def _scope_note(self) -> sl.TextLike | None:
        if self.kind is not VoteKind.BUILD or self._owner_guild_id in (None, self._guild.id):
            return None
        return tr(t"Build reviews are weighted by the network's own server, so these multipliers do not apply here.")
