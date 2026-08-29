"""The version catalogue screen."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import squid_ui as sl
from squid.bot.version_tracking import VersionOperations, VersionScreen, VersionTracker
from squid.versions.domain import MinecraftVersion
from squid_ui.testing import labels


def make_screen(*, allowed: bool = True) -> VersionScreen:
    versions = SimpleNamespace(
        list_display=AsyncMock(
            side_effect=lambda edition, **_kwargs: ["1.21", "1.20"] if edition == "Java" else ["1.21.90"]
        ),
        add=AsyncMock(return_value=MinecraftVersion("Java", 1, 22, 0)),
    )
    return VersionScreen(
        cast(VersionOperations, versions),
        can_create=allowed,
        authorize_create=AsyncMock(return_value=allowed),
    )


async def test_version_catalogue_loads_both_editions_into_a_browser() -> None:
    screen = make_screen()
    await screen.on_load()

    assert screen._browser is not None
    assert [item.edition for item in cast(Any, screen._browser.source).items] == ["Java", "Java", "Bedrock"]
    assert "Add version" in labels(screen.render())


async def test_creation_rechecks_permission_and_refreshes() -> None:
    screen = make_screen()
    await screen.on_load()
    event = SimpleNamespace(values={"edition": "Java", "version": "1.22"}, notice=AsyncMock())

    await screen._add(cast(sl.SubmitEvent, event))

    cast(Any, screen._versions).add.assert_awaited_once_with("1.22", edition="Java")
    assert cast(Any, screen._versions).list_display.await_count == 4
    event.notice.assert_awaited_once()


async def test_revoked_creation_permission_prevents_the_write() -> None:
    screen = make_screen(allowed=False)
    event = SimpleNamespace(values={"edition": "Java", "version": "1.22"}, notice=AsyncMock())

    await screen._add(cast(sl.SubmitEvent, event))

    cast(Any, screen._versions).add.assert_not_awaited()
    event.notice.assert_awaited_once()


def test_versions_are_one_app_only_workspace() -> None:
    tracker = cast(Any, VersionTracker)
    assert tracker.__cog_commands__ == []
    assert [command.qualified_name for command in tracker.__cog_app_commands__] == ["versions"]
