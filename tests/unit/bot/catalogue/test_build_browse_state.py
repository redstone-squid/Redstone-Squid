"""Build browser actions participate in the UI's state transactions."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import anyio
import pytest

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.bot.catalogue.browse import BuildBrowseScreen, BuildCapabilities, _BuildDetail
from squid.bot.catalogue.records import RecordsScreen
from squid.bot.submission.build_card import render_build_card
from squid.bot.utils.web import MediaPreviewClient
from squid.builds.application import BuildQueryService, BuildService
from squid.builds.domain import Status
from squid.builds.models import Build, DoorBuild
from squid.core.pagination import Page, PageAnchor, PageSelector
from squid.messages.domain import MessageRef
from squid.permissions.domain import PermissionNode
from squid.records.application import RecordService
from squid.records.application.models import PublishedTitle, RecordGap, TitleDiagnosticGap
from squid.records.domain import BaseTitle, BuildKind, ResolutionStatus, VersionScope
from squid.schematics.application import SchematicService
from squid.search.application import SearchService
from squid.search.application.fields import DEFAULT_FIELD_REGISTRY
from squid.search.application.parser import SearchQueryParser
from squid.search.domain import BuildSearchHit, RecordSearchHit, SearchMode, SearchPage, SearchScope
from squid.suggestions.application import Candidate, SuggestionRegistry, SuggestionService
from squid.suggestions.application import SuggestionSource as RegisteredSource
from squid_reactivity import LocalTopicBus
from squid_ui.ext.discord.target_types import ComponentsV2Target
from squid_ui.ext.discord.testing import StagedUI, stage
from squid_ui.testing import press_event, submit_event
from tests.unit.suggestions.test_services import RecordingProvider


def _screen(
    *,
    initial_id: int | None = 7,
    status: Status = Status.CONFIRMED,
    schematics_available: bool = False,
    schematic_attached: bool = True,
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
    search.fields.return_value = DEFAULT_FIELD_REGISTRY
    search.search.return_value = SearchPage(hits=(), total=0, next=None, prev=None)
    schematics = Mock(
        spec=SchematicService, available=schematics_available, videos=Mock(enabled=True) if video_enabled else None
    )

    schematics.featured_for_build.return_value = Mock() if schematic_attached else None

    async def render_build(build: Build) -> sl.LayoutNode[ComponentsV2Target, sd.Request]:
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


async def submit_search(ui: StagedUI[BuildBrowseScreen], query: str, sort: str = "") -> None:
    if ui.component._guided is not None:
        await ui.press(ui.key_for("guided-search"))
    ui.component._sort = sort
    await ui.press(ui.key_for("guided-search"))
    assert ui.component._guided is not None
    form = await ui.press_for_form(ui.key_for("advanced"))
    await form.submit({"query": query})
    assert ui.component._guided.query == SearchQueryParser().parse(query).normalized


async def toggle_help(ui: StagedUI[BuildBrowseScreen]) -> None:
    if ui.component._guided is None:
        await ui.press(ui.key_for("guided-search"))
    await ui.press(ui.key_for("search-help"))


async def test_back_to_builds_replaces_the_detail_with_the_catalogue() -> None:
    async with stage(_screen(), owner=object()) as ui:
        assert "Build #7" in ui.texts()
        # One Back: the opened build has no separate list of its own to return to.
        assert [key for key in ui.root.snapshot().handler_keys if key.endswith("back")] == [
            ui.key_for("linked-build.back")
        ]

        await ui.press(ui.key_for("linked-build.back"))

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
            await screen._run_search("door", "")
        else:
            await screen._open_id(submit_event({"id": 7}))

    assert screen._results is not None if search else screen._selected is not None


@pytest.mark.parametrize("records", [False, True], ids=["build", "record"])
async def test_search_opens_full_build_and_preserves_results(records: bool) -> None:
    screen = _screen(initial_id=None, schematics_available=True)
    hit = (
        RecordSearchHit("10", "Fastest door", "Java", 7, "Door", "fastest", "all_time")
        if records
        else BuildSearchHit("7", "Door", "confirmed", "A door")
    )
    service = cast(AsyncMock, screen._search_service)
    service.search.return_value = SearchPage(hits=(hit,), total=1, next=None, prev=None)
    async with stage(screen, owner=object()) as ui:
        await submit_search(ui, "door", "")
        results = screen._results
        # Choosing a result opens the full build in one step.
        await ui.select(ui.key_for("search.open"), [f"{hit.resource_kind}:{hit.source_id}"])

        assert "Build #7" in ui.texts()
        ui.control(ui.key_for("schematic-tools"))
        assert not any(key.endswith("open-build") for key in ui.root.snapshot().handler_keys)
        ui.assert_within_limits()

        await ui.press(ui.key_for("search.back"))
        assert screen._results is results
        ui.control(ui.key_for("search.open"))
        assert service.search.await_count == 1


@pytest.mark.parametrize("action", ["approve", "reject"])
async def test_review_confirmation_can_open_and_cancel(action: str) -> None:
    async with stage(_screen(status=Status.PENDING), owner=object()) as ui:
        await ui.press(ui.key_for(action))

        heading = f"## {action.title()} build #7?"
        assert heading in ui.texts()

        await ui.press(ui.key_for("build-review.cancel"))

        assert heading not in ui.texts()
        ui.control(ui.key_for(action))


async def test_a_failed_review_request_does_not_leave_a_confirmation() -> None:
    screen = _screen(status=Status.PENDING)
    await screen.on_load()
    assert screen._selected is not None
    detail = cast(_BuildDetail, screen._selected._detail.value)

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
        ui.control(ui.key_for("linked-build.item-next"))
        ui.control(ui.key_for("open-id"))


@pytest.mark.parametrize("schematics_available", [False, True])
async def test_schematic_tools_are_hidden_without_an_attachment(schematics_available: bool) -> None:
    screen = _screen(schematics_available=schematics_available, schematic_attached=False)
    async with stage(screen, owner=object()) as ui:
        assert "Build #7" in ui.texts()
        assert not any(key.endswith("schematic-tools") for key in ui.root.snapshot().handler_keys)


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
        await ui.press(ui.key_for("linked-build.item-previous" if newer else "linked-build.item-next"))
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
        await ui.press(ui.key_for("builds.back"))
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
        assert "## Records" in ui.texts()
        await ui.press(ui.key_for("all-standings"))
        await ui.select(ui.key_for("records.open"), ["10"])
        await ui.press(ui.key_for("holder-7"))
        assert "Build #7" in ui.texts()
        await ui.press(ui.key_for("linked-build.back"))
        assert "## Smallest 2x2 door" in ui.texts()
        assert not any("unresolved" in text or "no_candidate" in text for text in ui.texts())
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
        assert "## Records missing evidence" in ui.texts()
        assert "Every active record category has the evidence it needs." in ui.texts()
        ui.assert_within_limits()
        await ui.press(ui.key_for("all-builds"))
        assert "## Build catalogue" in ui.texts()


@pytest.mark.parametrize("count", [2, 6])
@pytest.mark.parametrize("browser", ["records", "gaps", "titles"])
async def test_long_record_titles_fit_picker_controls_and_timeout(browser: str, count: int) -> None:
    title = "Smallest seamless flush redstone door " * 4
    records = AsyncMock(spec=RecordService)
    records.list_page.return_value = Page(
        items=tuple(
            PublishedTitle(
                id=index,
                definition_id=index,
                title_id=uuid4(),
                title=title,
                subtitle=None,
                base_title=BaseTitle.SMALLEST,
                build_kind=BuildKind.DOOR,
                version_scope=VersionScope.ALL_TIME,
                status=ResolutionStatus.RESOLVED,
                holder_build_ids=(7,),
                computed_at=datetime.now(UTC),
            )
            for index in range(count)
        ),
        total=count,
        next=None,
        prev=None,
    )
    records.gaps.return_value = tuple(
        RecordGap(index, title, None, BaseTitle.SMALLEST, (7,), ("width",), BuildKind.DOOR) for index in range(count)
    )
    records.title_gaps.return_value = tuple(
        TitleDiagnosticGap(index, title, ({"code": "unknown"},), BuildKind.DOOR) for index in range(count)
    )
    tools = RecordsScreen(records, AsyncMock(), can_inspect=True, can_rebuild=False, authorize=AsyncMock())
    screen = _screen(initial_id=None, records=records, initial_records=True, record_tools=tools)
    async with stage(screen, owner=object()) as ui:
        if browser == "records":
            await ui.press(ui.key_for("all-standings"))
        else:
            await ui.press(ui.key_for("record-tools"))
            if browser == "titles":
                await ui.press(ui.key_for("records-tabs.titles"))
        assert any(title in text for text in ui.texts())
        ui.assert_within_limits()
        await ui.root.handle_timeout()
        ui.assert_within_limits()


async def test_full_build_card_fits_with_catalogue_navigation() -> None:
    screen = _screen(status=Status.PENDING, schematics_available=True)
    screen._render_build = lambda build: render_build_card(build, MediaPreviewClient())
    async with stage(screen, owner=object()) as ui:
        ui.control(ui.key_for("linked-build.item-next"))
        ui.control(ui.key_for("schematic-tools"))
        assert any("Submission ID: 7" in text for text in ui.texts())
        ui.assert_within_limits()
        await toggle_help(ui)
        for tab in ("fields", "syntax", "examples"):
            await ui.press(ui.key_for(f"search-reference.{tab}"))
            assert any("Submission ID: 7" in text for text in ui.texts())
            ui.assert_within_limits()
        await ui.press(ui.key_for("close-help"))
        assert any("Submission ID: 7" in text for text in ui.texts())


async def test_record_tools_show_populated_diagnostics_after_a_click() -> None:
    records = AsyncMock()
    records.gaps.return_value = (
        RecordGap(1, "Smallest 2x2 door", None, BaseTitle.SMALLEST, (7,), ("width",), BuildKind.DOOR),
    )
    records.title_gaps.return_value = ()
    tools = RecordsScreen(records, AsyncMock(), can_inspect=True, can_rebuild=False, authorize=AsyncMock())
    async with stage(_screen(initial_id=None, record_tools=tools), owner=object()) as ui:
        await ui.press(ui.key_for("record-tools"))
        assert "## Records missing evidence" in ui.texts()
        await ui.select(ui.key_for("evidence-gaps.open"), ["1"])
        assert any("Needs width" in text for text in ui.texts())
        ui.assert_within_limits()
        # A gap's build opens in place of the tools, and Back returns to the same gap.
        await ui.press(ui.key_for("gap-build-7"))
        assert "Build #7" in ui.texts()
        await ui.press(ui.key_for("linked-build.back"))
        assert any("Needs width" in text for text in ui.texts())


@pytest.mark.parametrize("records", [False, True])
async def test_query_expression_is_submitted_from_the_guided_search(records: bool) -> None:
    screen = _screen(initial_id=None)
    if records:
        screen._mode = "records"
    async with stage(screen, owner=object()) as ui:
        await submit_search(ui, "door kind:door", "-created_at")
        request = cast(AsyncMock, screen._search_service).search.call_args.args[0]
        assert request.mode is SearchMode.LEXICAL
        assert request.scope is (SearchScope.RECORDS if records else SearchScope.BUILDS)
        assert request.query == "door AND kind:door"
        assert "## Search results" in ui.texts()
        assert not any("Search by meaning" in text for text in ui.texts())
        ui.assert_within_limits()


async def test_record_category_shortcut_includes_all_statuses() -> None:
    records = AsyncMock(spec=RecordService)
    records.list_page.return_value = Page(items=(), total=0, next=None, prev=None)
    screen = _screen(initial_id=None, records=records, initial_records=True)
    async with stage(screen, owner=object()) as ui:
        await ui.press(ui.key_for("smallest"))
        request = cast(AsyncMock, screen._search_service).search.call_args.args[0]
        assert request.query == "base_title:smallest"
        assert request.scope is SearchScope.RECORDS
        assert "## Search results" in ui.texts()


async def test_record_toggles_preserve_text_and_sort_and_can_be_combined_or_removed() -> None:
    screen = _screen(initial_id=None)
    screen._mode = "records"
    service = cast(AsyncMock, screen._search_service)
    async with stage(screen, owner=object()) as ui:
        await submit_search(ui, 'title:"vault door"', "-created_at")
        await ui.press(ui.key_for("smallest"))
        assert ui.control(ui.key_for("smallest")).payload["style"] == 3
        await ui.press(ui.key_for("fastest"))
        request = service.search.call_args.args[0]
        assert request.query == 'title:"vault door" AND (base_title:fastest OR base_title:smallest)'
        assert request.sort is not None
        assert request.sort.field == "created_at"
        assert screen._guided is not None
        assert screen._query == screen._guided.query == request.query
        assert screen._sort == "-created_at"
        await submit_search(ui, request.query, "-created_at")
        assert service.search.call_args.args[0].query == request.query
        await ui.press(ui.key_for("smallest"))
        assert ui.control(ui.key_for("smallest")).payload["style"] == 2
        assert service.search.call_args.args[0].query == 'title:"vault door" AND base_title:fastest'
        await ui.press(ui.key_for("fastest"))
        assert service.search.call_args.args[0].query == 'title:"vault door"'
        ui.assert_within_limits()


@pytest.mark.parametrize("query", ["smallest door", 'door AND BASE_TITLE:"smallest"'])
async def test_record_buttons_edit_typed_title_filters(query: str) -> None:
    screen = _screen(initial_id=None)
    screen._mode = "records"
    async with stage(screen, owner=object()) as ui:
        await submit_search(ui, query, "-created_at")
        assert ui.control(ui.key_for("smallest")).payload["style"] == 3
        assert ui.control(ui.key_for("all-standings")).payload["style"] == 2
        await ui.press(ui.key_for("fastest"))
        assert screen._guided is not None
        assert "(base_title:fastest OR base_title:smallest)" in screen._guided.query
        await ui.press(ui.key_for("smallest"))
        expression = SearchQueryParser().parse(screen._query).expression
        assert expression in (
            SearchQueryParser().parse("door AND base_title:fastest").expression,
            SearchQueryParser().parse("base_title:fastest AND door").expression,
        )
        await ui.press(ui.key_for("remove-0" if screen._query.startswith("base_title") else "remove-1"))
        assert ui.control(ui.key_for("all-standings")).payload["style"] == 3
        assert screen._guided.query == "door"
        assert screen._sort == "-created_at"


@pytest.mark.parametrize(
    "query",
    [
        "NOT base_title:smallest",
        "base_title:smallest OR kind:door",
        "base_title:smallest AND base_title:fastest",
        "base_title:fastest_smallest",
    ],
)
async def test_record_buttons_leave_complex_title_filters_to_the_editor(query: str) -> None:
    screen = _screen(initial_id=None)
    screen._mode = "records"
    async with stage(screen, owner=object()) as ui:
        await submit_search(ui, query)
        for key in ("smallest", "fastest", "first", "all-standings"):
            assert ui.control(ui.key_for(key)).payload["disabled"]


async def test_all_types_preserves_literal_keywords() -> None:
    screen = _screen(initial_id=None)
    screen._mode = "records"
    async with stage(screen, owner=object()) as ui:
        await submit_search(ui, "fastest AND base_title:smallest")
        await ui.press(ui.key_for("all-standings"))
        assert screen._query == "(fastest)"
        assert ui.control(ui.key_for("all-standings")).payload["style"] == 3


async def test_all_types_clears_title_filters_and_preserves_other_filters() -> None:
    records = AsyncMock(spec=RecordService)
    records.list_page.return_value = Page(items=(), total=0, next=None, prev=None)
    screen = _screen(initial_id=None, records=records, initial_records=True)
    service = cast(AsyncMock, screen._search_service)
    async with stage(screen, owner=object()) as ui:
        assert ui.control(ui.key_for("smallest")).payload["label"] == "Smallest"
        await ui.press(ui.key_for("smallest"))
        await ui.press(ui.key_for("all-standings"))
        assert screen._query == ""
        assert screen._results is None
        assert ui.control(ui.key_for("smallest")).payload["style"] == 2
        assert "## Records" in ui.texts()
        # With a typed query, the query and sort stay and the search reruns without types.
        await submit_search(ui, "door base_title:smallest", "-created_at")
        await ui.press(ui.key_for("all-standings"))
        assert screen._query == "door"
        assert screen._sort == "-created_at"
        assert service.search.call_args.args[0].query == "door"
        ui.assert_within_limits()


@pytest.mark.parametrize("records", [False, True])
async def test_search_help_precedes_lists_and_results_without_refetching(records: bool) -> None:
    record_service = AsyncMock(spec=RecordService)
    record_service.list_page.return_value = Page(items=(), total=0, next=None, prev=None)
    screen = _screen(initial_id=None, records=record_service, initial_records=records)
    service = cast(AsyncMock, screen._search_service)
    async with stage(screen, owner=object()) as ui:
        heading = "## Records" if records else "## Build catalogue"
        await toggle_help(ui)
        assert ui.texts().index("## Search help") < ui.texts().index(heading)
        assert any((r"all\_time" if records else "creator:Steve") in text for text in ui.texts())
        service.search.assert_not_awaited()
        # Searching shows the results in place of help.
        await submit_search(ui, "door", "")
        assert "## Search help" not in ui.texts()
        assert "## Search results" in ui.texts()
        results = screen._results
        await toggle_help(ui)
        assert ui.texts().index("## Search help") < ui.texts().index("## Search results")
        assert screen._results is results
        ui.assert_within_limits()
        await ui.press(ui.key_for("close-help"))
        assert "## Search results" in ui.texts()
        assert "## Search help" not in ui.texts()
        assert service.search.await_count == 1
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
        assert screen._query == "base_title:first"


async def test_failed_toggle_keeps_the_previous_selection_and_results() -> None:
    screen = _screen(initial_id=None)
    screen._mode = "records"
    async with stage(screen, owner=object()) as ui:
        await ui.press(ui.key_for("smallest"))
        original_results = screen._results
        cast(AsyncMock, screen._search_service).search.side_effect = RuntimeError("search unavailable")
        await ui.press(ui.key_for("fastest"))
        assert screen._query == "base_title:smallest"
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
        await toggle_help(ui)
        assert any((r"fastest\_smallest" if records else "completion_at") in text for text in ui.texts())
        await ui.press(ui.key_for("search-reference.syntax"))
        assert any("leaves matches out" in text for text in ui.texts())
        ui.assert_within_limits()
        await ui.press(ui.key_for("search-reference.examples"))
        ui.assert_within_limits()
        await ui.press(ui.key_for("example-0"))
        assert "## Search results" in ui.texts()
        assert "## Search help" not in ui.texts()
        if records:
            assert ui.control(ui.key_for("fastest")).payload["style"] == 3
        assert screen._query == (
            "kind:door base_title:fastest version_scope:all_time" if records else "door width<=5 height<=6"
        )


async def test_video_editor_is_inside_schematic_tools() -> None:
    async with stage(_screen(schematics_available=True, video_enabled=True), owner=object()) as ui:
        assert "Schematic info" not in ui.texts()
        assert "Render video" not in ui.texts()
        await ui.press(ui.key_for("schematic-tools"))
        ui.control(ui.key_for("schematic-info"))
        ui.control(ui.key_for("guided-video"))
        assert "Advanced video recipe" not in ui.texts()
        await ui.press(ui.key_for("schematic-tools"))
        assert "Schematic info" not in ui.texts()
        assert "Render video" not in ui.texts()


async def test_catalogue_detail_crosses_a_service_keyset_boundary_using_its_offset_source() -> None:
    screen = _screen(initial_id=None)
    queries = cast(AsyncMock, screen._queries)
    queries.list_page.side_effect = (
        Page(
            items=tuple(
                DoorBuild(id=identifier, submission_status=Status.CONFIRMED) for identifier in range(27, 12, -1)
            ),
            total=16,
            next=PageAnchor(after_id=13),
            prev=None,
        ),
        Page(
            items=(DoorBuild(id=12, submission_status=Status.CONFIRMED),),
            total=16,
            next=None,
            prev=PageAnchor(offset=0),
        ),
    )
    queries.get.side_effect = lambda identifier: DoorBuild(id=identifier, submission_status=Status.CONFIRMED)
    async with stage(screen, owner=object()) as ui:
        await ui.select(ui.key_for("builds.open"), ["13"])
        assert "Build #13" in ui.texts()
        await ui.press(ui.key_for("builds.item-next"))
        assert "Build #12" in ui.texts()
        queries.list_page.assert_awaited_with(
            statuses=frozenset({Status.CONFIRMED, Status.PENDING}), selector=PageSelector(offset=15), page_size=15
        )


async def test_search_help_precedes_the_selected_build() -> None:
    screen = _screen()
    async with stage(screen, owner=object()) as ui:
        await toggle_help(ui)
        assert ui.texts().index("## Search help") < ui.texts().index("Build #7")
        for tab in ("fields", "syntax", "examples"):
            await ui.press(ui.key_for(f"search-reference.{tab}"))
            assert ui.texts().index("## Search help") < ui.texts().index("Build #7")
            ui.assert_within_limits()
            if tab != "examples":
                assert ui.root.plan is not None
                assert ui.root.plan.allocation is not None
        await toggle_help(ui)
        assert "Build #7" in ui.texts()
        ui.assert_within_limits()


async def test_help_keeps_an_open_builds_schematic_tools() -> None:
    screen = _screen(status=Status.PENDING, schematics_available=True, video_enabled=True)
    screen._render_build = lambda build: render_build_card(build, MediaPreviewClient())
    async with stage(screen, owner=object()) as ui:
        await ui.press(ui.key_for("schematic-tools"))
        ui.assert_within_limits()
        await toggle_help(ui)
        ui.control(ui.key_for("schematic-info"))
        await ui.press(ui.key_for("close-help"))
        await ui.press(ui.key_for("schematic-info"))
        ui.control(ui.key_for("all-builds"))


@pytest.mark.parametrize("records", [False, True])
async def test_help_with_full_catalogue_and_search_pages(records: bool) -> None:
    record_service = AsyncMock(spec=RecordService)
    record_service.list_page.return_value = Page(
        items=tuple(
            PublishedTitle(
                id=index,
                definition_id=index,
                title_id=uuid4(),
                title=f"Smallest door {index}",
                subtitle=None,
                base_title=BaseTitle.SMALLEST,
                build_kind=BuildKind.DOOR,
                version_scope=VersionScope.ALL_TIME,
                status=ResolutionStatus.RESOLVED,
                holder_build_ids=(7,),
                computed_at=datetime.now(UTC),
            )
            for index in range(1, 26)
        ),
        total=25,
        next=None,
        prev=None,
    )
    screen = _screen(initial_id=None, records=record_service, initial_records=records)
    queries = cast(AsyncMock, screen._queries)
    queries.list_page.return_value = Page(
        items=tuple(DoorBuild(id=index, submission_status=Status.CONFIRMED) for index in range(1, 26)),
        total=25,
        next=None,
        prev=None,
    )
    service = cast(AsyncMock, screen._search_service)
    service.search.return_value = SearchPage(
        hits=tuple(
            RecordSearchHit(str(index), f"Fastest door {index}", "Java", index, f"Door {index}", "fastest", "all_time")
            if records
            else BuildSearchHit(str(index), f"Door {index}", "confirmed", "A detailed door description")
            for index in range(1, 26)
        ),
        total=25,
        next=None,
        prev=None,
    )
    async with stage(screen, owner=object()) as ui:
        await toggle_help(ui)
        heading = "## Records" if records else "## Build catalogue"
        assert ui.texts().index("## Search help") < ui.texts().index(heading)
        ui.assert_within_limits()
        await toggle_help(ui)
        await submit_search(ui, "door", "")
        await toggle_help(ui)
        for tab in ("fields", "syntax", "examples"):
            await ui.press(ui.key_for(f"search-reference.{tab}"))
            assert ui.texts().index("## Search help") < ui.texts().index("## Search results")
            ui.assert_within_limits()
            if tab != "examples":
                assert ui.root.plan is not None
                assert ui.root.plan.allocation is not None
        await toggle_help(ui)
        assert "## Search results" in ui.texts()
        assert service.search.await_count == 1


async def test_catalogue_jump_form_requests_the_numbered_build_page() -> None:
    screen = _screen(initial_id=None)
    queries = cast(AsyncMock, screen._queries)

    async def page(*, statuses: frozenset[Status], selector: PageSelector, page_size: int) -> Page[Build]:
        assert Status.CONFIRMED in statuses
        return Page(
            items=tuple(
                DoorBuild(id=index + 1, submission_status=Status.CONFIRMED)
                for index in range(selector.offset, selector.offset + page_size)
            ),
            total=400,
            next=PageAnchor(offset=selector.offset + page_size) if selector.offset + page_size < 400 else None,
            prev=PageAnchor(offset=max(0, selector.offset - page_size)) if selector.offset else None,
        )

    queries.list_page.side_effect = page
    async with stage(screen, owner=object()) as ui:
        form = await ui.press_for_form(ui.key_for("builds.seek.jump"))
        await form.submit({"page": "13"})
        assert queries.list_page.await_args.kwargs["selector"] == PageSelector(offset=180)
        assert any("Page 13 of 27" in text for text in ui.texts())
        ui.assert_within_limits()


async def test_schematic_conversion_separates_releases_from_maintenance() -> None:
    async with stage(_screen(schematics_available=True), owner=object()) as ui:
        await ui.press(ui.key_for("schematic-tools"))
        form = await ui.press_for_form(ui.key_for("convert"))
        assert {
            getattr(item, "custom_id", None) for item in form.modal.walk_children() if hasattr(item, "custom_id")
        } == {
            "format",
        }
        for key in ("schematic-download", "schematic-render", "schematic-info"):
            ui.control(ui.key_for(key))
        ui.control(ui.key_for("convert-version"))
        maintenance = await ui.press_for_form(ui.key_for("conversion-maintenance"))
        assert {
            getattr(item, "custom_id", None) for item in maintenance.modal.walk_children() if hasattr(item, "custom_id")
        } == {
            "format",
            "data_version",
        }


async def test_live_search_keeps_editor_and_updates_results_on_every_edit() -> None:
    screen = _screen(initial_id=None)
    service = cast(AsyncMock, screen._search_service)
    async with stage(screen, owner=object()) as ui:
        interaction = await ui.press(ui.key_for("guided-search"))
        assert not interaction.sends
        editor = screen._guided
        assert editor is not None
        service.search.assert_not_awaited()
        for query in ("width:3 AND height:5", "width:7"):
            form = await ui.press_for_form(ui.key_for("advanced"))
            await form.submit({"query": query})
            assert screen._guided is editor
            assert screen._query == query
            assert service.search.call_args.args[0].query == query
            assert service.search.call_args.args[0].page_size == 15
            assert not ui.root.finished
            assert "## Search results" in ui.texts()
        await ui.press(ui.key_for("remove-0"))
        assert screen._query == ""
        assert screen._guided is editor
        ui.assert_within_limits()


async def test_failed_live_search_preserves_query_and_results() -> None:
    screen = _screen(initial_id=None)
    service = cast(AsyncMock, screen._search_service)
    async with stage(screen, owner=object()) as ui:
        await ui.press(ui.key_for("guided-search"))
        results = screen._results
        service.search.side_effect = RuntimeError("search unavailable")
        form = await ui.press_for_form(ui.key_for("advanced"))
        await form.submit({"query": "width:3"})
        assert screen._guided is not None
        assert screen._guided.query == screen._query == ""
        assert screen._results is results
        assert not ui.root.finished


async def test_search_panel_and_pickers_keep_browse_controls_and_results(monkeypatch: pytest.MonkeyPatch) -> None:
    screen = _screen(initial_id=None)
    suggestions = SuggestionService(
        SuggestionRegistry.of(
            (
                RegisteredSource("search_fields", RecordingProvider((Candidate.create("width", "Width"),))),
                RegisteredSource("search_sorts", RecordingProvider((Candidate.create("-width", "Widest first"),))),
            )
        )
    )
    cast(AsyncMock, screen._search_service).search.return_value = SearchPage(
        hits=tuple(BuildSearchHit(str(index), f"Door {index}", "confirmed", "A door") for index in range(5)),
        total=5,
        next=None,
        prev=None,
    )
    screen._query = "width:3 AND height:5"
    async with stage(screen, owner=object()) as ui:
        monkeypatch.setattr(ui._client, "actors", SimpleNamespace(resolve=AsyncMock(return_value=None)), raising=False)
        monkeypatch.setattr(ui._client, "services", SimpleNamespace(suggestions=suggestions), raising=False)
        await ui.press(ui.key_for("guided-search"))
        form = await ui.press_for_form(ui.key_for("advanced"))
        await form.submit({"query": screen._query})
        editor = screen._guided
        results = screen._results
        assert editor is not None
        assert editor in screen._find_nodes()
        ui.control(ui.key_for("open-id"))
        ui.control(ui.key_for("guided-search"))
        ui.control(ui.key_for("advanced"))

        await ui.press(ui.key_for("field"))
        await ui.press(ui.key_for("sort"))
        with anyio.fail_after(2):
            while not all(
                any(key.endswith(f"{picker}.results") for key in ui.root.snapshot().handler_keys)
                for picker in ("filter-lookup", "sort-lookup")
            ):
                await anyio.sleep(0.01)
        # Both panels, the filter rows, and a full page of results fit within the workspace.
        ui.assert_within_limits()
        ui.control(ui.key_for("open-id"))
        ui.control(ui.key_for("guided-search"))
        assert "## Search results" in ui.texts()
        assert screen._results is results
        for key in ("edit-0", "remove-0", "edit-1", "remove-1"):
            ui.control(ui.key_for(key))
        await ui.select(ui.key_for("filter-lookup.results"), ["width"])
        sort_picker = editor.sort_picker
        for value in ("7", "8", "9"):
            form = await ui.press_for_form(ui.key_for("value"))
            await form.submit({"value": value, "operator": "2"})
            assert screen._guided is editor
            assert editor.filter_open
            assert editor.selected is not None
            assert editor.selected.name == "width"
            assert editor.sort_picker is sort_picker
            assert screen._query == editor.query
            assert screen._query.endswith(f"width<={value}")
            assert "## Search results" in ui.texts()
            ui.assert_within_limits()
        ui.control(ui.key_for("edit-4"))
        results = screen._results
        query = screen._query
        cast(AsyncMock, screen._search_service).search.side_effect = RuntimeError("search unavailable")
        form = await ui.press_for_form(ui.key_for("value"))
        await form.submit({"value": "10", "operator": "2"})
        assert screen._query == editor.query == query
        assert screen._results is results
        assert editor.filter_open
        assert editor.selected is not None
        assert editor.selected.name == "width"
        ui.control(ui.key_for("edit-4"))
        await ui.press(ui.key_for("field"))
        assert not editor.filter_open
        assert screen._guided is editor
        assert screen._results is results
        await ui.press(ui.key_for("guided-search"))
        assert screen._guided is None
        assert screen._results is results
        ui.assert_within_limits()


async def test_records_default_to_standings_and_highlight_active_controls() -> None:
    records = AsyncMock(spec=RecordService)
    records.list_page.return_value = Page(items=(), total=0, next=None, prev=None)
    screen = _screen(initial_id=None, records=records, initial_records=True)
    async with stage(screen, owner=object()) as ui:
        assert "## Records" in ui.texts()
        assert "## Find a record" not in ui.texts()
        assert ui.control(ui.key_for("all-standings")).payload["style"] == 3
        assert ui.control(ui.key_for("guided-search")).payload["style"] == 2
        ui.control(ui.key_for("open-id"))
        await ui.press(ui.key_for("guided-search"))
        assert ui.control(ui.key_for("guided-search")).payload["style"] == 3
        ui.control(ui.key_for("open-id"))
        assert not any(key.endswith("cancel") for key in ui.root.snapshot().handler_keys)
        await toggle_help(ui)
        assert ui.control(ui.key_for("search-help")).payload["style"] == 3
        assert ui.texts().index("## Search help") < ui.texts().index("## Records")
        form = await ui.press_for_form(ui.key_for("advanced"))
        await form.submit({"query": "kind:door"})
        assert "## Search help" not in ui.texts()
        # A typed query is not a type, so All types stays highlighted until a type is chosen.
        assert ui.control(ui.key_for("all-standings")).payload["style"] == 3
        await ui.press(ui.key_for("smallest"))
        assert ui.control(ui.key_for("all-standings")).payload["style"] == 2
        await ui.press(ui.key_for("reset"))
        assert ui.control(ui.key_for("all-standings")).payload["style"] == 3
        assert screen._guided is not None
        assert screen._guided.query == ""
        assert "## Records" in ui.texts()


async def test_collapsed_query_and_sort_share_summary_with_reset() -> None:
    screen = _screen(initial_id=None)
    async with stage(screen, owner=object()) as ui:
        await submit_search(ui, "width:3", "-height")
        await ui.press(ui.key_for("guided-search"))
        summaries = [text for text in ui.texts() if "Width is 3" in text]
        assert summaries == ["Width is 3 · Height, largest first"]
        assert screen._guided is None
        assert not any(key.endswith("reset") for key in ui.root.snapshot().handler_keys)
        await ui.press(ui.key_for("guided-search"))
        ui.control(ui.key_for("reset"))
        await ui.press(ui.key_for("reset"))
        assert screen._query == screen._sort == ""
        assert screen._results is None
        assert "## Build catalogue" in ui.texts()


@pytest.mark.parametrize("records", [False, True])
async def test_expanded_editor_and_help_fit_together(records: bool) -> None:
    screen = _screen(initial_id=None)
    screen._mode = "records" if records else "builds"
    cast(AsyncMock, screen._search_service).search.return_value = SearchPage(
        hits=tuple(
            RecordSearchHit(str(index), f"Fastest door {index}", "Java", index, f"Door {index}", "fastest", "all_time")
            if records
            else BuildSearchHit(str(index), f"Door {index}", "confirmed", "A detailed door description")
            for index in range(25)
        ),
        total=25,
        next=None,
        prev=None,
    )
    async with stage(screen, owner=object()) as ui:
        await ui.press(ui.key_for("guided-search"))
        form = await ui.press_for_form(ui.key_for("advanced"))
        await form.submit({"query": "kind:door AND title:door" if records else "width:3 AND height:5"})
        await toggle_help(ui)
        for tab in ("fields", "syntax", "examples"):
            await ui.press(ui.key_for(f"search-reference.{tab}"))
            assert ui.texts().index("## Search help") < ui.texts().index("## Search results")
            ui.control(ui.key_for("advanced"))
            ui.control(ui.key_for("reset"))
            ui.assert_within_limits()
        await ui.press(ui.key_for("close-help"))
        assert "## Search results" in ui.texts()


async def test_editor_expansion_survives_switching_catalogue_tabs() -> None:
    records = AsyncMock(spec=RecordService)
    records.list_page.return_value = Page(items=(), total=0, next=None, prev=None)
    screen = _screen(initial_id=None, records=records)
    async with stage(screen, owner=object()) as ui:
        await ui.press(ui.key_for("guided-search"))
        for tab, scope in (("all-records", "records"), ("all-builds", "builds")):
            await ui.press(ui.key_for(tab))
            assert screen._guided is not None
            assert screen._guided.scope == scope
            ui.control(ui.key_for("advanced"))
            assert ui.control(ui.key_for("guided-search")).payload["label"] == "Done"
            assert ui.control(ui.key_for("guided-search")).payload["style"] == 3
        await ui.press(ui.key_for("guided-search"))
        await ui.press(ui.key_for("all-records"))
        assert screen._guided is None
        assert not any(key.endswith("advanced") for key in ui.root.snapshot().handler_keys)


async def test_expanded_reset_is_disabled_until_query_sort_or_categories_change() -> None:
    screen = _screen(initial_id=None)
    async with stage(screen, owner=object()) as ui:
        await ui.press(ui.key_for("guided-search"))
        assert ui.control(ui.key_for("reset")).payload["disabled"]
        form = await ui.press_for_form(ui.key_for("advanced"))
        await form.submit({"query": "width:3"})
        assert not ui.control(ui.key_for("reset")).payload.get("disabled", False)
        await ui.press(ui.key_for("reset"))
        assert ui.control(ui.key_for("reset")).payload["disabled"]
        await submit_search(ui, "", "-width")
        assert not ui.control(ui.key_for("reset")).payload.get("disabled", False)
        await ui.press(ui.key_for("reset"))
        assert ui.control(ui.key_for("reset")).payload["disabled"]


async def test_open_id_is_available_beside_record_tools() -> None:
    records = AsyncMock(spec=RecordService)
    records.gaps.return_value = ()
    records.title_gaps.return_value = ()
    tools = RecordsScreen(records, AsyncMock(), can_inspect=True, can_rebuild=False, authorize=AsyncMock())
    screen = _screen(initial_id=None, records=records, record_tools=tools)
    async with stage(screen, owner=object()) as ui:
        await ui.press(ui.key_for("record-tools"))
        form = await ui.press_for_form(ui.key_for("open-id"))
        await form.submit({"id": "7"})
        assert screen._mode == "builds"
        assert "Build #7" in ui.texts()
        ui.control(ui.key_for("open-id"))


async def test_examples_keep_help_and_editor_in_sync() -> None:
    screen = _screen(initial_id=None)
    async with stage(screen, owner=object()) as ui:
        await toggle_help(ui)
        editor = screen._guided
        await ui.press(ui.key_for("search-reference.examples"))
        for index in (0, 1):
            if index:
                await toggle_help(ui)
                await ui.press(ui.key_for("search-reference.examples"))
            await ui.press(ui.key_for(f"example-{index}"))
            assert not screen._show_search_help
            assert screen._guided is editor
            assert editor is not None
            assert editor.query == screen._query
            assert "## Search results" in ui.texts()
            ui.control(ui.key_for("advanced"))
            assert not ui.control(ui.key_for("reset")).payload.get("disabled", False)


async def test_expanded_search_filters_and_help_survive_expanding_a_build() -> None:
    screen = _screen(initial_id=None, schematics_available=True, video_enabled=True)
    screen._render_build = lambda build: render_build_card(build, MediaPreviewClient())
    build = DoorBuild(
        id=7,
        submission_status=Status.CONFIRMED,
        width=10,
        height=12,
        depth=3,
        versions=["Java 1.21"],
        source_messages=(MessageRef(id=3, guild_id=1, channel_id=2),),
        completion_time="2026-11-15",
        description="A compact piston door with synchronized opening and closing.",
    )
    build.creators_ign = ["Builder One", "Builder Two", "Builder Three"]
    cast(AsyncMock, screen._queries).get.return_value = build
    hit = BuildSearchHit("7", "Door 7", "confirmed", "A detailed door description")
    cast(AsyncMock, screen._search_service).search.return_value = SearchPage(hits=(hit,), total=1, next=None, prev=None)
    async with stage(screen, owner=object()) as ui:
        await toggle_help(ui)
        form = await ui.press_for_form(ui.key_for("advanced"))
        await form.submit({"query": "width:3 AND height:5 AND depth:2"})
        await ui.select(ui.key_for("search.open"), ["build:7"])
        await ui.press(ui.key_for("schematic-tools"))
        ui.control(ui.key_for("schematic-info"))
        ui.control(ui.key_for("search.back"))
        ui.assert_within_limits()
        await ui.press(
            next(key for key in ui.root.snapshot().handler_keys if "__cursor_next." in key and key.endswith("clauses"))
        )
        ui.control(ui.key_for("edit-2"))
        await ui.press(
            next(
                key
                for key in ui.root.snapshot().handler_keys
                if "__cursor_previous." in key and key.endswith("clauses")
            )
        )
        await toggle_help(ui)
        for tab in ("fields", "syntax", "examples"):
            await ui.press(ui.key_for(f"search-reference.{tab}"))
            ui.control(ui.key_for("advanced"))
            ui.assert_within_limits()
        await ui.press(ui.key_for("close-help"))
        ui.control(ui.key_for("schematic-info"))
        await ui.press(ui.key_for("search.back"))
        ui.control(ui.key_for("search.open"))
        assert screen._guided is not None
        assert screen._query == "width:3 AND height:5 AND depth:2"


async def test_records_default_to_all_statuses_and_reset_clears_status_filter() -> None:
    records = AsyncMock(spec=RecordService)
    records.list_page.return_value = Page(items=(), total=0, next=None, prev=None)
    screen = _screen(initial_id=None, records=records, initial_records=True)
    service = cast(AsyncMock, screen._search_service)
    async with stage(screen, owner=object()) as ui:
        assert screen._query == ""
        await ui.press(ui.key_for("guided-search"))
        assert screen._guided is not None
        assert screen._guided.query == ""
        form = await ui.press_for_form(ui.key_for("advanced"))
        await form.submit({"query": "status:unresolved"})
        await ui.press(ui.key_for("remove-0"))
        assert screen._query == ""
        assert service.search.call_args.args[0].query == ""
        assert screen._results is not None
        await ui.press(ui.key_for("all-standings"))
        assert screen._results is None
        form = await ui.press_for_form(ui.key_for("advanced"))
        await form.submit({"query": "status:unresolved"})
        await ui.press(ui.key_for("reset"))
        assert screen._query == screen._guided.query == ""
        assert screen._results is None


async def test_record_status_is_not_injected_into_an_explicit_expression() -> None:
    screen = _screen(initial_id=None)
    screen._mode = "records"
    async with stage(screen, owner=object()) as ui:
        await submit_search(ui, "status:unresolved OR status:no_candidate")
        request = cast(AsyncMock, screen._search_service).search.call_args.args[0]
        assert request.query == "status:unresolved OR status:no_candidate"
