"""Canonical starboard configuration workspace."""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from typing import Protocol, override

import squid_ui as sl
import squid_ui_discord as sd
import squid_ui_widgets as sp
from squid.bot.edit_history import history_controls, record_edit, save_edit
from squid.bot.ui import tr
from squid.permissions.domain import PermissionNode
from squid.permissions.domain.catalogue import (
    STARBOARD_BOARD_CREATE,
    STARBOARD_BOARD_DELETE,
    STARBOARD_BOARD_EDIT,
    STARBOARD_EMOJI_EDIT,
    STARBOARD_WEIGHT_EDIT,
)
from squid.starboard.application.history import StarboardSnapshot
from squid.starboard.domain import EDITABLE_SETTINGS, StarboardDirection
from squid.starboard.models import StarboardConfig, StarboardEmoji

type StarboardAuthorizer = Callable[[PermissionNode], Awaitable[bool]]
type BoardCreator = Callable[[int, str, float], Awaitable[StarboardConfig]]


class StarboardOperations(Protocol):
    """Starboard reads and writes used by the configuration screen."""

    async def snapshot(self, starboard_id: int) -> StarboardSnapshot | None: ...

    async def replace_snapshot(
        self, expected: StarboardSnapshot | None, replacement: StarboardSnapshot | None
    ) -> bool: ...

    async def list_for_guild(self, guild_id: int) -> Sequence[StarboardConfig]: ...

    async def get(self, guild_id: int, name: str) -> StarboardConfig | None: ...

    async def delete_starboard(self, guild_id: int, name: str) -> bool: ...

    async def update_settings(self, guild_id: int, name: str, **settings: object) -> StarboardConfig | None: ...

    async def set_emojis(self, config: StarboardConfig, emojis: tuple[StarboardEmoji, ...]) -> None: ...

    async def set_role_multiplier(self, config: StarboardConfig, role_id: int, multiplier: float | None) -> None: ...


class StarboardScreen(sd.Screen):
    """A guild configuration session that rechecks permission before each mutation.

    Initial capabilities choose visible controls; the authorizer checks current
    permission when an action is submitted, including deletion confirmation.
    """

    history: sl.runtime.History = sl.runtime.history(limit=20)
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
        create_board: BoardCreator,
    ) -> None:
        self._operations = operations
        self._guild_id = guild_id
        self._capabilities = capabilities
        self._authorize = authorize
        self._create_board = create_board

    @override
    async def on_load(self) -> None:
        await self._refresh()

    async def _refresh(self) -> None:
        boards = tuple(await self._operations.list_for_guild(self._guild_id))
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
                        tr(t"starboard-delete-confirmation = Delete **{board_name}** and stop mirroring new entries?")
                    ),
                ),
                self.boundary(self._decision, key="delete-decision"),
            )
        if self._tabs is None:
            return (sl.status(tr(t"bot-starboard-screen-loading-starboards = Loading starboards.")),)
        return (self.boundary(self._tabs, key="tabs"), *history_controls(self.history))

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
        nodes: list[sl.LayoutNode[sl.ComponentsV2Target, sd.Request]] = []
        if STARBOARD_BOARD_EDIT in self._capabilities:
            nodes.append(
                sl.form(
                    tr(t"bot-starboard-screen-edit-setting = Edit setting"),
                    _EditSettingForm().spec(),
                    key="edit-setting",
                    on_submit=self._edit,
                )
            )
        if STARBOARD_BOARD_DELETE in self._capabilities:
            nodes.append(
                sl.form(
                    tr(t"bot-starboard-screen-delete-board = Delete board"),
                    _DeleteBoardForm().spec(),
                    key="delete-board",
                    on_submit=self._request_delete,
                )
            )
        return tuple(nodes)

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
        created = await self._create_board(channel_id, name, required)
        record_edit(
            self.history,
            tr(t"starboard-history-create = Create starboard"),
            None,
            StarboardSnapshot.capture(created, {}),
            target=f"starboard:{created.id}",
            authorize=lambda: self._authorize(STARBOARD_BOARD_CREATE),
            replace=self._operations.replace_snapshot,
            refresh=self._refresh,
        )
        await self._refresh()
        created_name = created.name
        await event.notice(tr(t"bot-starboard-screen-created-starboard = Created starboard **{created_name}**."))

    async def _edit(self, event: sl.SubmitEvent) -> None:
        if not await self._may(event, STARBOARD_BOARD_EDIT):
            return
        form = _EditSettingForm.from_submission(event)
        name = form.name
        setting = form.setting
        value = _parse_setting(setting, form.value)
        config = await self._named(event, name)
        if config is None:
            return
        before = await self._operations.snapshot(config.id)
        if before is not None:
            after = replace(
                before, settings=tuple((key, value if key == setting else old) for key, old in before.settings)
            )
            await self._save(before, after, STARBOARD_BOARD_EDIT)

    async def _save(self, before: StarboardSnapshot, after: StarboardSnapshot, permission: PermissionNode) -> None:
        await save_edit(
            self.history,
            tr(t"starboard-history-edit = Edit starboard"),
            before,
            after,
            target=f"starboard:{before.id}",
            authorize=lambda: self._authorize(permission),
            replace=self._operations.replace_snapshot,
            refresh=self._refresh,
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
        before = await self._operations.snapshot(config.id)
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
            await self._save(before, after, STARBOARD_EMOJI_EDIT)

    async def _edit_weight(self, event: sl.SubmitEvent) -> None:
        if not await self._may(event, STARBOARD_WEIGHT_EDIT):
            return
        form = _EditWeightForm.from_submission(event)
        config = await self._named(event, form.name)
        if config is None:
            return
        role_id = form.role_id
        multiplier = form.multiplier
        before = await self._operations.snapshot(config.id)
        if before is not None:
            weights = dict(before.weights)
            if multiplier is None:
                weights.pop(role_id, None)
            else:
                weights[role_id] = multiplier
            await self._save(before, replace(before, weights=tuple(sorted(weights.items()))), STARBOARD_WEIGHT_EDIT)

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
        deleted = await self._operations.delete_starboard(self._guild_id, name)
        if deleted:
            self.history.clear()
        self._deleting = None
        self._decision = None
        await self._refresh()
        await event.source.notice(
            tr(t"bot-starboard-screen-starboard-deleted = Starboard deleted.")
            if deleted
            else tr(t"starboard-not-found = No starboard with that name exists.")
        )

    async def _named(self, event: sl.ActionEvent, name: str) -> StarboardConfig | None:
        config = await self._operations.get(self._guild_id, name)
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
    emojis = " ".join(f"{item.emoji} ({item.direction}, {item.multiplier:g}x)" for item in board.emojis) or "—"
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
