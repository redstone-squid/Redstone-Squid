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
from squid.core.pagination import Page, PageSelector
from squid.permissions.domain import PermissionNode
from squid.schematics.application import SchematicService
from squid_reactivity import LocalTopicBus
from squid_ui.testing import press_event, submit_event
from squid_ui_discord.testing import stage


def _screen(
    *, initial_id: int | None = 7, status: Status = Status.CONFIRMED, schematics_available: bool = False
) -> BuildBrowseScreen:
    build = DoorBuild(id=7, submission_status=status)
    queries = AsyncMock(spec=BuildQueryService)
    queries.get.return_value = build
    queries.semantic.return_value = build
    queries.list_page.return_value = Page(items=(build,), total=1, next=None, prev=None)
    schematics = Mock(spec=SchematicService, available=schematics_available, videos=None)

    async def render_build(build: Build) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request]:
        return sl.paragraph(f"Build #{build.id}")

    async def authorize(_node: PermissionNode) -> bool:
        return True

    async def refresh_posts(_build_id: int) -> None:
        pass

    return BuildBrowseScreen(
        cast(BuildQueryService, queries),
        cast(BuildService, None),
        AsyncMock(),
        AsyncMock(),
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
        await ui.press(ui.key_for(action))

        assert "## Confirm review decision" in ui.texts()

        await ui.press(ui.key_for("build-review.cancel"))

        assert "## Confirm review decision" not in ui.texts()
        ui.control(ui.key_for(action))


async def test_a_failed_review_request_does_not_leave_a_confirmation() -> None:
    screen = _screen(status=Status.PENDING)
    await screen.on_load()
    detail = screen._selected
    assert detail is not None

    async def request_then_fail() -> None:
        await detail._request_approve(1, press_event())
        raise RuntimeError("later failure")

    with pytest.raises(RuntimeError, match="later failure"), sl.runtime.transaction():
        await request_then_fail()

    assert detail._decision is None
    assert detail._pending_review is None


async def test_schematic_controls_survive_opening_and_cancelling_review() -> None:
    screen = _screen(status=Status.PENDING, schematics_available=True)
    async with stage(screen, owner=object()) as ui:
        await ui.press(ui.key_for("schematic-tools"))
        ui.control(ui.key_for("schematic-info"))
        await ui.press(ui.key_for("approve"))
        await ui.press(ui.key_for("build-review.cancel"))
        ui.control(ui.key_for("schematic-info"))


async def test_open_id_through_the_discord_modal() -> None:
    async with stage(_screen(initial_id=None), owner=object()) as ui:
        form = await ui.press_for_form(ui.key_for("open-id"))
        await form.submit({"id": "7"})
        assert "Build #7" in ui.texts()
        ui.control(ui.key_for("older"))
        ui.control(ui.key_for("open-id"))


async def test_schematic_tools_are_collapsed_on_arrival() -> None:
    async with stage(_screen(schematics_available=True), owner=object()) as ui:
        assert "Schematic info" not in ui.texts()
        await ui.press(ui.key_for("schematic-tools"))
        ui.control(ui.key_for("schematic-info"))


@pytest.mark.parametrize("newer", [False, True])
async def test_detail_navigation_uses_id_anchors_and_preserves_visibility(newer: bool) -> None:
    screen = _screen()
    queries = cast(AsyncMock, screen._queries)
    queries.list_page.return_value = Page(
        items=(DoorBuild(id=8 if newer else 6, submission_status=Status.CONFIRMED),),
        total=20,
        next=None,
        prev=None,
    )
    async with stage(screen, owner=object()) as ui:
        await ui.press(ui.key_for("newer" if newer else "older"))
        assert f"Build #{8 if newer else 6}" in ui.texts()
        queries.list_page.assert_awaited_once_with(
            statuses=frozenset({Status.CONFIRMED, Status.PENDING}),
            selector=PageSelector(before_id=7) if newer else PageSelector(after_id=7),
            page_size=1,
        )


async def test_list_selection_and_return_keep_the_list_available() -> None:
    async with stage(_screen(initial_id=None), owner=object()) as ui:
        await ui.select(ui.key_for("builds.open"), ["7"])
        assert "Build #7" in ui.texts()
        await ui.press(ui.key_for("back"))
        assert "## Build catalogue" in ui.texts()
        ui.control(ui.key_for("builds.open"))
