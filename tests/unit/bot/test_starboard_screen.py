"""The consolidated starboard configuration workspace."""

from typing import Any, cast, override

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.starboard import StarboardCog
from squid.bot.starboard.screen import StarboardScreen
from squid.permissions.domain import PermissionNode
from squid.permissions.domain.catalogue import (
    STARBOARD_BOARD_CREATE,
    STARBOARD_BOARD_DELETE,
    STARBOARD_BOARD_EDIT,
    STARBOARD_EMOJI_EDIT,
    STARBOARD_WEIGHT_EDIT,
)
from squid.starboard.application import StarboardService
from squid.starboard.domain import StarboardConfig, StarboardEmoji
from squid_ui.testing import labels


def board(name: str = "main") -> StarboardConfig:
    return StarboardConfig(1, 7, 11, name, (StarboardEmoji("⭐", "up"),))


class FakeStarboardService(StarboardService):
    def __init__(self) -> None:
        self.config = board()
        self.list_reads = 0
        self.deletions: list[tuple[int, str]] = []

    @override
    async def list_for_guild(self, guild_id: int) -> tuple[StarboardConfig, ...]:
        del guild_id
        self.list_reads += 1
        return (self.config,)

    @override
    async def get(self, guild_id: int, name: str) -> StarboardConfig | None:
        del guild_id
        return self.config if name == self.config.name else None

    @override
    async def delete_starboard(self, guild_id: int, name: str) -> bool:
        self.deletions.append((guild_id, name))
        return True

    @override
    async def update_settings(self, guild_id: int, name: str, **settings: object) -> StarboardConfig | None:
        del guild_id, name, settings
        return self.config

    @override
    async def set_emojis(self, config: StarboardConfig, emojis: tuple[StarboardEmoji, ...]) -> None:
        del config, emojis

    @override
    async def set_role_multiplier(self, config: StarboardConfig, role_id: int, multiplier: float | None) -> None:
        del config, role_id, multiplier


class BoardCreator:
    def __init__(self, config: StarboardConfig) -> None:
        self.config = config
        self.calls: list[tuple[int, str, float]] = []

    async def __call__(self, channel_id: int, name: str, required: float) -> StarboardConfig:
        self.calls.append((channel_id, name, required))
        return self.config


class NoticeEvent:
    def __init__(self, **values: object) -> None:
        self.values = values
        self.notices: list[object] = []

    async def notice(self, text: object, **_kwargs: object) -> None:
        self.notices.append(text)


class TransitionEvent:
    def __init__(self, source: NoticeEvent) -> None:
        self.source = source


def make_screen(
    *,
    capabilities: frozenset[PermissionNode] = frozenset(),
    allowed: bool = True,
) -> tuple[StarboardScreen, FakeStarboardService, BoardCreator]:
    operations = FakeStarboardService()
    creator = BoardCreator(operations.config)

    async def authorize(_node: PermissionNode) -> bool:
        return allowed

    return (
        StarboardScreen(
            operations,
            guild_id=7,
            capabilities=capabilities,
            authorize=authorize,
            create_board=creator,
        ),
        operations,
        creator,
    )


async def test_starboard_screen_uses_user_guild_tabs_and_browser() -> None:
    screen, _, _ = make_screen(
        capabilities=frozenset(
            {
                STARBOARD_BOARD_CREATE,
                STARBOARD_BOARD_EDIT,
                STARBOARD_BOARD_DELETE,
                STARBOARD_EMOJI_EDIT,
                STARBOARD_WEIGHT_EDIT,
            }
        )
    )
    await screen.on_load()

    assert screen.session is not None
    assert screen.session.scope is sd.ScopeKind.USER_GUILD
    assert screen._browser is not None
    assert screen._tabs is not None
    assert {"Boards", "Create", "Settings", "Emojis", "Role weights"} <= set(labels(screen._tabs.render()))


async def test_create_uses_injected_discord_operation_and_refreshes() -> None:
    screen, operations, creator = make_screen(capabilities=frozenset({STARBOARD_BOARD_CREATE}))
    await screen.on_load()
    event = NoticeEvent(channel_id=11, name="main", required=4.0)

    await screen._create(cast(sl.SubmitEvent, event))

    assert creator.calls == [(11, "main", 4.0)]
    assert operations.list_reads == 2
    assert len(event.notices) == 1


async def test_revoked_delete_permission_prevents_the_decision() -> None:
    screen, _, _ = make_screen(capabilities=frozenset({STARBOARD_BOARD_DELETE}), allowed=False)
    event = NoticeEvent(name="main")

    await screen._request_delete(cast(sl.SubmitEvent, event))

    assert screen._decision is None
    assert len(event.notices) == 1


async def test_delete_requires_confirmation_and_refreshes() -> None:
    screen, operations, _ = make_screen(capabilities=frozenset({STARBOARD_BOARD_DELETE}))
    await screen.on_load()
    submit = NoticeEvent(name="main")
    await screen._request_delete(cast(sl.SubmitEvent, submit))
    source = NoticeEvent()

    await screen._delete(cast(Any, TransitionEvent(source)), "confirm")

    assert operations.deletions == [(7, "main")]
    assert operations.list_reads == 2
    assert len(source.notices) == 1


def test_starboard_is_one_app_only_workspace() -> None:
    cog = cast(Any, StarboardCog)
    assert all(command.name != "starboard" for command in cog.__cog_commands__)
    assert "starboard" in {command.name for command in cog.__cog_app_commands__}
