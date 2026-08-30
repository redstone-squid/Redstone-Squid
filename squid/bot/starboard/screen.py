"""Canonical starboard configuration workspace."""

from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol, cast

import squid_ui as sl
import squid_ui_discord as sd
import squid_ui_widgets as sp
from squid.bot.ui import tr
from squid.permissions.domain import PermissionNode
from squid.permissions.domain.catalogue import (
    STARBOARD_BOARD_CREATE,
    STARBOARD_BOARD_DELETE,
    STARBOARD_BOARD_EDIT,
    STARBOARD_EMOJI_EDIT,
    STARBOARD_WEIGHT_EDIT,
)
from squid.starboard.domain import EDITABLE_SETTINGS, StarboardConfig, StarboardDirection, StarboardEmoji

type StarboardAuthorizer = Callable[[PermissionNode], Awaitable[bool]]
type BoardCreator = Callable[[int, str, float], Awaitable[StarboardConfig]]


class StarboardOperations(Protocol):
    """Starboard reads and writes used by the configuration screen."""

    async def list_for_guild(self, guild_id: int) -> Sequence[StarboardConfig]: ...

    async def get(self, guild_id: int, name: str) -> StarboardConfig | None: ...

    async def delete_starboard(self, guild_id: int, name: str) -> bool: ...

    async def update_settings(self, guild_id: int, name: str, **settings: object) -> StarboardConfig | None: ...

    async def set_emojis(self, config: StarboardConfig, emojis: tuple[StarboardEmoji, ...]) -> None: ...

    async def set_role_multiplier(self, config: StarboardConfig, role_id: int, multiplier: float | None) -> None: ...


