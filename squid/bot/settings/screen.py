"""Interactive Components V2 rendering for server settings."""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, cast, override

import discord

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.bot.diagnostics.permissions import GuildPermissionDiagnostics
from squid.bot.edit_controller import EditController
from squid.bot.i18n import localization_for
from squid.bot.settings.vote_emoji_editor import VoteEmojiEditor
from squid.bot.ui import CardField, tr
from squid.bot.utils.permissions import Grants, requires_grant
from squid.core.actors import Actor
from squid.core.i18n import SUPPORTED_LOCALES
from squid.edits.application import EditRunner
from squid.edits.domain import EditValues
from squid.permissions.domain.catalogue import (
    SETTINGS_SERVER_EDIT,
    SETTINGS_SERVER_VIEW,
    SETTINGS_VOTING_EDIT,
    STARBOARD_BOARD_VIEW,
)
from squid.settings.application.edits import GUILD_EDIT, guild_target
from squid.settings.domain import ScalarChannelSetting
from squid.settings.domain.guild import CHANNEL_FIELDS, GuildSettings
from squid.voting.domain import EmojiPreset, RoleWeight, VoteChoice, VoteKind, VoteOption, normalize_vote_options
from squid.voting.domain.models import DEFAULT_VOTE_OPTIONS, VoteConfiguration
from squid.voting.errors import InvalidVoteConfigurationError
from squid_i18n import NUMBER, Text, emoji_text, md_tr
from squid_ui.ext.discord.target_types import ComponentsV2Target

if TYPE_CHECKING:
    from squid.bot.app import RedstoneSquid
    from squid.settings.application import SettingsService

type SettingsRequest = sd.Request[Any, RedstoneSquid]

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

SETTING_LABELS: dict[ScalarChannelSetting, Text] = {
    "Smallest": tr(t"bot-settings-view-smallest-record-builds = Smallest-record builds"),
    "Fastest": tr(t"bot-settings-view-fastest-record-builds = Fastest-record builds"),
    "First": tr(t"bot-settings-view-first-of-a-kind-builds = First-of-a-kind builds"),
    "Builds": tr(t"bot-settings-view-confirmed-builds = Confirmed builds"),
    "Vote": tr(t"bot-settings-view-builds-awaiting-review = Builds awaiting review"),
}
"""What each channel setting is for, named rather than title-cased so it translates."""

KIND_LABELS: dict[VoteKind, Text] = {
    VoteKind.BUILD: tr(t"bot-settings-view-build-reviews = Build reviews"),
    VoteKind.DELETE_LOG: tr(t"bot-settings-view-deletion-votes = Deletion votes"),
    VoteKind.GENERIC: tr(t"bot-settings-view-polls = Polls"),
}


@dataclass(slots=True)
class _SettingsLocalization:
    # Keep the mounted root binding even when a later render rolls back UI values.
    root: sd.MessageRoot | None = None
    fallback: str | None = None


