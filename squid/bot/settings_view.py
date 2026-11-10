"""Interactive Components V2 rendering for server settings."""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, cast, override

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
    "Smallest": tr(t"bot-settings-view-smallest-record-builds = Smallest-record builds"),
    "Fastest": tr(t"bot-settings-view-fastest-record-builds = Fastest-record builds"),
    "First": tr(t"bot-settings-view-first-of-a-kind-builds = First-of-a-kind builds"),
    "Builds": tr(t"bot-settings-view-confirmed-builds = Confirmed builds"),
    "Vote": tr(t"bot-settings-view-builds-awaiting-review = Builds awaiting review"),
}
"""What each channel setting is for, named rather than title-cased so it translates."""

KIND_LABELS: dict[VoteKind, Message] = {
    VoteKind.BUILD: tr(t"bot-settings-view-build-reviews = Build reviews"),
    VoteKind.DELETE_LOG: tr(t"bot-settings-view-deletion-votes = Deletion votes"),
    VoteKind.GENERIC: tr(t"bot-settings-view-polls = Polls"),
}


@dataclass(frozen=True, slots=True)
class SettingsCapabilities:
    """Capabilities captured when the panel opens, used to choose visible pages and controls.

    Action handlers use the separate authorizer for permission checks; these flags
    do not refresh automatically when a grant changes.
    """

    view_server: bool
    edit_server: bool
    edit_voting: bool


type SettingsAuthorizer = Callable[[PermissionNode], Awaitable[bool]]


