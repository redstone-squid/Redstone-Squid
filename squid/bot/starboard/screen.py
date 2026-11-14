"""Canonical starboard configuration workspace."""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from typing import Protocol, override

import squid_ui as sl
import squid_ui_discord as sd
import squid_ui_widgets as sp
from squid.bot.edit_controller import EditController
from squid.bot.ui import tr
from squid.core.actors import Actor
from squid.edits.application import EditRunner
from squid.edits.domain import EditValues
from squid.permissions.domain import PermissionNode
from squid.permissions.domain.catalogue import (
    STARBOARD_BOARD_CREATE,
    STARBOARD_BOARD_DELETE,
    STARBOARD_BOARD_EDIT,
    STARBOARD_EMOJI_EDIT,
    STARBOARD_WEIGHT_EDIT,
)
from squid.starboard.application.edits import (
    STARBOARD_CREATE,
    STARBOARD_DELETE,
    STARBOARD_EDIT,
    BoardPresence,
    BoardValues,
    starboard_target,
)
from squid.starboard.application.snapshots import StarboardSnapshot
from squid.starboard.domain import EDITABLE_SETTINGS, StarboardDirection
from squid.starboard.models import StarboardConfig, StarboardEmoji
from squid_i18n import Text, emoji_text, md_tr

type StarboardAuthorizer = Callable[[PermissionNode], Awaitable[bool]]
type ChannelValidator = Callable[[int], Awaitable[None]]


class StarboardOperations(Protocol):
    """Starboard reads and writes used by the configuration screen."""

    async def snapshot(self, starboard_id: int) -> StarboardSnapshot | None: ...

    async def reserve_id(self) -> int: ...

    async def list_for_guild(self, guild_id: int) -> Sequence[StarboardConfig]: ...