class SettingsPanel(sd.Screen):
    """Compose server and voting editors with one guild revision and edit history."""

    policy = sd.ResponseSpec(timeout=SESSION_SECONDS, session=sd.SessionSpec("settings", scope=sd.ScopeKind.USER_GUILD))
    page: str = sl.state("server")
    diagnostics: GuildPermissionDiagnostics | None = sl.state(None, opaque=True)

    def __init__(
        self,
        *,
        settings: SettingsService,
        edits: EditRunner,
        actor: Callable[[], Awaitable[Actor]],
        guild: discord.Guild,
        grants: Grants,
        owner_guild_id: int | None = None,
    ) -> None:
        self._guild = guild
        self._owner_guild_id = owner_guild_id
        self.settings = SettingsEditor(settings, guild.id, grants, edits, actor)
        self.edits = self.settings.edits
        self.server = ServerSettings(self.settings, guild, editable=SETTINGS_SERVER_EDIT in grants)
        self.voting = VotingSettings(
            self.settings, guild, editable=SETTINGS_VOTING_EDIT in grants, owner_guild_id=owner_guild_id
        )

    @property
    def grants(self) -> Grants:
        return self.settings.grants

    @property
    def shows_server(self) -> bool:
        return SETTINGS_SERVER_VIEW in self.settings.grants or SETTINGS_SERVER_EDIT in self.settings.grants

    @property
    def shows_voting(self) -> bool:
        return SETTINGS_SERVER_VIEW in self.settings.grants or SETTINGS_VOTING_EDIT in self.settings.grants

    @override
    async def on_load(self) -> None:
        """Load server settings when visible, otherwise load voting settings."""
        if self.shows_server:
            await self.open_server()
        else:
            await self.open_voting()

    async def open_server(self) -> None:
        await self.settings.refresh()
        self.page = "server"

    async def open_voting(self, kind: VoteKind | None = None) -> None:
        if kind is not None:
            self.voting.kind = kind
        await self.settings.refresh()
        self.voting.confirming_reset = False
        self.page = "voting"

    @override
    def render(self) -> Sequence[sl.LayoutNode[ComponentsV2Target, sd.Request]]:
        navigation = sl.action_controls(
            self.shows_server
            and sl.action_control(
                tr(t"settings-tab-server = Server"),
                self._show_server,
                key="server",
                available=self.page != "server",
                emphasis=sl.semantic.Emphasis.STRONG if self.page == "server" else sl.semantic.Emphasis.NORMAL,
            ),
            self.shows_voting
            and sl.action_control(
                tr(t"bot-settings-view-voting = Voting"),
                self._show_voting,
                key="voting",
                available=self.page != "voting",
                emphasis=sl.semantic.Emphasis.STRONG if self.page == "voting" else sl.semantic.Emphasis.NORMAL,
            ),
            self.shows_server
            and sl.action_control(
                tr(t"settings-tab-diagnostics = Diagnostics"), self._show_diagnostics, key="diagnostics"
            ),
            key="settings-navigation",
            display=sl.semantic.ControlDisplay.INDIVIDUAL,
        )
        if self.page == "diagnostics" and self.diagnostics is not None:
            content = self.diagnostics
        elif self.page == "voting":
            content = self.voting
        else:
            content = self.server
        return (navigation, content, *self.edits.refresh_controls())

    @requires_grant(SETTINGS_SERVER_VIEW, SETTINGS_SERVER_EDIT)
    async def _show_diagnostics(self, event: sl.PressEvent) -> None:
        await self.settings.refresh()
        snapshot = self.settings.snapshot
        assert snapshot is not None
        request = cast(SettingsRequest, event.context)
        services = request.client.services
        boards = (
            await services.starboards.list_for_guild(self._guild.id)
            if await self.settings.grants.allows(STARBOARD_BOARD_VIEW)
            else ()
        )
        role_id = None
        if self._guild.id == self._owner_guild_id:
            role_id = (await services.settings.get_runtime()).redstoner_role_id
        self.diagnostics = GuildPermissionDiagnostics(self._guild, snapshot, boards, redstoner_role_id=role_id)
        self.page = "diagnostics"

    async def _show_voting(self, event: sl.PressEvent) -> None:
        await self.open_voting()

    async def _show_server(self, event: sl.PressEvent) -> None:
        await self.open_server()


class SettingsEditor(sl.Reactive):
    """Own the guild snapshot and revision shared by both settings pages."""

    snapshot: GuildSettings | None = sl.state(None)
    revision: int = sl.state(0)

    def __init__(
        self,
        settings: SettingsService,
        guild_id: int,
        grants: Grants,
        edits: EditRunner,
        actor: Callable[[], Awaitable[Actor]],
    ) -> None:
        self._settings = settings
        self._guild_id = guild_id
        self.grants = grants
        self.localization = _SettingsLocalization()
        self.edits = EditController(edits, actor, self.refresh)

    async def refresh(self) -> None:
        self.snapshot, self.revision = await self._settings.guild_snapshot(self._guild_id)
        if self.localization.root is not None:
            self.localization.root.localize(localization_for(self.snapshot.locale or self.localization.fallback))

    async def save(self, values: EditValues, label: sl.TextLike) -> None:
        if self.snapshot is None:
            await self.refresh()
        await self.edits.bind(GUILD_EDIT, guild_target(self._guild_id), self.revision).save(values, label)


