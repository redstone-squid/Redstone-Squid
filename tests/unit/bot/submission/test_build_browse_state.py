"""Build browser actions participate in the UI's state transactions."""

from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.submission.build_browse import BuildBrowseScreen, BuildCapabilities
from squid.builds.application import BuildQueryService, BuildService
from squid.builds.domain import Status
from squid.builds.models import Build, DoorBuild
from squid.core.pagination import Page
from squid.permissions.domain import PermissionNode
from squid.schematics.application import SchematicService
from squid_reactivity import LocalTopicBus
from squid_ui.testing import press_event, submit_event
from squid_ui_discord.testing import stage


def _screen(*, initial_id: int | None = 7, status: Status = Status.CONFIRMED) -> BuildBrowseScreen:
    build = DoorBuild(id=7, submission_status=status)
    queries = AsyncMock(spec=BuildQueryService)
    queries.get.return_value = build
    queries.semantic.return_value = build
    queries.list_page.return_value = Page(items=(build,), total=1, next=None, prev=None)
    schematics = Mock(spec=SchematicService, available=False)

    async def render_build(build: Build) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request]:
        return sl.paragraph(f"Build #{build.id}")

    async def authorize(_node: PermissionNode) -> bool:
        return True

    async def refresh_posts(_build_id: int) -> None:
        pass

    return BuildBrowseScreen(
        cast(BuildQueryService, queries),
        cast(BuildService, None),
        cast(SchematicService, schematics),
        topics=LocalTopicBus(),
        initial_id=initial_id,
        render_build=render_build,
        capabilities=BuildCapabilities(
            view_pending=True,
            approve=True,
            reject=True,
            debug=False,
            edit=False,
            measure_timing=False,
        ),
        actor_account_id=1,
        authorize=authorize,
        refresh_posts=refresh_posts,
    )


async def test_back_to_builds_replaces_the_detail_with_the_catalogue() -> None:
    async with stage(_screen(), owner=object()) as ui:
        assert "Build #7" in ui.texts()

        await ui.press("back")

        assert "## Build catalogue" in ui.texts()
        assert "Build #7" not in ui.texts()


async def test_a_failed_back_action_keeps_the_selected_build() -> None:
    screen = _screen()
    await screen.on_load()
    selected = screen._selected

    async def back_then_fail() -> None:
        await screen._back(press_event())
        raise RuntimeError("later failure")

    with pytest.raises(RuntimeError, match="later failure"), sl.runtime.transaction():
        await back_then_fail()

    assert screen._selected is selected


@pytest.mark.parametrize("search", [False, True], ids=["open-id", "search"])
async def test_find_build_actions_can_select_inside_a_transaction(search: bool) -> None:
    screen = _screen(initial_id=None)
    await screen.on_load()

    with sl.runtime.transaction():
        if search:
            await screen._search(submit_event({"query": "door"}))
        else:
            await screen._open_id(submit_event({"id": 7}))

    assert screen._selected is not None


@pytest.mark.parametrize("action", ["approve", "reject"])
async def test_review_confirmation_can_open_and_cancel(action: str) -> None:
    async with stage(_screen(status=Status.PENDING), owner=object()) as ui:
        await ui.press(f"selected-build.{action}")

        assert "## Confirm review decision" in ui.texts()

        await ui.press("selected-build.review-decision.build-review.cancel")

        assert "## Confirm review decision" not in ui.texts()
        ui.control(f"selected-build.{action}")


async def test_a_failed_review_request_does_not_leave_a_confirmation() -> None:
    screen = _screen(status=Status.PENDING)
    await screen.on_load()
    detail = screen._selected
    assert detail is not None

    async def request_then_fail() -> None:
        await detail._request_approve(press_event())
        raise RuntimeError("later failure")

    with pytest.raises(RuntimeError, match="later failure"), sl.runtime.transaction():
        await request_then_fail()

    assert detail._decision is None
    assert detail._pending_review is None
