"""Build browser actions participate in the UI's state transactions."""

from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.submission.build_browse import BuildBrowseScreen, BuildCapabilities
from squid.bot.submission.build_card import render_build_card
from squid.bot.submission.records import RecordsScreen
from squid.bot.utils.web import MediaPreviewClient
from squid.builds.application import BuildQueryService, BuildService
from squid.builds.domain import Status
from squid.builds.models import Build, DoorBuild
from squid.core.pagination import Page, PageSelector
from squid.permissions.domain import PermissionNode
from squid.records.application import RecordService
from squid.records.application.models import PublishedTitle, RecordGap
from squid.records.domain import BaseTitle, BuildKind, ResolutionStatus, VersionScope
from squid.schematics.application import SchematicService
from squid.search.application import SearchService
from squid.search.domain import SearchMode, SearchPage, SearchScope
from squid_reactivity import LocalTopicBus
from squid_ui.testing import press_event, submit_event
from squid_ui_discord.testing import stage


def _screen(
    *,
    initial_id: int | None = 7,
    status: Status = Status.CONFIRMED,
    schematics_available: bool = False,
    video_enabled: bool = False,
    records: RecordService | None = None,
    initial_records: bool = False,
    record_tools: RecordsScreen | None = None,
) -> BuildBrowseScreen:
    build = DoorBuild(id=7, submission_status=status)
    queries = AsyncMock(spec=BuildQueryService)
    queries.get.return_value = build
    queries.semantic.return_value = build
    queries.list_page.return_value = Page(items=(build,), total=1, next=None, prev=None)
    search = AsyncMock(spec=SearchService)
    search.search.return_value = SearchPage(hits=(), total=0, next=None, prev=None)
    schematics = Mock(
        spec=SchematicService, available=schematics_available, videos=Mock(enabled=True) if video_enabled else None
    )

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
        ),
        actor_account_id=1,
        authorize=authorize,
        refresh_posts=refresh_posts,
        records=records,
        search=search,
        record_tools=record_tools,
        initial_records=initial_records,
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
            await screen._search(submit_event({"query": "door", "sort": None}))
        else:
            await screen._open_id(submit_event({"id": 7}))

    assert screen._results is not None if search else screen._selected is not None


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


async def test_records_open_holders_and_preserve_the_selected_record() -> None:
    records = AsyncMock(spec=RecordService)
    records.list_page.return_value = Page(
        items=(
            PublishedTitle(
                id=10,
                definition_id=1,
                title_id=uuid4(),
                title="Smallest 2x2 door",
                subtitle=None,
                base_title=BaseTitle.SMALLEST,
                build_kind=BuildKind.DOOR,
                version_scope=VersionScope.ALL_TIME,
                status=ResolutionStatus.RESOLVED,
                holder_build_ids=(7, 9),
                computed_at=datetime.now(UTC),
            ),
        ),
        total=1,
        next=None,
        prev=None,
    )
    async with stage(_screen(initial_id=None, records=records, initial_records=True), owner=object()) as ui:
        ui.assert_within_limits()
        assert "## Find a record" in ui.texts()
        await ui.press(ui.key_for("all-standings"))
        await ui.select(ui.key_for("records.open"), ["10"])
        await ui.press(ui.key_for("holder-7"))
        assert "Build #7" in ui.texts()
        await ui.press(ui.key_for("back"))
        assert "## Smallest 2x2 door" in ui.texts()
        assert not any("resolved" in text or "no_candidate" in text for text in ui.texts())
        ui.control(ui.key_for("holder-9"))
        await ui.press(ui.key_for("all-builds"))
        assert "## Build catalogue" in ui.texts()
        ui.assert_within_limits()


async def test_missing_id_keeps_the_current_build() -> None:
    screen = _screen()
    async with stage(screen, owner=object()) as ui:
        cast(AsyncMock, screen._queries).get.return_value = None
        form = await ui.press_for_form(ui.key_for("open-id"))
        await form.submit({"id": "999"})
        assert "Build #7" in ui.texts()


async def test_record_tools_load_on_request_and_return_to_builds() -> None:
    records = AsyncMock()
    records.gaps.return_value = ()
    records.title_gaps.return_value = ()
    tools = RecordsScreen(records, AsyncMock(), can_inspect=True, can_rebuild=False, authorize=AsyncMock())
    async with stage(_screen(initial_id=None, record_tools=tools), owner=object()) as ui:
        records.gaps.assert_not_awaited()
        await ui.press(ui.key_for("record-tools"))
        records.gaps.assert_awaited_once()
        assert "## Record evidence gaps" in ui.texts()
        assert "No unresolved active record categories." in ui.texts()
        ui.assert_within_limits()
        await ui.press(ui.key_for("all-builds"))
        assert "## Build catalogue" in ui.texts()