class ServerSettings(sl.Component[ComponentsV2Target, sd.Request]):
    """Edit publication channels and language from the shared guild snapshot."""

    def __init__(self, settings: SettingsEditor, guild: discord.Guild, *, editable: bool) -> None:
        self._settings = settings
        self._guild = guild
        self._editable = editable

    @property
    def grants(self) -> Grants:
        return self._settings.grants

    @property
    def locale_override(self) -> str | None:
        return None if self._settings.snapshot is None else self._settings.snapshot.locale

    def channel_id(self, setting: ScalarChannelSetting) -> int | None:
        snapshot = self._settings.snapshot
        return None if snapshot is None else getattr(snapshot, CHANNEL_FIELDS[setting])

    @override
    def render(self) -> Sequence[sl.LayoutNode[ComponentsV2Target, sd.Request]]:
        description = (
            tr(
                t"bot-settings-view-change-as-many-as-you-like-an = Change as many as you like; an emptied "
                t"picker clears that setting."
            )
            if self._editable
            else None
        )
        children: list[sl.LayoutNode[ComponentsV2Target, sd.Request]] = [
            sl.section(
                sl.heading(tr(t"bot-settings-view-server-settings = Server settings")),
                description and sl.paragraph(description).truncate(),
                sl.fields(*(sl.field(field.name, field.value) for field in self._server_fields())),
            )
        ]
        if self._editable:
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
                    selection=sl.controlled((self.locale_override or FOLLOW_DISCORD,), self._locale_changed),
                )
            )
        return children

    @requires_grant(SETTINGS_SERVER_EDIT)
    async def _channel_changed(self, event: sl.EntityEvent, setting: ScalarChannelSetting) -> None:
        channel_id = event.selected[0].id if event.selected else None
        if channel_id is not None and not isinstance(channel_id, int):
            await event.notice(tr(t"settings-channel-invalid = That channel selection is invalid."))
            return
        await self.set_channel(setting, channel_id)

    @requires_grant(SETTINGS_SERVER_EDIT)
    async def _locale_changed(self, event: sd.ChoiceEvent) -> None:
        request = event.context
        assert request.root is not None
        await self.set_locale(
            None if event.selected[0] == FOLLOW_DISCORD else event.selected[0],
            message_root=request.root,
        )

    async def set_channel(self, setting: ScalarChannelSetting, channel_id: int | None) -> None:
        setting_label = SETTING_LABELS[setting]
        await self._settings.save(
            EditValues.of({CHANNEL_FIELDS[setting]: channel_id}),
            md_tr(t"bot-settings-view-changed = Changed {setting_label}"),
        )

    async def set_locale(self, locale: str | None, *, message_root: sd.MessageRoot) -> None:
        if self._settings.localization.root is None:
            self._settings.localization.fallback = message_root.localization.locale
        self._settings.localization.root = message_root
        await self._settings.save(
            EditValues.of({"locale": locale}),
            tr(t"bot-settings-view-changed-the-bot-language = Changed the bot language"),
        )

    def _server_fields(self) -> list[CardField]:
        fields = [
            CardField(SETTING_LABELS[setting], self._channel_display(self.channel_id(setting)))
            for setting in CHANNEL_SETTINGS
        ]
        language = (
            self.locale_override
            if self.locale_override is not None
            else tr(t"settings-follow-server-language = Following this server's Discord language")
        )
        fields.append(CardField(tr(t"bot-settings-view-bot-language = Bot language"), language))
        return fields

    def _channel_display(self, channel_id: int | None) -> sl.TextLike:
        if channel_id is None:
            return md_tr(t"bot-settings-view-not-set = _Not set_")
        if self._guild.get_channel_or_thread(channel_id) is None:
            return md_tr(
                t"bot-settings-view-not-found = _Not found_ ({channel_id!s})",
            )
        return sl.mention(channel_id, kind="channel")