class SettingsPanel(sd.Screen):
    """Edit a guild's publication channels, language, voting emojis, and role weights.

    Changes are saved as the user makes them. Channel and language changes also
    record undo actions for this UI session, including the writes needed to restore
    stored settings; voting changes are saved without that history.
    """

    policy = sd.ResponseSpec(timeout=SESSION_SECONDS, session=sd.SessionSpec("settings", scope=sd.ScopeKind.USER_GUILD))

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

    @override
    async def on_load(self) -> None:
        """Load server settings when visible, otherwise load voting settings."""
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

    @override
    def render(self) -> Sequence[sl.LayoutNode[sl.ComponentsV2Target, sd.Request]]:
        if self.page == "voting":
            return self._voting_nodes()
        return self._server_nodes()

    def _server_nodes(self) -> Sequence[sl.LayoutNode[sl.ComponentsV2Target, sd.Request]]:
        description = (
            tr(
                t"bot-settings-view-change-as-many-as-you-like-an = Change as many as you like; an emptied "
                t"picker clears that setting."
            )
            if self._capabilities.edit_server
            else None
        )
        children: list[sl.LayoutNode[sl.ComponentsV2Target, sd.Request]] = [
            sl.section(
                sl.heading(tr(t"bot-settings-view-server-settings = Server settings")),
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
                        placeholder=SETTING_LABELS[setting],
                    )
                )
            children.append(
                sl.choices(
                    *(
                        sl.choice(
                            tr(t"bot-settings-view-follow-discord = Follow Discord") if tag == FOLLOW_DISCORD else tag,
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
            actions.append(sl.action_control(tr(t"bot-settings-view-undo = Undo"), self._undo, key="undo"))
        if self._capabilities.edit_server and self.history.can_redo:
            actions.append(sl.action_control(tr(t"bot-settings-view-redo = Redo"), self._redo, key="redo"))
        if self.shows_voting:
            actions.append(sl.action_control(tr(t"bot-settings-view-voting = Voting"), self._show_voting, key="voting"))
        children.append(sl.action_controls(*actions, key="server-actions"))
        return children

    def _voting_nodes(self) -> Sequence[sl.LayoutNode[sl.ComponentsV2Target, sd.Request]]:
        scope_note = self._scope_note()
        kind_label = KIND_LABELS[self.kind]
        children: list[sl.LayoutNode[sl.ComponentsV2Target, sd.Request]] = [
            sl.section(
                sl.heading(tr(t"bot-settings-view-voting-2 = Voting — {kind_label}")),
                sl.fields(*(sl.field(field.name, field.value) for field in self._voting_fields())),
                scope_note and sl.note(scope_note),
            ),
            sl.choices(
                *(sl.choice(label, key=kind.value) for kind, label in KIND_LABELS.items()),
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
                    placeholder=tr(t"bot-settings-view-choose-a-role = Choose a role"),
                )
            )
        actions: list[sl.semantic.ActionControl] = []
        if self._capabilities.edit_voting:
            actions.extend(
                (
                    sl.action_control(
                        tr(t"bot-settings-view-edit-emojis = Edit emojis"),
                        self._edit_emojis,
                        key="edit-emojis",
                        emphasis=sl.semantic.Emphasis.STRONG,
                    ),
                    sl.action_control(
                        tr(t"bot-settings-view-confirm-reset = Confirm reset")
                        if self.confirming_reset
                        else tr(t"bot-settings-view-reset = Reset"),
                        self._reset,
                        key="reset",
                        tone=sl.Tone.DANGER if self.confirming_reset else sl.Tone.NEUTRAL,
                    ),
                )
            )
        if self.shows_server:
            actions.append(sl.action_control(tr(t"bot-settings-view-back = Back"), self._show_server, key="server"))
        children.append(sl.action_controls(*actions, key="voting-actions"))
        return children

    async def _channel_changed(self, event: sl.EntityEvent, setting: ScalarChannelSetting) -> None:
        if not await self._may_event(event, SETTINGS_SERVER_EDIT):
            return
        channel_id = event.selected[0].id if event.selected else None
        if channel_id is not None and not isinstance(channel_id, int):
            await event.notice(tr(t"settings-channel-invalid = That channel selection is invalid."))
            return
        await self.set_channel(setting, channel_id)

    async def _locale_changed(self, event: sd.ChoiceEvent) -> None:
        if not await self._may_event(event, SETTINGS_SERVER_EDIT):
            return
        request = event.context
        assert request.root is not None
        await self.set_locale(
            None if event.selected[0] == FOLLOW_DISCORD else event.selected[0],
            message_root=request.root,
        )

    async def _kind_changed(self, event: sl.ChoiceEvent) -> None:
        await self.open_voting(VoteKind(event.selected[0]))

    async def _role_changed(self, event: sl.EntityEvent) -> None:
        if not await self._may_event(event, SETTINGS_VOTING_EDIT):
            return
        role_id = event.selected[0].id
        if not isinstance(role_id, int):
            await event.notice(
                tr(t"bot-settings-view-that-role-selection-is-invalid = That role selection is invalid.")
            )
            return
        role = self._guild.get_role(role_id)
        if role is None:
            await event.notice(tr(t"bot-settings-view-that-role-has-been-deleted = That role has been deleted."))
            return
        role_name = role.name
        await event.present_form(
            replace(
                _VoteWeightForm(
                    multiplier=f"{current:g}" if (current := self.weight_for(role.id)) is not None else "",
                ).spec(),
                title=tr(t"bot-settings-view-vote-weight-for = Vote weight for {role_name}"),
            ),
            key="role-weight",
            on_submit=lambda form_event: self._role_weight_submitted(form_event, role.id),
        )

    async def _edit_emojis(self, event: sl.PressEvent) -> None:
        if await self._may_event(event, SETTINGS_VOTING_EDIT):
            kind = self.kind.value
            await event.present_form(
                replace(
                    _VoteEmojisForm(aliases=self.emoji_preset_text()).spec(),
                    title=tr(t"bot-settings-view-vote-emojis = {kind} vote emojis"),
                ),
                key="vote-emojis",
                on_submit=self._emoji_form_submitted,
            )

    async def _role_weight_submitted(self, event: sl.SubmitEvent, role_id: int) -> None:
        form = _VoteWeightForm.from_submission(event)
        text = (form.multiplier or "").strip()
        try:
            await self.set_weight(role_id, float(text) if text else None)
        except InvalidVoteConfigurationError, ValueError:
            await event.notice(
                tr(t"settings-vote-multiplier-invalid = A vote multiplier must be a positive number, such as 1.5.")
            )

    async def _emoji_form_submitted(self, event: sl.SubmitEvent) -> None:
        form = _VoteEmojisForm.from_submission(event)
        text = form.aliases
        options: list[VoteOption] = []
        for position, line in enumerate(filter(None, (line.strip() for line in text.splitlines()))):
            parts = [part.strip() for part in line.split("|", 1)]
            if len(parts) != 2:
                await event.notice(tr(t"settings-emoji-format-error = Each line must read `choice | emoji`."))
                return
            choice_text, emoji = parts
            try:
                choice = VoteChoice.GENERIC if self.kind is VoteKind.GENERIC else VoteChoice(choice_text)
            except ValueError:
                await event.notice(
                    tr(t"bot-settings-view-is-not-a-vote-choice = `{choice_text}` is not a vote choice.")
                )
                return
            parsed = discord.PartialEmoji.from_str(emoji)
            if parsed.is_custom_emoji():
                custom = self._guild.get_emoji(parsed.id or 0)
                if custom is None or not custom.is_usable():
                    await event.notice(
                        tr(t"settings-custom-emoji-unavailable = The custom emoji {emoji} is inaccessible.")
                    )
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
            await event.notice(tr(t"bot-settings-view-undid = Undid: {change}"))
        elif result.status is sl.runtime.HistoryResultStatus.CONFLICT:
            await event.notice(
                tr(t"settings-undo-conflict = That change was modified elsewhere and cannot be undone safely.")
            )

    async def _redo(self, event: sl.PressEvent) -> None:
        if not await self._may_event(event, SETTINGS_SERVER_EDIT):
            return
        result = await self.history.redo()
        if result.applied and result.entry is not None:
            change = result.entry.label
            await event.notice(tr(t"bot-settings-view-redid = Redid: {change}"))
        elif result.status is sl.runtime.HistoryResultStatus.CONFLICT:
            await event.notice(
                tr(t"settings-redo-conflict = That change was modified elsewhere and cannot be redone safely.")
            )

    async def _show_voting(self, event: sl.PressEvent) -> None:
        await self.open_voting()

    async def _show_server(self, event: sl.PressEvent) -> None:
        await self.open_server()

    async def _may_event(self, event: sl.ActionEvent, node: PermissionNode) -> bool:
        if await self._authorize(node):
            return True
        await event.notice(tr(t"settings-change-permission-denied = You are no longer allowed to change this."))
        return False

    async def set_channel(self, setting: ScalarChannelSetting, channel_id: int | None) -> None:
        """Write a channel selection and record its previous value for undo.

        The caller must authorize the write. None clears the setting.
        """
        previous = self._channels[setting]
        await self._write_channel(setting, channel_id)
        self._channels = {**self._channels, setting: channel_id}
        setting_label = SETTING_LABELS[setting]
        self.history.record(
            tr(t"bot-settings-view-changed = Changed {setting_label}"),
            compensate=sl.runtime.CompensationSpec(
                lambda _key: self._write_channel(setting, previous),
                lambda commit: f"settings:{self._guild.id}:channel:{setting}:{commit.context.action_id}",
            ),
        )

    async def _write_channel(self, setting: ScalarChannelSetting, channel_id: int | None) -> None:
        """Persist a channel change; history restores the component's cached value separately."""
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
            tr(t"bot-settings-view-changed-the-bot-language = Changed the bot language"),
            compensate=sl.runtime.CompensationSpec(
                lambda _key: self._write_locale(previous_override, previous_locale, message_root),
                lambda commit: f"settings:{self._guild.id}:locale:{commit.context.action_id}",
            ),
        )

    async def _write_locale(self, override: str | None, effective: str | None, message_root: sd.MessageRoot) -> None:
        """Update stored locale and mounted localization from explicitly captured values.

        Compensation runs before component state is restored, so it must use the
        supplied previous values rather than reading the state being undone.
        """
        await self._settings.set_locale(self._guild.id, override)
        message_root.localize(localization_for(effective))

    async def set_weight(self, role_id: int, multiplier: float | None) -> None:
        """Set or remove a role multiplier for the selected vote kind, then reload the page.

        None removes the override. The caller must authorize the operation.
        """
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
            CardField(SETTING_LABELS[setting], self._channel_display(self._channels[setting]))
            for setting in CHANNEL_SETTINGS
        ]
        language = (
            self._locale_override
            if self._locale_override is not None
            else tr(t"settings-follow-server-language = Following this server's Discord language")
        )
        fields.append(CardField(tr(t"bot-settings-view-bot-language = Bot language"), language))
        return fields

    def _channel_display(self, channel_id: int | None) -> sl.TextLike:
        if channel_id is None:
            return tr(t"bot-settings-view-not-set = _Not set_")
        if self._guild.get_channel_or_thread(channel_id) is None:
            return tr(t"bot-settings-view-not-found = _Not found_ ({(channel_id_text := str(channel_id))})")  # noqa: F841 - captured text identifier
        return sl.md("{mention}", mention=sl.raw_md(f"<#{channel_id}>"))

    def _voting_fields(self) -> list[CardField]:
        preset = self._preset
        emojis = (
            "\n".join(f"{option.emoji} — {option.choice.value}" for option in preset.options)
            if preset is not None and preset.options
            else tr(t"bot-settings-view-none = _None_")
        )
        weight_lines = [
            tr(
                t"bot-settings-role-multiplier = {(role := self._role_display(weight.role_id))} — {(multiplier := format(weight.multiplier, 'g'))}x"  # noqa: F841 - captured Fluent bindings
            )
            for weight in self._weights
        ]
        weights = sl.text.Message.join("\n", weight_lines) if weight_lines else tr(t"bot-settings-view-none = _None_")
        return [
            CardField(tr(t"bot-settings-view-emojis = Emojis"), emojis),
            CardField(tr(t"bot-settings-view-role-multipliers = Role multipliers"), weights),
        ]

    def _role_display(self, role_id: int) -> sl.TextLike:
        role = self._guild.get_role(role_id)
        if role is None:
            return tr(t"bot-settings-view-deleted-role = _Deleted role_ ({(role_id_text := str(role_id))})")  # noqa: F841 - captured text identifier
        return role.name

    def _scope_note(self) -> sl.TextLike | None:
        if self.kind is not VoteKind.BUILD or self._owner_guild_id in (None, self._guild.id):
            return None
        return tr(
            t"settings-review-weighting-explained = Build reviews are weighted by "
            t"the network's own server, so these multipliers do not apply here."
        )


class _VoteWeightForm(sl.forms.Form):
    multiplier = sl.forms.TextField(
        label=tr(t"settings-role-multiplier-help = Multiplier; leave empty to remove this role's weight"),
        placeholder="1.5",
        required=False,
        maximum=16,
    )


class _VoteEmojisForm(sl.forms.Form):
    aliases = sl.forms.TextAreaField(
        label=tr(t"bot-settings-view-one-choice-emoji-per-line = One `choice | emoji` per line"),
        placeholder="approve | ✅\ndeny | ❌",
        minimum=1,
        maximum=1000,
    )