async def test_full_build_card_fits_with_catalogue_navigation() -> None:
    screen = _screen(status=Status.PENDING)
    screen._render_build = lambda build: render_build_card(build, MediaPreviewClient())
    async with stage(screen, owner=object()) as ui:
        ui.control(ui.key_for("older"))
        ui.control(ui.key_for("schematic-tools"))
        assert any("Submission ID: 7" in text for text in ui.texts())
        ui.assert_within_limits()


async def test_record_tools_show_populated_diagnostics_after_a_click() -> None:
    records = AsyncMock()
    records.gaps.return_value = (RecordGap(1, "Smallest 2x2 door", None, BaseTitle.SMALLEST, (7,), ("width",)),)
    records.title_gaps.return_value = ()
    tools = RecordsScreen(records, AsyncMock(), can_inspect=True, can_rebuild=False, authorize=AsyncMock())
    async with stage(_screen(initial_id=None, record_tools=tools), owner=object()) as ui:
        await ui.press(ui.key_for("record-tools"))
        assert "## Record evidence gaps" in ui.texts()
        await ui.select(ui.key_for("evidence-gaps.open"), ["1"])
        assert any("width" in text for text in ui.texts())
        ui.assert_within_limits()


@pytest.mark.parametrize("records", [False, True])
async def test_keyword_search_and_filters_from_the_catalogue_modal(records: bool) -> None:
    screen = _screen(initial_id=None)
    if records:
        screen._mode = "records"
    async with stage(screen, owner=object()) as ui:
        form = await ui.press_for_form(ui.key_for("catalogue-search"))
        await form.submit({"query": "door kind:door", "sort": "-created_at"})
        request = cast(AsyncMock, screen._search_service).search.call_args.args[0]
        assert request.mode is SearchMode.LEXICAL
        assert request.scope is (SearchScope.RECORDS if records else SearchScope.BUILDS)
        assert request.query == ("status:resolved AND (door kind:door)" if records else "door kind:door")
        assert "## Search results" in ui.texts()
        assert not any("Search by meaning" in text for text in ui.texts())
        ui.assert_within_limits()


async def test_record_category_shortcut_requests_only_populated_standings() -> None:
    screen = _screen(initial_id=None)
    screen._mode = "records"
    async with stage(screen, owner=object()) as ui:
        await ui.press(ui.key_for("smallest"))
        request = cast(AsyncMock, screen._search_service).search.call_args.args[0]
        assert request.query == "status:resolved AND (base_title:smallest)"
        assert request.scope is SearchScope.RECORDS
        assert "## Search results" in ui.texts()


async def test_record_toggles_preserve_text_and_sort_and_can_be_combined_or_removed() -> None:
    screen = _screen(initial_id=None)
    screen._mode = "records"
    service = cast(AsyncMock, screen._search_service)
    async with stage(screen, owner=object()) as ui:
        form = await ui.press_for_form(ui.key_for("catalogue-search"))
        await form.submit({"query": 'title:"vault door"', "sort": "-created_at"})
        await ui.press(ui.key_for("smallest"))
        assert ui.control(ui.key_for("smallest")).payload["style"] == 3
        await ui.press(ui.key_for("fastest"))
        request = service.search.call_args.args[0]
        assert request.query == (
            'status:resolved AND (title:"vault door") AND (base_title:fastest OR base_title:smallest)'
        )
        assert request.sort is not None
        assert request.sort.field == "created_at"
        assert screen._query == 'title:"vault door"'
        assert screen._sort == "-created_at"
        form = await ui.press_for_form(ui.key_for("catalogue-search"))
        await form.submit({"query": 'title:"vault door"', "sort": "-created_at"})
        assert service.search.call_args.args[0].query == request.query
        await ui.press(ui.key_for("smallest"))
        assert ui.control(ui.key_for("smallest")).payload["style"] == 2
        assert service.search.call_args.args[0].query.endswith("AND (base_title:fastest)")
        await ui.press(ui.key_for("fastest"))
        assert service.search.call_args.args[0].query == 'status:resolved AND (title:"vault door")'
        ui.assert_within_limits()


async def test_all_standings_clears_active_search_and_toggles() -> None:
    records = AsyncMock(spec=RecordService)
    records.list_page.return_value = Page(items=(), total=0, next=None, prev=None)
    screen = _screen(initial_id=None, records=records, initial_records=True)
    async with stage(screen, owner=object()) as ui:
        await ui.press(ui.key_for("smallest"))
        await ui.press(ui.key_for("all-standings"))
        assert screen._record_categories == frozenset()
        assert screen._results is None
        assert screen._query == screen._sort == ""
        assert ui.control(ui.key_for("smallest")).payload["style"] == 2
        assert "## Records" in ui.texts()
        ui.assert_within_limits()