class StarboardScreen(sd.Screen):
    """A guild configuration session that rechecks permission before each mutation.

    Initial capabilities choose visible controls; the authorizer checks current
    permission when an action is submitted, including deletion confirmation.
    """

    _snapshots: Mapping[str, StarboardSnapshot] = sl.state({}, persist=False)
    _browser: sp.Browser[StarboardConfig, sl.ComponentsV2Target, sd.Request] | None = sl.state(
        default=None, persist=False
    )
    _tabs: sp.ComponentDriver[sp.TabsState, sl.ComponentsV2Target, sd.Request] | None = sl.state(
        default=None, persist=False
    )
    _deleting: str | None = sl.state(default=None, persist=False)
    _decision: sp.ComponentDriver[sp.DecisionState, sl.ComponentsV2Target, sd.Request] | None = sl.state(
        default=None, persist=False
    )

    policy = sd.ResponseSpec(timeout=300, session=sd.SessionSpec("starboard", scope=sd.ScopeKind.USER_GUILD))

    def __init__(
        self,
        operations: StarboardOperations,
        *,
        guild_id: int,
        capabilities: frozenset[PermissionNode],
        authorize: StarboardAuthorizer,
        edits: EditRunner,
        actor: Callable[[], Awaitable[Actor]],
        validate_channel: ChannelValidator,
    ) -> None:
        self._operations = operations
        self._guild_id = guild_id
        self._capabilities = capabilities
        self._authorize = authorize
        self._validate_channel = validate_channel
        self.edits = EditController(edits, actor, self._refresh)

    @override
    async def on_load(self) -> None:
        await self._refresh()

    async def _refresh(self) -> None:
        snapshots = {}
        for board in await self._operations.list_for_guild(self._guild_id):
            snapshot = await self._operations.snapshot(board.id)
            if snapshot is not None:
                snapshots[str(dict(snapshot.settings)["name"]).casefold()] = snapshot
        self._snapshots = snapshots
        boards = tuple(snapshot.config() for snapshot in snapshots.values())
        self._browser = sp.Browser(
            sl.sources.list_source(boards),
            key="boards",
            identity=lambda board: board.name,
            label=lambda board: board.name,
            summary=lambda board: f"{board.name} · #{board.channel_id} · {board.required:g} votes",
            detail=_board_detail,
            page_size=10,
            title=tr(t"bot-starboard-screen-starboards = Starboards"),
            empty=tr(t"starboard-none-configured = No starboards are configured."),
        )
        tabs = [sp.Tab("boards", tr(t"bot-starboard-screen-boards = Boards"), self._browser)]
        if STARBOARD_BOARD_CREATE in self._capabilities:
            tabs.append(sp.Tab("create", tr(t"bot-starboard-screen-create = Create"), self._create_nodes()))
        if STARBOARD_BOARD_EDIT in self._capabilities or STARBOARD_BOARD_DELETE in self._capabilities:
            tabs.append(sp.Tab("settings", tr(t"bot-starboard-screen-settings = Settings"), self._settings_nodes()))
        if STARBOARD_EMOJI_EDIT in self._capabilities:
            tabs.append(sp.Tab("emojis", tr(t"bot-starboard-screen-emojis = Emojis"), self._emoji_nodes()))
        if STARBOARD_WEIGHT_EDIT in self._capabilities:
            tabs.append(
                sp.Tab("weights", tr(t"bot-starboard-screen-role-weights = Role weights"), self._weight_nodes())
            )
        self._tabs = sp.Tabs(
            tabs,
            key="starboard-tabs",
            title=tr(t"bot-starboard-screen-starboard-configuration = Starboard configuration"),
        )

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        if self._deleting is not None and self._decision is not None:
            board_name = self._deleting
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

    def _create_nodes(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        return (
            sl.form(
                tr(t"bot-starboard-screen-create-starboard = Create starboard"),
                _CreateBoardForm().spec(),
                key="create-board",
                on_submit=self._create,
            ),
        )

    def _settings_nodes(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        may_edit = STARBOARD_BOARD_EDIT in self._capabilities
        may_delete = STARBOARD_BOARD_DELETE in self._capabilities
        if not (may_edit or may_delete):
            return ()
        return (
            sl.action_controls(
                may_edit
                and sl.form(
                    tr(t"bot-starboard-screen-edit-setting = Edit setting"),
                    _EditSettingForm().spec(),
                    key="edit-setting",
                    on_submit=self._edit,
                ),
                may_delete
                and sl.form(
                    tr(t"bot-starboard-screen-delete-board = Delete board"),
                    _DeleteBoardForm().spec(),
                    key="delete-board",
                    on_submit=self._request_delete,
                ),
                key="settings-forms",
            ),
        )

    def _emoji_nodes(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        return (
            sl.form(
                tr(t"bot-starboard-screen-edit-emoji = Edit emoji"),
                _EditEmojiForm().spec(),
                key="edit-emoji",
                on_submit=self._edit_emoji,
            ),
        )

    def _weight_nodes(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        return (
            sl.form(
                tr(t"bot-starboard-screen-edit-role-weight = Edit role weight"),
                _EditWeightForm().spec(),
                key="edit-weight",
                on_submit=self._edit_weight,
            ),
        )

    async def _create(self, event: sl.SubmitEvent) -> None:
        if not await self._may(event, STARBOARD_BOARD_CREATE):
            return
        form = _CreateBoardForm.from_submission(event)
        channel_id = form.channel_id
        name = form.name
        required = form.required
        await self._validate_channel(channel_id)
        identity = await self._operations.reserve_id()
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

    async def _edit(self, event: sl.SubmitEvent) -> None:
        if not await self._may(event, STARBOARD_BOARD_EDIT):
            return
        form = _EditSettingForm.from_submission(event)
        name = form.name
        setting = form.setting
        value = _parse_setting(setting, form.value)
        if setting == "channel_id":
            assert isinstance(value, int)
            await self._validate_channel(value)
        config = await self._named(event, name)
        if config is None:
            return
        before = self._snapshots[config.name.casefold()]
        if before is not None:
            after = replace(
                before, settings=tuple((key, value if key == setting else old) for key, old in before.settings)
            )
            await self._save(before, after)

    async def _save(self, before: StarboardSnapshot, after: StarboardSnapshot) -> None:
        await self.edits.bind(STARBOARD_EDIT, starboard_target(before.id), before.revision).save(
            BoardValues.capture(after).values(), tr(t"starboard-history-edit = Edit starboard")
        )

    async def _edit_emoji(self, event: sl.SubmitEvent) -> None:
        if not await self._may(event, STARBOARD_EMOJI_EDIT):
            return
        form = _EditEmojiForm.from_submission(event)

        config = await self._named(event, form.name)
        if config is None:
            return
        emoji = form.emoji
        operation = form.operation
        before = self._snapshots[config.name.casefold()]
        if before is None:
            return
        aliases = tuple(item for item in before.aliases() if item.emoji != emoji)
        if operation == "add":
            direction = form.direction
            multiplier = form.multiplier
            aliases += (StarboardEmoji(emoji=emoji, direction=direction, multiplier=multiplier, position=len(aliases)),)
        if before is not None:
            after = replace(
                before, emojis=tuple((item.emoji, item.direction, item.multiplier, item.position) for item in aliases)
            )
            await self._save(before, after)

    async def _edit_weight(self, event: sl.SubmitEvent) -> None:
        if not await self._may(event, STARBOARD_WEIGHT_EDIT):
            return
        form = _EditWeightForm.from_submission(event)
        config = await self._named(event, form.name)
        if config is None:
            return
        role_id = form.role_id
        multiplier = form.multiplier
        before = self._snapshots[config.name.casefold()]
        if before is not None:
            weights = dict(before.weights)
            if multiplier is None:
                weights.pop(role_id, None)
            else:
                weights[role_id] = multiplier
            await self._save(before, replace(before, weights=tuple(sorted(weights.items()))))

    async def _request_delete(self, event: sl.SubmitEvent) -> None:
        if not await self._may(event, STARBOARD_BOARD_DELETE):
            return
        form = _DeleteBoardForm.from_submission(event)
        self._deleting = form.name
        self._decision = sp.Decision[sl.ComponentsV2Target, sd.Request](
            tr(t"starboard-delete-audit-retention = Deleting a starboard cannot be undone from this session."),
            (
                sp.DecisionOption("confirm", tr(t"bot-starboard-screen-delete = Delete"), sl.Tone.DANGER),
                sp.DecisionOption("cancel", tr(t"bot-starboard-screen-cancel = Cancel")),
            ),
            key="delete-starboard",
        ).build_component(on_decide=self._delete)

    async def _delete(self, event: sp.TransitionEvent[sp.DecisionState], choice: str) -> None:
        name = self._deleting
        if name is None or choice == "cancel":
            self._deleting = None
            self._decision = None
            return
        if not await self._may(event.source, STARBOARD_BOARD_DELETE):
            self._deleting = None
            self._decision = None
            return
        snapshot = self._snapshots.get(name.casefold())
        deleted = snapshot is not None
        if snapshot is not None:
            await self.edits.bind(STARBOARD_DELETE, starboard_target(snapshot.id), snapshot.revision).save(
                EditValues.of({}), tr(t"bot-starboard-screen-delete-starboard = Delete starboard")
            )
        self._deleting = None
        self._decision = None
        await event.source.notice(
            tr(t"bot-starboard-screen-starboard-deleted = Starboard deleted.")
            if deleted
            else tr(t"starboard-not-found = No starboard with that name exists.")
        )

    async def _named(self, event: sl.ActionEvent, name: str) -> StarboardConfig | None:
        snapshot = self._snapshots.get(name.casefold())
        config = None if snapshot is None else snapshot.config()
        if config is None:
            await event.notice(tr(t"starboard-not-found = No starboard with that name exists."))
        return config

    async def _may(self, event: sl.ActionEvent, node: PermissionNode) -> bool:
        if await self._authorize(node):
            return True
        await event.notice(
            tr(
                t"starboard-operation-permission-denied = You are no longer allowed to "
                t"perform this starboard operation."
            )
        )
        return False


def _board_detail(board: StarboardConfig) -> sl.semantic.Fields:
    emojis = (
        Text.join(
            " ", (Text(t"{emoji_text(item.emoji)} ({item.direction}, {item.multiplier:g}x)") for item in board.emojis)
        )
        or "—"
    )
    return sl.fields(
        sl.field(tr(t"bot-starboard-screen-channel = Channel"), str(board.channel_id)),
        sl.field(tr(t"bot-starboard-screen-post-threshold = Post threshold"), f"{board.required:g}"),
        sl.field(tr(t"bot-starboard-screen-removal-threshold = Removal threshold"), f"{board.required_remove:g}"),
        sl.field(tr(t"bot-starboard-screen-emojis = Emojis"), emojis),
    )


def _parse_setting(setting: str, value: str) -> object:
    match EDITABLE_SETTINGS.get(setting):
        case "boolean":
            normalized = value.casefold()
            if normalized not in {"true", "false", "on", "off", "yes", "no"}:
                message = "Boolean settings accept true or false."
                raise ValueError(message)
            return normalized in {"true", "on", "yes"}
        case "threshold":
            return float(value)
        case "integer":
            return int(value, 0)
        case "text":
            return value
        case _:
            message = f"Unknown starboard setting: {setting}"
            raise ValueError(message)


class _CreateBoardForm(sl.forms.Form):
    title = tr(t"bot-starboard-screen-create-starboard = Create starboard")

    name = sl.forms.TextField(label=tr(t"bot-starboard-screen-name = Name"), default="main", maximum=100)
    channel_id = sl.forms.IntField(
        label=tr(t"bot-starboard-screen-destination-channel-id = Destination channel ID"), minimum=1
    )
    required = sl.forms.FloatField(label=tr(t"bot-starboard-screen-post-threshold = Post threshold"), default=3.0)


class _EditSettingForm(sl.forms.Form):
    title = tr(t"bot-starboard-screen-edit-starboard-setting = Edit starboard setting")

    name = sl.forms.TextField(label=tr(t"bot-starboard-screen-starboard-name = Starboard name"), maximum=100)
    setting = sl.forms.ChoiceField(
        label=tr(t"bot-starboard-screen-setting = Setting"),
        options=tuple(sl.forms.ChoiceOption(key, key.replace("_", " ").title(), key) for key in EDITABLE_SETTINGS),
    )
    value = sl.forms.TextField(label=tr(t"bot-starboard-screen-value = Value"), maximum=200)


class _DeleteBoardForm(sl.forms.Form):
    title = tr(t"bot-starboard-screen-choose-starboard-to-delete = Choose starboard to delete")

    name = sl.forms.TextField(label=tr(t"bot-starboard-screen-starboard-name = Starboard name"), maximum=100)


class _EditEmojiForm(sl.forms.Form):
    title = tr(t"starboard-emoji-editor-title = Add or remove starboard emoji")

    name = sl.forms.TextField(label=tr(t"bot-starboard-screen-starboard-name = Starboard name"), maximum=100)
    operation = sl.forms.ChoiceField(
        label=tr(t"bot-starboard-screen-operation = Operation"),
        options=(
            sl.forms.ChoiceOption("add", tr(t"bot-starboard-screen-add-or-replace = Add or replace"), "add"),
            sl.forms.ChoiceOption("remove", tr(t"bot-starboard-screen-remove = Remove"), "remove"),
        ),
    )
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


class _EditWeightForm(sl.forms.Form):
    title = tr(t"starboard-role-multiplier-action = Set or remove role multiplier")

    name = sl.forms.TextField(label=tr(t"bot-starboard-screen-starboard-name = Starboard name"), maximum=100)
    role_id = sl.forms.IntField(label=tr(t"bot-starboard-screen-role-id = Role ID"), minimum=1)
    multiplier = sl.forms.FloatField(
        label=tr(t"starboard-role-multiplier-help = Multiplier; leave empty to remove"),
        required=False,
    )