class StarboardScreen(sd.Screen):
    """A guild starboard workspace that ends when closed, replaced, or timed out."""

    session = sd.SessionSpec("starboard", scope=sd.ScopeKind.USER_GUILD)
    timeout = 300
    audience = "personal"

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
        self._browser: sp.Browser[StarboardConfig, sl.ComponentsV2Target] | None = None
        self._tabs: sp.ComponentDriver[sp.TabsState, sl.ComponentsV2Target] | None = None
        self._deleting: str | None = None
        self._decision: sp.ComponentDriver[sp.DecisionState, sl.ComponentsV2Target] | None = None

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
            title=tr(t"Starboards"),
            empty=tr(t"No starboards are configured."),
        )
        tabs = [sp.Tab("boards", tr(t"Boards"), self._browser)]
        if STARBOARD_BOARD_CREATE in self._capabilities:
            tabs.append(sp.Tab("create", tr(t"Create"), self._create_nodes()))
        if STARBOARD_BOARD_EDIT in self._capabilities or STARBOARD_BOARD_DELETE in self._capabilities:
            tabs.append(sp.Tab("settings", tr(t"Settings"), self._settings_nodes()))
        if STARBOARD_EMOJI_EDIT in self._capabilities:
            tabs.append(sp.Tab("emojis", tr(t"Emojis"), self._emoji_nodes()))
        if STARBOARD_WEIGHT_EDIT in self._capabilities:
            tabs.append(sp.Tab("weights", tr(t"Role weights"), self._weight_nodes()))
        self._tabs = sp.Tabs(tabs, key="starboard-tabs", title=tr(t"Starboard configuration")).build_component()

    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        if self._deleting is not None and self._decision is not None:
            board_name = self._deleting
            return (
                sl.section(
                    sl.heading(tr(t"Delete starboard")),
                    sl.paragraph(tr(t"Delete **{board_name}** and stop mirroring new entries?")),
                ),
                self.boundary(self._decision, key="delete-decision"),
            )
        if self._tabs is None:
            return (sl.status(tr(t"Loading starboards.")),)
        return (
            self.boundary(self._tabs, key="tabs"),
            sl.action_controls(sl.action_control(tr(t"Close"), self._close, key="close"), key="starboard-actions"),
        )

    def _create_nodes(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        return (
            sl.form(
                tr(t"Create starboard"),
                sl.forms.FormSpec(
                    tr(t"Create starboard"),
                    (
                        sl.forms.TextField(key="name", label=tr(t"Name"), default="main", maximum=100),
                        sl.forms.IntField(key="channel_id", label=tr(t"Destination channel ID"), minimum=1),
                        sl.forms.FloatField(key="required", label=tr(t"Post threshold"), default=3.0),
                    ),
                ),
                key="create-board",
                on_submit=self._create,
            ),
        )

    def _settings_nodes(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        nodes: list[sl.LayoutNode[sl.ComponentsV2Target]] = []
        if STARBOARD_BOARD_EDIT in self._capabilities:
            nodes.append(
                sl.form(
                    tr(t"Edit setting"),
                    sl.forms.FormSpec(
                        tr(t"Edit starboard setting"),
                        (
                            sl.forms.TextField(key="name", label=tr(t"Starboard name"), maximum=100),
                            sl.forms.ChoiceField(
                                key="setting",
                                label=tr(t"Setting"),
                                options=tuple(
                                    sl.forms.ChoiceOption(key, key.replace("_", " ").title(), key)
                                    for key in EDITABLE_SETTINGS
                                ),
                            ),
                            sl.forms.TextField(key="value", label=tr(t"Value"), maximum=200),
                        ),
                    ),
                    key="edit-setting",
                    on_submit=self._edit,
                )
            )
        if STARBOARD_BOARD_DELETE in self._capabilities:
            nodes.append(
                sl.form(
                    tr(t"Delete board"),
                    sl.forms.FormSpec(
                        tr(t"Choose starboard to delete"),
                        (sl.forms.TextField(key="name", label=tr(t"Starboard name"), maximum=100),),
                    ),
                    key="delete-board",
                    on_submit=self._request_delete,
                )
            )
        return tuple(nodes)

    def _emoji_nodes(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        return (
            sl.form(
                tr(t"Edit emoji"),
                sl.forms.FormSpec(
                    tr(t"Add or remove starboard emoji"),
                    (
                        sl.forms.TextField(key="name", label=tr(t"Starboard name"), maximum=100),
                        sl.forms.ChoiceField(
                            key="operation",
                            label=tr(t"Operation"),
                            options=(
                                sl.forms.ChoiceOption("add", tr(t"Add or replace"), "add"),
                                sl.forms.ChoiceOption("remove", tr(t"Remove"), "remove"),
                            ),
                        ),
                        sl.forms.TextField(key="emoji", label=tr(t"Emoji"), maximum=100),
                        sl.forms.ChoiceField(
                            key="direction",
                            label=tr(t"Direction"),
                            default="up",
                            options=(
                                sl.forms.ChoiceOption("up", tr(t"Up"), "up"),
                                sl.forms.ChoiceOption("down", tr(t"Down"), "down"),
                            ),
                        ),
                        sl.forms.FloatField(key="multiplier", label=tr(t"Multiplier"), default=1.0),
                    ),
                ),
                key="edit-emoji",
                on_submit=self._edit_emoji,
            ),
        )

    def _weight_nodes(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        return (
            sl.form(
                tr(t"Edit role weight"),
                sl.forms.FormSpec(
                    tr(t"Set or remove role multiplier"),
                    (
                        sl.forms.TextField(key="name", label=tr(t"Starboard name"), maximum=100),
                        sl.forms.IntField(key="role_id", label=tr(t"Role ID"), minimum=1),
                        sl.forms.FloatField(
                            key="multiplier",
                            label=tr(t"Multiplier; leave empty to remove"),
                            required=False,
                        ),
                    ),
                ),
                key="edit-weight",
                on_submit=self._edit_weight,
            ),
        )

    async def _create(self, event: sl.SubmitEvent) -> None:
        if not await self._may(event, STARBOARD_BOARD_CREATE):
            return
        channel_id = cast(int, event.values["channel_id"])
        name = cast(str, event.values["name"])
        required = cast(float, event.values["required"])
        created = await self._create_board(channel_id, name, required)
        await self._refresh()
        created_name = created.name
        await event.notice(tr(t"Created starboard **{created_name}**."))

    async def _edit(self, event: sl.SubmitEvent) -> None:
        if not await self._may(event, STARBOARD_BOARD_EDIT):
            return
        name = cast(str, event.values["name"])
        setting = cast(str, event.values["setting"])
        value = _parse_setting(setting, cast(str, event.values["value"]))
        updated = await self._operations.update_settings(self._guild_id, name, **{setting: value})
        if updated is None:
            await event.notice(tr(t"No starboard with that name exists."))
            return
        await self._refresh()
        await event.notice(tr(t"Starboard updated."))

    async def _edit_emoji(self, event: sl.SubmitEvent) -> None:
        if not await self._may(event, STARBOARD_EMOJI_EDIT):
            return
        config = await self._named(event, cast(str, event.values["name"]))
        if config is None:
            return
        emoji = cast(str, event.values["emoji"])
        operation = cast(str, event.values["operation"])
        aliases = tuple(item for item in config.emojis if item.emoji != emoji)
        if operation == "add":
            direction = cast(str, event.values["direction"])
            multiplier = cast(float, event.values["multiplier"])
            aliases += (StarboardEmoji(emoji, cast(StarboardDirection, direction), multiplier, len(aliases)),)
        await self._operations.set_emojis(config, aliases)
        await self._refresh()
        await event.notice(tr(t"Starboard emojis updated."))

    async def _edit_weight(self, event: sl.SubmitEvent) -> None:
        if not await self._may(event, STARBOARD_WEIGHT_EDIT):
            return
        config = await self._named(event, cast(str, event.values["name"]))
        if config is None:
            return
        role_id = cast(int, event.values["role_id"])
        multiplier = cast(float | None, event.values.get("multiplier"))
        await self._operations.set_role_multiplier(config, role_id, multiplier)
        await event.notice(tr(t"Starboard role weight updated."))

    async def _request_delete(self, event: sl.SubmitEvent) -> None:
        if not await self._may(event, STARBOARD_BOARD_DELETE):
            return
        self._deleting = cast(str, event.values["name"])
        self._decision = sp.Decision[sl.ComponentsV2Target](
            tr(t"Deleting a starboard keeps its audit history but disables its configuration."),
            (
                sp.DecisionOption("confirm", tr(t"Delete"), sl.Tone.DANGER),
                sp.DecisionOption("cancel", tr(t"Cancel")),
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
        self._deleting = None
        self._decision = None
        await self._refresh()
        await event.source.notice(tr(t"Starboard deleted.") if deleted else tr(t"No starboard with that name exists."))

    async def _named(self, event: sl.ActionEvent, name: str) -> StarboardConfig | None:
        config = await self._operations.get(self._guild_id, name)
        if config is None:
            await event.notice(tr(t"No starboard with that name exists."))
        return config

    async def _may(self, event: sl.ActionEvent, node: PermissionNode) -> bool:
        if await self._authorize(node):
            return True
        await event.notice(tr(t"You are no longer allowed to perform this starboard operation."))
        return False

    async def _close(self, event: sl.PressEvent) -> None:
        await event.finish()


def _board_detail(board: StarboardConfig) -> sl.semantic.Fields:
    emojis = " ".join(f"{item.emoji} ({item.direction}, {item.multiplier:g}x)" for item in board.emojis) or "—"
    return sl.fields(
        sl.field(tr(t"Channel"), str(board.channel_id)),
        sl.field(tr(t"Post threshold"), f"{board.required:g}"),
        sl.field(tr(t"Removal threshold"), f"{board.required_remove:g}"),
        sl.field(tr(t"Emojis"), emojis),
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
