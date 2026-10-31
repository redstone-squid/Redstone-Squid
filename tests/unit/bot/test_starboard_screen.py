"""The consolidated starboard configuration workspace."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.starboard import StarboardCog
from squid.bot.starboard.screen import StarboardOperations, StarboardScreen
from squid.permissions.domain import PermissionNode
from squid.permissions.domain.catalogue import (
    STARBOARD_BOARD_CREATE,
    STARBOARD_BOARD_DELETE,
    STARBOARD_BOARD_EDIT,
    STARBOARD_EMOJI_EDIT,
    STARBOARD_WEIGHT_EDIT,
)
from squid.starboard.domain import StarboardConfig, StarboardEmoji
from squid_ui.testing import labels


def board(name: str = "main") -> StarboardConfig:
    return StarboardConfig(1, 7, 11, name, (StarboardEmoji("⭐", "up"),))


def make_screen(
    *,
    capabilities: frozenset[PermissionNode] = frozenset(),
    allowed: bool = True,
) -> tuple[StarboardScreen, Any, AsyncMock]:
    config = board()
    operations = SimpleNamespace(
        list_for_guild=AsyncMock(return_value=(config,)),
        get=AsyncMock(return_value=config),
        delete_starboard=AsyncMock(return_value=True),
        update_settings=AsyncMock(return_value=config),
        set_emojis=AsyncMock(),
        set_role_multiplier=AsyncMock(),
    )
    creator = AsyncMock(return_value=config)

    async def authorize(_node: PermissionNode) -> bool:
        return allowed

    return (
        StarboardScreen(
            cast(StarboardOperations, operations),
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

    assert screen.scope is sd.ScopeKind.USER_GUILD
    assert screen._browser is not None
    assert screen._tabs is not None
    assert {"Boards", "Create", "Settings", "Emojis", "Role weights"} <= set(labels(screen._tabs.render()))


async def test_create_uses_injected_discord_operation_and_refreshes() -> None:
    screen, operations, creator = make_screen(capabilities=frozenset({STARBOARD_BOARD_CREATE}))
    await screen.on_load()
    event = SimpleNamespace(
        values={"channel_id": 11, "name": "main", "required": 4.0},
        notice=AsyncMock(),
    )

    await screen._create(cast(sl.SubmitEvent, event))

    creator.assert_awaited_once_with(11, "main", 4.0)
    assert operations.list_for_guild.await_count == 2
    event.notice.assert_awaited_once()


async def test_revoked_delete_permission_prevents_the_decision() -> None:
    screen, _, _ = make_screen(capabilities=frozenset({STARBOARD_BOARD_DELETE}), allowed=False)
    event = SimpleNamespace(values={"name": "main"}, notice=AsyncMock())

    await screen._request_delete(cast(sl.SubmitEvent, event))

    assert screen._decision is None
    event.notice.assert_awaited_once()


async def test_delete_requires_confirmation_and_refreshes() -> None:
    screen, operations, _ = make_screen(capabilities=frozenset({STARBOARD_BOARD_DELETE}))
    await screen.on_load()
    submit = SimpleNamespace(values={"name": "main"}, notice=AsyncMock())
    await screen._request_delete(cast(sl.SubmitEvent, submit))
    source = SimpleNamespace(notice=AsyncMock())

    await screen._delete(cast(Any, SimpleNamespace(source=source)), "confirm")

    operations.delete_starboard.assert_awaited_once_with(7, "main")
    assert operations.list_for_guild.await_count == 2
    source.notice.assert_awaited_once()


def test_starboard_is_one_app_only_workspace() -> None:
    cog = cast(Any, StarboardCog)
    assert all(command.name != "starboard" for command in cog.__cog_commands__)
    assert "starboard" in {command.name for command in cog.__cog_app_commands__}
