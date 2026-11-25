"""Canonical starboard configuration workspace."""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from functools import partial
from typing import Any, Protocol, cast, override

import discord

import squid_ui as sl
import squid_ui.ext.discord as sd
import squid_ui_widgets as sp
from squid.bot.edit_controller import EditController
from squid.bot.ui import tr
from squid.bot.utils.permissions import Grants, requires_grant
from squid.core.actors import ActingContext, Actor
from squid.core.edits.application import EditRunner
from squid.core.edits.domain import EditValues
from squid.core.permissions.domain.catalogue import (
    STARBOARD_BOARD_CREATE,
    STARBOARD_BOARD_DELETE,
    STARBOARD_BOARD_EDIT,
    STARBOARD_EMOJI_EDIT,
    STARBOARD_WEIGHT_EDIT,
)
from squid.core.starboard.application.edits import (
    STARBOARD_CREATE,
    STARBOARD_DELETE,
    STARBOARD_EDIT,
    BoardPresence,
    BoardValues,
    starboard_target,
)
from squid.core.starboard.application.snapshots import StarboardSnapshot
from squid.core.starboard.domain import EDITABLE_SETTINGS, StarboardDirection
from squid.core.starboard.models import StarboardConfig, StarboardEmoji
from squid_i18n import Text, emoji_text, md_tr
from squid_ui.ext.discord.form_fields import EntityField
from squid_ui.ext.discord.target_types import ComponentsV2Target

type ChannelValidator = Callable[[int], Awaitable[None]]


class StarboardOperations(Protocol):
    """Starboard reads and writes used by the configuration screen."""

    async def snapshot(self, starboard_id: int, *, context: ActingContext) -> StarboardSnapshot | None: ...

    async def reserve_id(self, *, context: ActingContext) -> int: ...

    async def list_for_guild(self, guild_id: int, *, context: ActingContext) -> Sequence[StarboardConfig]: ...