@pytest.mark.parametrize("records", [False, True])
async def test_search_help_stays_with_results_without_refetching(records: bool) -> None:
    screen = _screen(initial_id=None)
    screen._mode = "records" if records else "builds"
    service = cast(AsyncMock, screen._search_service)
    async with stage(screen, owner=object()) as ui:
        await ui.press(ui.key_for("search-help"))
        assert "## Search help" in ui.texts()
        assert any((r"all\_time" if records else "creator:Steve") in text for text in ui.texts())
        service.search.assert_not_awaited()
        form = await ui.press_for_form(ui.key_for("catalogue-search"))
        await form.submit({"query": "door", "sort": ""})
        assert "## Search help" in ui.texts()
        assert "## Search results" in ui.texts()
        await ui.press(ui.key_for("search-help"))
        assert "## Search help" not in ui.texts()
        service.search.assert_awaited_once()
        ui.assert_within_limits()


async def test_record_toggles_are_visible_on_build_details() -> None:
    screen = _screen()
    screen._mode = "records"
    async with stage(screen, owner=object()) as ui:
        assert "Build #7" in ui.texts()
        for category in ("smallest", "fastest", "first"):
            ui.control(ui.key_for(category))
        await ui.press(ui.key_for("first"))
        assert "## Search results" in ui.texts()
        assert screen._selected is None
        assert screen._record_categories == frozenset({"first"})


async def test_failed_toggle_keeps_the_previous_selection_and_results() -> None:
    screen = _screen(initial_id=None)
    screen._mode = "records"
    async with stage(screen, owner=object()) as ui:
        await ui.press(ui.key_for("smallest"))
        original_results = screen._results
        cast(AsyncMock, screen._search_service).search.side_effect = RuntimeError("search unavailable")
        await ui.press(ui.key_for("fastest"))
        assert screen._record_categories == frozenset({"smallest"})
        assert screen._results is original_results
        assert ui.control(ui.key_for("smallest")).payload["style"] == 3
        assert ui.control(ui.key_for("fastest")).payload["style"] == 2


async def test_catalogue_shows_exactly_one_active_workspace() -> None:
    records = AsyncMock(spec=RecordService)
    records.gaps.return_value = ()
    records.title_gaps.return_value = ()
    tools = RecordsScreen(records, AsyncMock(), can_inspect=True, can_rebuild=False, authorize=AsyncMock())
    screen = _screen(initial_id=None, records=records, record_tools=tools)
    keys = ("all-builds", "all-records", "record-tools")
    async with stage(screen, owner=object()) as ui:
        for selected in keys:
            if selected != "all-builds":
                await ui.press(ui.key_for(selected))
            for key in keys:
                control = ui.control(ui.key_for(key)).payload
                assert control["style"] == (1 if key == selected else 2)
                assert control.get("disabled", False) == (key == selected)
            ui.assert_within_limits()


@pytest.mark.parametrize("records", [False, True])
async def test_search_reference_runs_examples_and_exposes_more_than_modal_hints(records: bool) -> None:
    screen = _screen(initial_id=None)
    screen._mode = "records" if records else "builds"
    async with stage(screen, owner=object()) as ui:
        await ui.press(ui.key_for("search-help"))
        assert any((r"fastest\_smallest" if records else r"completion\_at") in text for text in ui.texts())
        await ui.press(ui.key_for("search-reference.syntax"))
        assert any("NOT excludes" in text for text in ui.texts())
        ui.assert_within_limits()
        await ui.press(ui.key_for("search-reference.examples"))
        ui.assert_within_limits()
        await ui.press(ui.key_for("example-0"))
        assert "## Search results" in ui.texts()
        assert "## Search help" not in ui.texts()
        assert screen._record_categories == frozenset()
        assert screen._query == (
            "kind:door base_title:fastest version_scope:all_time" if records else "door width<=5 height<=6"
        )


async def test_video_form_is_inside_schematic_tools() -> None:
    async with stage(_screen(schematics_available=True, video_enabled=True), owner=object()) as ui:
        assert "Schematic info" not in ui.texts()
        assert "Render video" not in ui.texts()
        await ui.press(ui.key_for("schematic-tools"))
        ui.control(ui.key_for("schematic-info"))
        ui.control(ui.key_for("schematic-video"))
        await ui.press_for_form(ui.key_for("schematic-video"))
        await ui.press(ui.key_for("schematic-tools"))
        assert "Schematic info" not in ui.texts()
        assert "Render video" not in ui.texts()