class VotingSettings(sl.Component[ComponentsV2Target, sd.Request]):
    """Own voting kind selection, configuration forms, and reset confirmation."""

    kind: VoteKind = sl.state(VoteKind.BUILD)
    confirming_reset: bool = sl.state(False)

    def __init__(
        self,
        settings: SettingsEditor,
        guild: discord.Guild,
        *,
        editable: bool,
        owner_guild_id: int | None,
    ) -> None:
        self._settings = settings
        self._guild = guild
        self._editable = editable
        self._owner_guild_id = owner_guild_id

    @property
    def grants(self) -> Grants:
        return self._settings.grants

    @property
    def configuration(self) -> VoteConfiguration:
        snapshot = self._settings.snapshot
        return VoteConfiguration(None, ()) if snapshot is None else snapshot.voting[self.kind]

    @override
    def render(self) -> Sequence[sl.LayoutNode[ComponentsV2Target, sd.Request]]:
        scope_note = self._scope_note()
        kind_label = KIND_LABELS[self.kind]
        children: list[sl.LayoutNode[ComponentsV2Target, sd.Request]] = [
            sl.section(
                sl.heading(md_tr(t"bot-settings-view-voting-2 = Voting — {kind_label}")),
                sl.fields(*(sl.field(field.name, field.value) for field in self._voting_fields())),
                scope_note and sl.note(scope_note),
            ),
            sl.choices(
                *(sl.choice(label, key=kind.value) for kind, label in KIND_LABELS.items()),
                key="vote-kind",
                selection=sl.controlled((self.kind.value,), self._kind_changed),
            ),
        ]
        if self._editable:
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
        if self._editable:
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
        children.append(sl.action_controls(*actions, key="voting-actions"))
        return children

    def emoji_preset_text(self) -> str:
        return (
            "\n".join(f"{option.choice.value} | {option.emoji}" for option in self.configuration.preset.options)
            if self.configuration.preset
            else ""
        )

    def weight_for(self, role_id: int) -> float | None:
        return next((weight.multiplier for weight in self.configuration.weights if weight.role_id == role_id), None)

    @requires_grant(SETTINGS_VOTING_EDIT)
    async def _role_changed(self, event: sl.EntityEvent) -> None:
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
                title=md_tr(t"bot-settings-view-vote-weight-for = Vote weight for {role_name}"),
            ),
            key="role-weight",
            on_submit=lambda form_event: self._role_weight_submitted(form_event, role.id),
        )

    @requires_grant(SETTINGS_VOTING_EDIT)
    async def _edit_emojis(self, event: sl.PressEvent) -> None:
        snapshot = self._settings.snapshot
        assert snapshot is not None
        revision, kind = self._settings.revision, self.kind
        before = snapshot.voting[kind]
        options = (
            before.preset.options
            if before.preset is not None
            else (
                tuple(replace(option, guild_id=self._guild.id) for option in DEFAULT_VOTE_OPTIONS)
                if kind is not VoteKind.GENERIC
                else ()
            )
        )
        source = cast(sd.PressEvent, event)
        assert source.context.root is not None
        parent = source.context.root.component
        assert isinstance(parent, sd.Screen)

        async def selected(result: sd.ScreenResultEvent[tuple[VoteOption, ...]]) -> None:
            if not await self.grants.confirm(result, SETTINGS_VOTING_EDIT):
                return
            after = replace(before, preset=EmojiPreset(self._guild.id, kind, result.value))
            values = snapshot.model_copy(update={"voting": snapshot.voting | {kind: after}})
            await self._settings.edits.bind(GUILD_EDIT, guild_target(self._guild.id), revision).save(
                EditValues.of({"voting": values.model_dump(mode="json")["voting"]}), KIND_LABELS[kind]
            )

        await parent.open_child(source, VoteEmojiEditor(self._guild, kind, options), on_result=selected)

    @requires_grant(SETTINGS_VOTING_EDIT)
    async def _role_weight_submitted(self, event: sl.SubmitEvent, role_id: int) -> None:
        form = _VoteWeightForm.from_submission(event)
        text = (form.multiplier or "").strip()
        try:
            await self.set_weight(role_id, float(text) if text else None)
        except InvalidVoteConfigurationError, ValueError:
            await event.notice(
                tr(t"settings-vote-multiplier-invalid = A vote multiplier must be a positive number, such as 1.5.")
            )

    @requires_grant(SETTINGS_VOTING_EDIT)
    async def _emoji_form_submitted(self, event: sl.SubmitEvent) -> None:
        form = _VoteEmojisForm.from_submission(event)
        text = form.aliases
        options: list[VoteOption] = []
        for position, line in enumerate(filter(None, (line.strip() for line in text.splitlines()))):
            parts = [part.strip() for part in line.split("|", 1)]
            if len(parts) != 2:
                await event.notice(md_tr(t"settings-emoji-format-error = Each line must read `choice | emoji`."))
                return
            choice_text, emoji = parts
            try:
                choice = VoteChoice.GENERIC if self.kind is VoteKind.GENERIC else VoteChoice(choice_text)
            except ValueError:
                await event.notice(
                    md_tr(
                        t"bot-settings-view-is-not-a-vote-choice = `{choice_text}` is not a vote choice.",
                    )
                )
                return
            parsed = discord.PartialEmoji.from_str(emoji)
            if parsed.is_custom_emoji():
                custom = self._guild.get_emoji(parsed.id or 0)
                if custom is None or not custom.is_usable():
                    await event.notice(
                        md_tr(
                            t"settings-custom-emoji-unavailable = The custom emoji {emoji} is inaccessible.",
                        )
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

    @requires_grant(SETTINGS_VOTING_EDIT)
    async def _reset(self, event: sl.PressEvent) -> None:
        if self.confirming_reset:
            await self.reset_voting()
        else:
            self.arm_reset()

    async def set_weight(self, role_id: int, multiplier: float | None) -> None:
        if self._settings.snapshot is None:
            await self._settings.refresh()
        assert self._settings.snapshot is not None
        before = self._settings.snapshot.voting[self.kind]
        weights = tuple(weight for weight in before.weights if weight.role_id != role_id)
        if multiplier is not None:
            weights += (RoleWeight(self._guild.id, self.kind, role_id, multiplier),)
        await self._save_voting(replace(before, weights=tuple(sorted(weights, key=lambda weight: weight.role_id))))

    async def set_emojis(self, options: Sequence[VoteOption]) -> None:
        if self._settings.snapshot is None:
            await self._settings.refresh()
        assert self._settings.snapshot is not None
        before = self._settings.snapshot.voting[self.kind]
        await self._save_voting(
            replace(
                before, preset=EmojiPreset(self._guild.id, self.kind, normalize_vote_options(options, kind=self.kind))
            )
        )

    async def _save_voting(self, after: VoteConfiguration) -> None:
        assert self._settings.snapshot is not None
        value = self._settings.snapshot.model_copy(
            update={"voting": self._settings.snapshot.voting | {self.kind: after}}
        )
        await self._settings.save(
            EditValues.of({"voting": value.model_dump(mode="json")["voting"]}), KIND_LABELS[self.kind]
        )
        self.confirming_reset = False

    def arm_reset(self) -> None:
        self.confirming_reset = True

    async def reset_voting(self) -> None:
        await self._save_voting(VoteConfiguration(None, ()))

    def _voting_fields(self) -> list[CardField]:
        preset = self.configuration.preset
        emojis = (
            Text.join("\n", (Text(t"{emoji_text(option.emoji)} — {option.choice.value}") for option in preset.options))
            if preset is not None and preset.options
            else md_tr(t"bot-settings-view-none = _None_")
        )
        weight_lines: list[Text] = []
        for weight in self.configuration.weights:
            role = self._role_display(weight.role_id)
            multiplier = weight.multiplier
            weight_lines.append(
                md_tr(
                    t"bot-settings-role-multiplier = {role} — "
                    t"{NUMBER(multiplier, use_grouping=False, maximum_significant_digits=6)}x",
                )
            )
        weights = sl.text.Text.join("\n", weight_lines) if weight_lines else md_tr(t"bot-settings-view-none = _None_")
        return [
            CardField(tr(t"bot-settings-view-emojis = Emojis"), emojis),
            CardField(tr(t"bot-settings-view-role-multipliers = Role multipliers"), weights),
        ]

    def _role_display(self, role_id: int) -> sl.TextLike:
        role = self._guild.get_role(role_id)
        if role is None:
            return md_tr(
                t"bot-settings-view-deleted-role = _Deleted role_ ({role_id!s})",
            )
        return role.name

    def _scope_note(self) -> sl.TextLike | None:
        if self.kind is not VoteKind.BUILD or self._owner_guild_id in (None, self._guild.id):
            return None
        return tr(
            t"settings-review-weighting-explained = Build reviews are weighted by "
            t"the network's own server, so these multipliers do not apply here."
        )

    async def _kind_changed(self, event: sl.ChoiceEvent) -> None:
        self.kind = VoteKind(event.selected[0])
        await self._settings.refresh()
        self.confirming_reset = False


class _VoteWeightForm(sl.forms.Form[sd.Request[Any, Any]]):
    multiplier = sl.forms.TextField(
        label=tr(t"settings-role-multiplier-help = Multiplier; leave empty to remove this role's weight"),
        placeholder="1.5",
        required=False,
        maximum=16,
    )


class _VoteEmojisForm(sl.forms.Form[sd.Request[Any, Any]]):
    aliases = sl.forms.TextAreaField(
        label=md_tr(t"bot-settings-view-one-choice-emoji-per-line = One `choice | emoji` per line"),
        placeholder="approve | ✅\ndeny | ❌",
        minimum=1,
        maximum=1000,
    )