class StarboardScreen(sd.Screen):
    """A guild configuration session that rechecks permission before each mutation.

    Grants held at open choose visible controls; each action, including deletion
    confirmation, rechecks them before it runs.
    """

    _snapshots: Mapping[int, StarboardSnapshot] = sl.state({})
    _browser: sp.Browser[StarboardConfig, ComponentsV2Target, sd.Request] | None = sl.state(default=None)
    _tabs: sp.ComponentDriver[sp.TabsState, ComponentsV2Target, sd.Request] | None = sl.state(default=None)
    _deleting: StarboardSnapshot | None = sl.state(default=None)
    _decision: sp.ComponentDriver[sp.DecisionState, ComponentsV2Target, sd.Request] | None = sl.state(default=None)

    policy = sd.ResponseSpec(timeout=300, session=sd.SessionSpec("starboard", scope=sd.ScopeKind.USER_GUILD))

    def __init__(
        self,
        operations: StarboardOperations,
        *,
        guild_id: int,
        grants: Grants,
        edits: EditRunner,
        actor: Callable[[], Awaitable[Actor]],
        validate_channel: ChannelValidator,
        role_label: Callable[[int], sl.TextLike] | None = None,
    ) -> None:
        self._operations = operations
        self._guild_id = guild_id
        self.grants = grants
        self._validate_channel = validate_channel
        self._role_label = role_label or (lambda _id: tr(t"starboard-unknown-role = Deleted role"))
        self.edits = EditController(edits, actor, self._refresh)

    @override
    async def on_load(self) -> None:
        await self._refresh()

    async def _refresh(self) -> None:
        focus = self._browser.session.focus if self._browser is not None else None
        snapshots = {}
        for board in await self._operations.list_for_guild(self._guild_id, context=await self.edits.context()):
            snapshot = await self._operations.snapshot(board.id, context=await self.edits.context())
            if snapshot is not None:
                snapshots[snapshot.id] = snapshot
        self._snapshots = snapshots
        boards = tuple(snapshot.config() for snapshot in snapshots.values())
        source = sl.sources.list_source(boards)
        self._browser = sp.Browser(
            source,
            key="boards",
            identity=lambda board: str(board.id),
            label=lambda board: board.name,
            summary=lambda board: f"{board.name} · #{board.channel_id} · {board.required:g} votes",
            detail=self._board_editor,
            fetch_size=10,
            title=tr(t"bot-starboard-screen-starboards = Starboards"),
            empty=tr(t"starboard-none-configured = No starboards are configured."),
        )
        tabs = [sp.Tab("boards", tr(t"bot-starboard-screen-boards = Boards"), self._browser)]
        if STARBOARD_BOARD_CREATE in self.grants:
            tabs.append(sp.Tab("create", tr(t"bot-starboard-screen-create = Create"), self._create_nodes()))
        if focus is not None:
            offset = next((index // 10 * 10 for index, board in enumerate(boards) if str(board.id) == focus), 0)
            batch = await source.fetch(sl.sources.Position(offset=offset), 10)
            self._browser.session.replace(
                sl.sources.LoadedBatch(batch, sl.sources.batch_fingerprint(batch.items, lambda item: str(item.id)))
            )
            self._browser.session.open(focus, revision=self._browser.session.revision)
        self._tabs = sp.Tabs(
            tabs,
            key="starboard-tabs",
            title=tr(t"bot-starboard-screen-starboard-configuration = Starboard configuration"),
        )

    @override
    def render(self) -> tuple[sl.LayoutNode[ComponentsV2Target, sd.Request], ...]:
        if self._deleting is not None and self._decision is not None:
            board_name = self._deleting.config().name
            return (
                sl.section(
                    sl.heading(tr(t"bot-starboard-screen-delete-starboard = Delete starboard")),
                    sl.paragraph(
                        md_tr(
                            t"starboard-delete-confirmation = Delete **{board_name}** and stop mirroring new entries?",
                        )
                    ),
                ),
                self._decision,
            )
        if self._tabs is None:
            return (sl.status(tr(t"bot-starboard-screen-loading-starboards = Loading starboards.")),)
        return (self._tabs, *self.edits.refresh_controls())

    def _create_nodes(self) -> tuple[sl.LayoutNode[ComponentsV2Target, sd.Request], ...]:
        return (
            sl.section(
                sl.heading(tr(t"starboard-create-heading = Highlight community favourites")),
                sl.paragraph(
                    tr(
                        t"starboard-create-help = Create a starboard, then choose which reactions count and where "
                        t"highlighted messages appear."
                    )
                ),
            ),
            sl.form(
                tr(t"bot-starboard-screen-create-starboard = Create starboard"),
                _CreateBoardForm().spec(),
                key="create-board",
                on_submit=self._create,
            ),
        )

    def _board_editor(self, board: StarboardConfig) -> tuple[sl.LayoutNode[ComponentsV2Target, sd.Request], ...]:
        snapshot = self._snapshots[board.id]
        sections: list[sp.Tab[ComponentsV2Target, sd.Request]] = []
        if STARBOARD_BOARD_EDIT in self.grants:
            sections.append(
                sp.Tab(
                    "settings",
                    tr(t"bot-starboard-screen-settings = Settings"),
                    sl.choices(
                        *(
                            sl.choice(key.replace("_", " ").title(), key=key, description=str(value))
                            for key, value in snapshot.settings
                        ),
                        key="setting",
                        selection=sl.controlled((), partial(self._choose_setting, snapshot)),
                    ),
                )
            )
        if STARBOARD_EMOJI_EDIT in self.grants:
            sections.append(
                sp.Tab(
                    "emojis",
                    tr(t"bot-starboard-screen-emojis = Emojis"),
                    (
                        sl.form(
                            tr(t"starboard-add-emoji = Add or replace emoji"),
                            _EditEmojiForm().spec(),
                            key="edit-emoji",
                            on_submit=partial(self._edit_emoji, snapshot),
                        ),
                        sp.Browser(
                            sl.sources.list_source(snapshot.aliases()),
                            key="emojis",
                            identity=lambda item: item.emoji,
                            label=lambda item: item.emoji,
                            detail=lambda item: sl.action_controls(
                                sl.action_control(
                                    tr(t"starboard-remove-emoji = Remove emoji"),
                                    partial(self._remove_emoji, snapshot, item.emoji),
                                    key="remove-emoji",
                                    tone=sl.Tone.DANGER,
                                ),
                                key="emoji-actions",
                            ),
                        ),
                    ),
                )
            )
        if STARBOARD_WEIGHT_EDIT in self.grants:
            sections.append(
                sp.Tab(
                    "weights",
                    tr(t"bot-starboard-screen-role-weights = Role weights"),
                    (
                        sl.form(
                            tr(t"bot-starboard-screen-edit-role-weight = Edit role weight"),
                            _EditWeightForm().spec(),
                            key="edit-weight",
                            on_submit=partial(self._edit_weight, snapshot),
                        ),
                        sp.Browser(
                            sl.sources.list_source(snapshot.weights),
                            key="weights",
                            identity=lambda item: str(item[0]),
                            label=lambda item: self._role_label(item[0]),
                            detail=lambda item: sl.stack(
                                sl.paragraph(f"<@&{item[0]}> · {item[1]:g}x"),
                                sl.action_controls(
                                    sl.action_control(
                                        tr(t"starboard-remove-weight = Remove weight"),
                                        partial(self._remove_weight, snapshot, item[0]),
                                        key="remove-weight",
                                        tone=sl.Tone.DANGER,
                                    ),
                                    key="weight-actions",
                                ),
                            ),
                        ),
                    ),
                )
            )
        return (
            _board_detail(board),
            sp.Tabs(sections, key="board-editor") if sections else sl.note(tr(t"starboard-read-only = Read only")),
            sl.action_controls(
                sl.action_control(
                    tr(t"bot-starboard-screen-delete-board = Delete board"),
                    partial(self._request_delete, snapshot),
                    key="delete-board",
                    tone=sl.Tone.DANGER,
                ),
                key="board-actions",
            )
            if STARBOARD_BOARD_DELETE in self.grants
            else sl.note(""),
        )

    @requires_grant(STARBOARD_BOARD_EDIT)
    async def _choose_setting(self, snapshot: StarboardSnapshot, event: sl.ChoiceEvent) -> None:
        setting = event.selected[0]
        await event.present_form(
            _setting_form(setting, dict(snapshot.settings)[setting]),
            key="edit-setting",
            on_submit=partial(self._edit, snapshot, setting),
        )

    @requires_grant(STARBOARD_BOARD_CREATE)
    async def _create[ContextT](self, event: sl.SubmitEvent[ContextT]) -> None:
        form = _CreateBoardForm.from_submission(event)
        channel_id = cast(discord.abc.Snowflake, form.channel_id).id
        name = form.name
        required = form.required
        await self._validate_channel(channel_id)
        identity = await self._operations.reserve_id(context=await self.edits.context())
        created = StarboardConfig(
            id=identity,
            guild_id=self._guild_id,
            channel_id=channel_id,
            name=name,
            required=required,
            emojis=(
                StarboardEmoji(emoji="⭐", direction="up", position=0),
                StarboardEmoji(emoji="💩", direction="down", position=1),
            ),
        )
        values = BoardPresence(**BoardValues.capture(StarboardSnapshot.capture(created, {})).model_dump(), present=True)
        await self.edits.bind(STARBOARD_CREATE, starboard_target(identity), 0).save(
            values.values(), tr(t"starboard-history-create = Create starboard")
        )
        created_name = created.name
        await event.notice(md_tr(t"bot-starboard-screen-created-starboard = Created starboard **{created_name}**."))

    @requires_grant(STARBOARD_BOARD_EDIT)
    async def _edit[ContextT](self, before: StarboardSnapshot, setting: str, event: sl.SubmitEvent[ContextT]) -> None:
        value = event.values["value"]
        if setting == "channel_id":
            value = cast(discord.abc.Snowflake, value).id
            await self._validate_channel(value)
        after = replace(before, settings=tuple((key, value if key == setting else old) for key, old in before.settings))
        await self._save(before, after)

    async def _save(self, before: StarboardSnapshot, after: StarboardSnapshot) -> None:
        await self.edits.bind(STARBOARD_EDIT, starboard_target(before.id), before.revision).save(
            BoardValues.capture(after).values(), tr(t"starboard-history-edit = Edit starboard")
        )

    @requires_grant(STARBOARD_EMOJI_EDIT)
    async def _edit_emoji[ContextT](self, before: StarboardSnapshot, event: sl.SubmitEvent[ContextT]) -> None:
        form = _EditEmojiForm.from_submission(event)
        aliases = tuple(item for item in before.aliases() if item.emoji != form.emoji)
        aliases += (
            StarboardEmoji(
                emoji=form.emoji, direction=form.direction, multiplier=form.multiplier, position=len(aliases)
            ),
        )
        await self._save(
            before,
            replace(
                before, emojis=tuple((item.emoji, item.direction, item.multiplier, item.position) for item in aliases)
            ),
        )

    @requires_grant(STARBOARD_EMOJI_EDIT)
    async def _remove_emoji(self, before: StarboardSnapshot, emoji: str, event: sl.ActionEvent[Any]) -> None:
        await self._save(before, replace(before, emojis=tuple(item for item in before.emojis if item[0] != emoji)))

    @requires_grant(STARBOARD_WEIGHT_EDIT)
    async def _edit_weight[ContextT](self, before: StarboardSnapshot, event: sl.SubmitEvent[ContextT]) -> None:
        form = _EditWeightForm.from_submission(event)
        role_id = cast(discord.abc.Snowflake, form.role_id).id
        weights = dict(before.weights)
        weights[role_id] = form.multiplier
        await self._save(before, replace(before, weights=tuple(sorted(weights.items()))))

    @requires_grant(STARBOARD_WEIGHT_EDIT)
    async def _remove_weight(self, before: StarboardSnapshot, role_id: int, event: sl.ActionEvent[Any]) -> None:
        await self._save(before, replace(before, weights=tuple(item for item in before.weights if item[0] != role_id)))

    @requires_grant(STARBOARD_BOARD_DELETE)
    async def _request_delete(self, snapshot: StarboardSnapshot, event: sl.ActionEvent[Any]) -> None:
        self._deleting = snapshot
        self._decision = sp.Decision[ComponentsV2Target, sd.Request](
            tr(t"starboard-delete-audit-retention = Deleting a starboard cannot be undone from this session."),
            (
                sp.DecisionOption("confirm", tr(t"bot-starboard-screen-delete = Delete"), sl.Tone.DANGER),
                sp.DecisionOption("cancel", tr(t"bot-starboard-screen-cancel = Cancel")),
            ),
            key="delete-starboard",
        ).build_component(on_decide=self._delete)

    async def _delete(self, event: sp.TransitionEvent[sp.DecisionState], choice: str) -> None:
        snapshot = self._deleting
        if snapshot is None or choice == "cancel":
            self._deleting = None
            self._decision = None
            return
        if not await self.grants.confirm(event.source, STARBOARD_BOARD_DELETE):
            self._deleting = None
            self._decision = None
            return
        await self.edits.bind(STARBOARD_DELETE, starboard_target(snapshot.id), snapshot.revision).save(
            EditValues.of({}), tr(t"bot-starboard-screen-delete-starboard = Delete starboard")
        )
        self._deleting = None
        self._decision = None
        await event.source.notice(tr(t"bot-starboard-screen-starboard-deleted = Starboard deleted."))


def _board_detail(board: StarboardConfig) -> sl.semantic.Fields:
    emojis = (
        Text.join(
            " ", (Text(t"{emoji_text(item.emoji)} ({item.direction}, {item.multiplier:g}x)") for item in board.emojis)
        )
        or "—"
    )
    return sl.fields(
        sl.field(tr(t"bot-starboard-screen-channel = Channel"), f"<#{board.channel_id}>"),
        sl.field(tr(t"bot-starboard-screen-post-threshold = Post threshold"), f"{board.required:g}"),
        sl.field(tr(t"bot-starboard-screen-removal-threshold = Removal threshold"), f"{board.required_remove:g}"),
        sl.field(tr(t"bot-starboard-screen-emojis = Emojis"), emojis),
    )


def _setting_form(setting: str, value: object) -> sl.forms.FormSpec[sd.Request[Any, Any]]:
    label = setting.replace("_", " ").title()
    field: sl.forms.FormField[Any, Any]
    if setting == "channel_id":
        field = EntityField(
            key="value",
            label=label,
            default=value,
            entity_type=sl.entity.EntityType.CONVERSATION,
            channel_types=(discord.ChannelType.text,),
        )
    elif EDITABLE_SETTINGS[setting] == "boolean":
        field = sl.forms.BoolField(key="value", label=label, default=bool(value))
    elif EDITABLE_SETTINGS[setting] == "threshold":
        field = sl.forms.FloatField(key="value", label=label, default=cast(float, value))
    elif EDITABLE_SETTINGS[setting] == "integer":
        field = sl.forms.IntField(key="value", label=label, default=cast(int, value))
    else:
        field = sl.forms.TextField(key="value", label=label, default=str(value), maximum=200)
    return sl.forms.FormSpec[sd.Request[Any, Any]](label, (field,))


class _CreateBoardForm(sl.forms.Form[sd.Request[Any, Any]]):
    title = tr(t"bot-starboard-screen-create-starboard = Create starboard")

    name = sl.forms.TextField(label=tr(t"bot-starboard-screen-name = Name"), default="main", maximum=100)
    channel_id = EntityField(
        label=tr(t"starboard-destination-channel = Destination channel"),
        entity_type=sl.entity.EntityType.CONVERSATION,
        channel_types=(discord.ChannelType.text,),
    )
    required = sl.forms.FloatField(label=tr(t"bot-starboard-screen-post-threshold = Post threshold"), default=3.0)


class _EditEmojiForm(sl.forms.Form[sd.Request[Any, Any]]):
    title = tr(t"starboard-emoji-editor-title = Add or remove starboard emoji")

    emoji = sl.forms.TextField(label=tr(t"bot-starboard-screen-emoji = Emoji"), maximum=100)
    direction = sl.forms.ChoiceField[StarboardDirection](
        label=tr(t"bot-starboard-screen-direction = Direction"),
        default="up",
        options=(
            sl.forms.ChoiceOption("up", tr(t"bot-starboard-screen-up = Up"), "up"),
            sl.forms.ChoiceOption("down", tr(t"bot-starboard-screen-down = Down"), "down"),
        ),
    )
    multiplier = sl.forms.FloatField(label=tr(t"bot-starboard-screen-multiplier = Multiplier"), default=1.0)


class _EditWeightForm(sl.forms.Form[sd.Request[Any, Any]]):
    title = tr(t"starboard-role-multiplier-action = Set or remove role multiplier")

    role_id = EntityField(label=tr(t"starboard-role = Role"), entity_type=sl.entity.EntityType.ROLE)
    multiplier = sl.forms.FloatField(label=tr(t"bot-starboard-screen-multiplier = Multiplier"), default=1.0)
