"""Build browser actions participate in the UI's state transactions."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import anyio
import discord
import pytest

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.bot.catalogue.browse import BuildBrowseScreen, _BuildDetail, _open_credit
from squid.bot.catalogue.records import RecordsScreen
from squid.bot.catalogue.search.clauses import CatalogueQuery
from squid.bot.catalogue.search.results import SearchFocus
from squid.bot.submission.build_card import render_build_card
from squid.bot.utils.web import MediaPreviewClient
from squid.core.accounts.application import AccountService
from squid.core.accounts.models import CataloguePreference
from squid.core.actors import ActingContext, UserActor
from squid.core.builds.application import BuildListSort, BuildQueryService, BuildService
from squid.core.builds.domain import BuildStatus, Credit, CreditRole
from squid.core.builds.models import Build, DoorBuild
from squid.core.messages.domain import MessageRef
from squid.core.pagination import Page, PageAnchor, PageCursor, PageSelector
from squid.core.permissions.domain.catalogue import (
    BUILD_SUBMISSION_APPROVE,
    BUILD_SUBMISSION_REJECT,
    BUILD_SUBMISSION_VIEW_PENDING,
    RECORD_ENTRY_INSPECT,
)
from squid.core.records.application import PublicRecordQueryService, RecordService
from squid.core.records.application.models import (
    PublishedRecordResult,
    RecordGap,
    RecordListFilters,
    RecordNameDiagnostic,
)
from squid.core.records.domain import BuildKind, RecordCriterion, RecordFact, VersionScope
from squid.core.schematics.application import SchematicService
from squid.core.search.application import SearchService
from squid.core.search.application.fields import DEFAULT_FIELD_REGISTRY
from squid.core.search.application.parser import SearchQueryParser
from squid.core.search.application.results import BuildSearchHit, RecordSearchHit, SearchPage
from squid.core.search.domain import BuildQuery, RecordQuery, SearchMode, SearchRequest, SearchResultAnchor, SearchScope
from squid.core.search.domain.query import TextExpression
from squid.core.suggestions.application import Candidate, SuggestionRegistry, SuggestionService
from squid.core.suggestions.application import SuggestionSource as RegisteredSource
from squid_i18n import Text
from squid_reactivity import LocalTopicBus
from squid_ui.ext.discord.target_types import ComponentsV2Target
from squid_ui.ext.discord.testing import StagedUI, stage
from squid_ui.testing import find_all, press_event, submit_event
from tests.support.grants import grants
from tests.unit.suggestions.test_services import RecordingProvider

pytestmark = pytest.mark.usefixtures("discord_screen_context")


def _screen(
    *,
    initial_id: int | None = 7,
    status: BuildStatus = BuildStatus.APPROVED,
    schematics_available: bool = False,
    schematic_attached: bool = True,
    video_enabled: bool = False,
    records: PublicRecordQueryService | None = None,
    initial_records: bool = False,
    record_tools: RecordsScreen | None = None,
    submitter_account_id: int | None = None,
    credits: list[Credit] | None = None,
    accounts: AccountService | None = None,
) -> BuildBrowseScreen:
    build = DoorBuild(id=7, submission_status=status, submitter_account_id=submitter_account_id)
    build.credits = credits or []
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

    async def refresh_posts(_build_id: int) -> None:
        pass

    return BuildBrowseScreen(
        cast(BuildQueryService, queries),
        cast(BuildService, None),
        AsyncMock(),
        AsyncMock(return_value=UserActor(account_id=1, discord_user_id=1)),
        cast(SchematicService, schematics),
        topics=LocalTopicBus(),
        initial_id=initial_id,
        render_build=render_build,
        grants=grants(BUILD_SUBMISSION_VIEW_PENDING, BUILD_SUBMISSION_APPROVE, BUILD_SUBMISSION_REJECT),
        actor_account_id=1,
        refresh_posts=refresh_posts,
        records=records,
        search=search,
        record_tools=record_tools,
        initial_records=initial_records,
        accounts=accounts,
    )


async def submit_search(ui: StagedUI[BuildBrowseScreen], query: str, sort: str = "") -> None:
    if ui.component._guided is not None:
        await ui.press("guided-search")
    ui.component._sort = sort
    await ui.press("guided-search")
    assert ui.component._guided is not None
    form = await ui.press_for_form("advanced")
    await form.submit({"query": query})
    assert ui.component._guided.query.text == query


async def toggle_help(ui: StagedUI[BuildBrowseScreen]) -> None:
    if ui.component._guided is None:
        await ui.press("guided-search")
    await ui.press("search-help")


async def pick_sort(ui: StagedUI[BuildBrowseScreen], sort: str, monkeypatch: pytest.MonkeyPatch) -> None:
    if ui.component._guided is None:
        await ui.press("guided-search")
    assert ui.component._guided is not None
    if ui.component._guided.sort_picker is None:
        provider = RecordingProvider((Candidate.create("-width", "Widest"), Candidate.create("width", "Narrowest")))
        suggestions = SuggestionService(SuggestionRegistry.of((RegisteredSource("search_sorts", provider),)))
        monkeypatch.setattr(
            ui._client,
            "actors",
            SimpleNamespace(resolve=AsyncMock(return_value=ActingContext.current().effective)),
            raising=False,
        )
        monkeypatch.setattr(ui._client, "services", SimpleNamespace(suggestions=suggestions), raising=False)
        await ui.press("sort")
    with anyio.fail_after(2):
        while not any(key.endswith("sort-lookup.results") for key in ui.root.snapshot().handler_keys):
            await anyio.sleep(0.01)
    await ui.select("sort-lookup.results", [sort])


async def test_back_to_builds_replaces_the_detail_with_the_catalogue() -> None:
    async with stage(_screen(), owner=object()) as ui:
        assert "Build #7" in ui.texts()
        # One Back: the opened build has no separate list of its own to return to.
        assert [key for key in ui.root.snapshot().handler_keys if key.endswith("back")] == [
            ui.control("linked-build.back").key
        ]

        await ui.press("linked-build.back")

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
            await screen._run_search(CatalogueQuery.from_text(screen._search_parser, "door"), "")
        else:
            await screen._open_id(submit_event({"id": 7}))

    assert screen._results is not None if search else screen._selected is not None


@pytest.mark.parametrize("records", [False, True], ids=["build", "record"])
async def test_search_opens_full_build_and_preserves_results(records: bool) -> None:
    screen = _screen(initial_id=None, schematics_available=True)
    hit = (
        RecordSearchHit("10", "Fastest door", "Java", 7, "Door", "fastest", "all_time")
        if records
        else BuildSearchHit("7", "Door", "approved", "A door")
    )
    service = cast(AsyncMock, screen._search_service)
    service.search.return_value = SearchPage(hits=(hit,), total=1, next=None, prev=None)
    async with stage(screen, owner=object()) as ui:
        await submit_search(ui, "door", "")
        results = screen._results
        # Choosing a result opens the full build in one step.
        await ui.select("search.open", [f"{hit.resource_kind}:{hit.source_id}"])

        assert "Build #7" in ui.texts()
        ui.control("schematic-tools")
        assert not any(key.endswith("open-build") for key in ui.root.snapshot().handler_keys)
        ui.assert_within_limits()

        await ui.press("search.back")
        assert screen._results is results
        ui.control("search.open")
        assert service.search.await_count == 1


@pytest.mark.parametrize("entry", ["catalogue", "direct", "search"])
async def test_sort_keeps_the_focused_build_on_its_new_page(entry: str, monkeypatch: pytest.MonkeyPatch) -> None:
    screen = _screen(initial_id=7 if entry == "direct" else None)
    service = cast(AsyncMock, screen._search_service)
    hit = BuildSearchHit("7", "Door 7", "approved")
    service.search.return_value = SearchPage((hit,), total=1, next=None, prev=None)
    async with stage(screen, owner=object()) as ui:
        if entry == "catalogue":
            await ui.select("builds.open", ["7"])
        elif entry == "search":
            await submit_search(ui, "door")
            await ui.select("search.open", ["build:7"])
        assert "Build #7" in ui.texts()

        service.search.return_value = SearchPage(
            (BuildSearchHit("9", "Door 9", "approved"), hit), total=17, next=None, prev=PageAnchor(offset=0), offset=15
        )
        await pick_sort(ui, "-width", monkeypatch)

        request: SearchRequest = service.search.call_args.args[0]
        assert request.anchor == SearchResultAnchor("build", "7")
        assert "Build #7" in ui.texts()
        assert screen._results is not None
        assert screen._results.request.offset == 15
        assert screen._results.request.anchor is None
        calls = service.search.await_count
        await pick_sort(ui, "-width", monkeypatch)
        assert service.search.await_count == calls
        await ui.press("search.back")
        ui.control("search.open")
        assert screen._results.request.offset == 15
        ui.assert_within_limits()


async def test_sort_keeps_the_focused_record_and_its_open_holder(monkeypatch: pytest.MonkeyPatch) -> None:
    records = AsyncMock(spec=PublicRecordQueryService)
    record = PublishedRecordResult(
        result_id=10,
        definition_id=1,
        record_id=uuid4(),
        title="Smallest 2x2 door",
        subtitle=None,
        criterion=RecordCriterion.SMALLEST,
        build_kind=BuildKind.DOOR,
        version_scope=VersionScope.ALL_TIME,
        category_key="door:door|2x3|t[]|Door:r[]:p[]",
        holder_build_ids=(7, 9),
        computed_at=datetime.now(UTC),
    )
    records.list_page.return_value = Page(items=(record,), total=1, next=None, prev=None)
    screen = _screen(initial_id=None, records=records, initial_records=True)
    service = cast(AsyncMock, screen._search_service)
    key = f"record:{record.record_id}"
    hit = RecordSearchHit(key, record.title, None, 9, "Door 9", "smallest", "all_time", holder_build_ids=(7, 9))
    service.search.return_value = SearchPage((hit,), total=1, next=None, prev=None)
    async with stage(screen, owner=object()) as ui:
        await ui.select("records.open", ["10"])
        await ui.press("holder-7")
        await pick_sort(ui, "-width", monkeypatch)

        assert service.search.call_args.args[0].anchor == SearchResultAnchor("record", key)
        assert "Build #7" in ui.texts()
        await ui.press("linked-build.back")
        assert f"## {record.title}" in ui.texts()
        await ui.press("holder-7")
        await pick_sort(ui, "width", monkeypatch)

        assert service.search.call_args.args[0].anchor == SearchResultAnchor("record", key)
        assert screen._sort == "width"
        assert screen._results is not None
        assert screen._results.focus == SearchFocus(SearchResultAnchor("record", key), record_id=key, show_records=True)
        assert "Build #7" in ui.texts()
        await ui.press("linked-build.back")
        assert f"## {record.title}" in ui.texts()
        ui.assert_within_limits()


@pytest.mark.parametrize(("submitter_account_id", "editable"), [(1, True), (2, False)])
def test_only_the_submitter_or_an_editor_gets_edit(submitter_account_id: int, editable: bool) -> None:
    screen = _screen(submitter_account_id=submitter_account_id)

    actions = screen._build_actions(DoorBuild(id=7, submitter_account_id=submitter_account_id))

    assert [action.key for action in actions] == (["edit"] if editable else [])


@pytest.mark.parametrize("action", ["approve", "reject"])
async def test_review_confirmation_can_open_and_cancel(action: str) -> None:
    async with stage(_screen(status=BuildStatus.PENDING), owner=object()) as ui:
        await ui.press(action)

        heading = f"## {action.title()} build #7?"
        assert heading in ui.texts()

        await ui.press("build-review.cancel")

        assert heading not in ui.texts()
        ui.control(action)


async def test_a_failed_review_request_does_not_leave_a_confirmation() -> None:
    screen = _screen(status=BuildStatus.PENDING)
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
    screen = _screen(status=BuildStatus.PENDING, schematics_available=True)
    async with stage(screen, owner=object()) as ui:
        await ui.press("schematic-tools")
        ui.control("schematic-info")
        await ui.press("approve")
        await ui.press("build-review.cancel")
        ui.control("schematic-info")


async def test_each_credit_opens_its_creator_page_or_searches_its_name() -> None:
    owner = uuid4()
    accounts = AsyncMock(spec=AccountService)
    accounts.credit_owners.return_value = {0: owner}
    screen = _screen(
        credits=[Credit(name="Notch"), Credit(name="Jeb", role=CreditRole.LAYOUT)],
        accounts=cast(AccountService, accounts),
    )
    await screen.on_load()
    assert screen._selected is not None
    detail = cast(_BuildDetail, screen._selected._detail.value)
    await detail.projection.resolve()

    (creators,) = find_all(detail.render(), sl.semantic.Choices, key="creators")
    assert [
        (Text(choice.label).resolve(), Text(choice.description or "").resolve()) for choice in creators.choices
    ] == [
        ("Notch", "Open creator page"),
        ("Jeb (layout)", "Builds credited to this name"),
    ]

    router = Mock(resolve=Mock(return_value="match"), dispatch=AsyncMock())
    event = Mock(selected=("0",), context=Mock(client=Mock(ui_router=router)))
    state = detail.projection.status
    assert isinstance(state, sl.resources.Ready)
    build, _, _, owners = state.value
    await _open_credit(build.credits, owners, event)

    router.resolve.assert_called_once_with(f"/creators/{owner}")
    router.dispatch.assert_awaited_once()


async def test_open_id_through_the_discord_modal() -> None:
    async with stage(_screen(initial_id=None), owner=object()) as ui:
        form = await ui.press_for_form("open-id")
        await form.submit({"id": "7"})
        assert "Build #7" in ui.texts()
        ui.control("linked-build.item-next")
        ui.control("open-id")


@pytest.mark.parametrize("schematics_available", [False, True])
async def test_schematic_tools_are_hidden_without_an_attachment(schematics_available: bool) -> None:
    screen = _screen(schematics_available=schematics_available, schematic_attached=False)
    async with stage(screen, owner=object()) as ui:
        assert "Build #7" in ui.texts()
        assert not any(key.endswith("schematic-tools") for key in ui.root.snapshot().handler_keys)


async def test_schematic_tools_are_collapsed_on_arrival() -> None:
    async with stage(_screen(schematics_available=True), owner=object()) as ui:
        assert "Schematic info" not in ui.texts()
        await ui.press("schematic-tools")
        ui.control("schematic-info")


@pytest.mark.parametrize("newer", [False, True])
async def test_detail_navigation_uses_id_anchors_and_preserves_visibility(newer: bool) -> None:
    screen = _screen()
    queries = cast(AsyncMock, screen._queries)
    queries.list_page.return_value = Page(
        items=(DoorBuild(id=8 if newer else 6, submission_status=BuildStatus.APPROVED),),
        total=20,
        next=None,
        prev=None,
    )
    async with stage(screen, owner=object()) as ui:
        await ui.press("linked-build.item-previous" if newer else "linked-build.item-next")
        assert f"Build #{8 if newer else 6}" in ui.texts()
        queries.list_page.assert_awaited_once_with(
            context=ActingContext.current(),
            statuses=frozenset({BuildStatus.APPROVED, BuildStatus.PENDING}),
            sort=BuildListSort.ID,
            selector=PageSelector(cursor=PageCursor(BuildListSort.ID, 7, 7, backward=newer)),
            page_size=1,
        )


async def test_list_selection_and_return_keep_the_list_available() -> None:
    async with stage(_screen(initial_id=None), owner=object()) as ui:
        await ui.select("builds.open", ["7"])
        assert "Build #7" in ui.texts()
        await ui.press("builds.back")
        assert "## Build catalogue" in ui.texts()
        ui.control("builds.open")


async def test_records_open_holders_and_preserve_the_selected_record() -> None:
    records = AsyncMock(spec=PublicRecordQueryService)
    records.list_page.return_value = Page(
        items=(
            PublishedRecordResult(
                result_id=10,
                definition_id=1,
                record_id=uuid4(),
                title="Smallest 2x2 door",
                subtitle=None,
                criterion=RecordCriterion.SMALLEST,
                build_kind=BuildKind.DOOR,
                version_scope=VersionScope.ALL_TIME,
                category_key="door:door|2x3|t[]|Door:r[]:p[]",
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
        await ui.press("all-criteria")
        await ui.select("records.open", ["10"])
        await ui.press("holder-7")
        assert "Build #7" in ui.texts()
        await ui.press("linked-build.back")
        assert "## Smallest 2x2 door" in ui.texts()
        assert any("Lowest volume" in text for text in ui.texts())
        assert any("Tied holders" in text for text in ui.texts())
        assert not any("unresolved" in text or "no_candidate" in text for text in ui.texts())
        ui.control("holder-9")
        await ui.press("all-builds")
        assert "## Build catalogue" in ui.texts()
        ui.assert_within_limits()


async def test_missing_id_keeps_the_current_build() -> None:
    screen = _screen()
    async with stage(screen, owner=object()) as ui:
        cast(AsyncMock, screen._queries).get.return_value = None
        form = await ui.press_for_form("open-id")
        await form.submit({"id": "999"})
        assert "Build #7" in ui.texts()


async def test_record_tools_load_on_request_and_return_to_builds() -> None:
    records = AsyncMock()
    records.gaps.return_value = ()
    records.title_gaps.return_value = ()
    tools = RecordsScreen(records, AsyncMock(), grants=grants(RECORD_ENTRY_INSPECT))
    async with stage(_screen(initial_id=None, record_tools=tools), owner=object()) as ui:
        records.gaps.assert_not_awaited()
        await ui.press("record-tools")
        records.gaps.assert_awaited_once()
        assert "## Records missing evidence" in ui.texts()
        assert "Every active record has the evidence it needs." in ui.texts()
        ui.assert_within_limits()
        await ui.press("all-builds")
        assert "## Build catalogue" in ui.texts()


@pytest.mark.parametrize("count", [2, 6])
@pytest.mark.parametrize("browser", ["records", "gaps", "titles"])
async def test_long_record_titles_fit_picker_controls_and_timeout(browser: str, count: int) -> None:
    title = "Smallest seamless flush redstone door " * 4
    records = AsyncMock(spec=PublicRecordQueryService)
    inspection = AsyncMock(spec=RecordService)
    records.list_page.return_value = Page(
        items=tuple(
            PublishedRecordResult(
                result_id=index,
                definition_id=index,
                record_id=uuid4(),
                title=title,
                subtitle=None,
                criterion=RecordCriterion.SMALLEST,
                build_kind=BuildKind.DOOR,
                version_scope=VersionScope.ALL_TIME,
                category_key="door:door|2x3|t[]|Door:r[]:p[]",
                holder_build_ids=(7,),
                computed_at=datetime.now(UTC),
            )
            for index in range(count)
        ),
        total=count,
        next=None,
        prev=None,
    )
    inspection.gaps.return_value = tuple(
        RecordGap(index, title, None, RecordCriterion.SMALLEST, (7,), (RecordFact.FIXED_VOLUME,), BuildKind.DOOR)
        for index in range(count)
    )
    inspection.title_gaps.return_value = tuple(
        RecordNameDiagnostic(index, title, ({"code": "unknown"},), BuildKind.DOOR) for index in range(count)
    )
    tools = RecordsScreen(inspection, AsyncMock(), grants=grants(RECORD_ENTRY_INSPECT))
    screen = _screen(initial_id=None, records=records, initial_records=True, record_tools=tools)
    async with stage(screen, owner=object()) as ui:
        if browser == "records":
            await ui.press("all-criteria")
        else:
            await ui.press("record-tools")
            if browser == "titles":
                await ui.press("records-tabs.titles")
        assert any(title in text for text in ui.texts())
        ui.assert_within_limits()
        await ui.root.handle_timeout()
        ui.assert_within_limits()


async def test_full_build_card_fits_with_catalogue_navigation() -> None:
    screen = _screen(status=BuildStatus.PENDING, schematics_available=True)
    screen._render_build = lambda build: render_build_card(build, MediaPreviewClient())
    async with stage(screen, owner=object()) as ui:
        ui.control("linked-build.item-next")
        ui.control("schematic-tools")
        assert any("Submission ID: 7" in text for text in ui.texts())
        ui.assert_within_limits()
        await toggle_help(ui)
        for tab in ("fields", "syntax", "examples"):
            await ui.press(f"search-reference.{tab}")
            assert any("Submission ID: 7" in text for text in ui.texts())
            ui.assert_within_limits()
        await ui.press("close-help")
        assert any("Submission ID: 7" in text for text in ui.texts())


async def test_record_tools_show_populated_diagnostics_after_a_click() -> None:
    records = AsyncMock()
    records.gaps.return_value = (
        RecordGap(
            1, "Smallest 2x2 door", None, RecordCriterion.SMALLEST, (7,), (RecordFact.FIXED_VOLUME,), BuildKind.DOOR
        ),
    )
    records.title_gaps.return_value = ()
    tools = RecordsScreen(records, AsyncMock(), grants=grants(RECORD_ENTRY_INSPECT))
    async with stage(_screen(initial_id=None, record_tools=tools), owner=object()) as ui:
        await ui.press("record-tools")
        assert "## Records missing evidence" in ui.texts()
        await ui.select("evidence-gaps.open", ["1"])
        assert any("Needs Volume" in text for text in ui.texts())
        ui.assert_within_limits()
        # A gap's build opens in place of the tools, and Back returns to the same gap.
        await ui.press("gap-build-7")
        assert "Build #7" in ui.texts()
        await ui.press("linked-build.back")
        assert any("Needs Volume" in text for text in ui.texts())


@pytest.mark.parametrize("records", [False, True])
async def test_query_expression_is_submitted_from_the_guided_search(records: bool) -> None:
    screen = _screen(initial_id=None)
    if records:
        screen._mode = "records"
    async with stage(screen, owner=object()) as ui:
        typed = "  door   KIND:door  "
        await submit_search(ui, typed, "-created_at")
        request = cast(AsyncMock, screen._search_service).search.call_args.args[0]
        assert request.mode is SearchMode.LEXICAL
        assert request.scope is (SearchScope.RECORDS if records else SearchScope.BUILDS)
        assert isinstance(request.parsed_query, RecordQuery if records else BuildQuery)
        if records:
            assert request.parsed_query.normalized.endswith("AND version_scope:all_time")
        else:
            assert request.parsed_query is screen._query.parsed
        assert request.query == typed
        assert screen._query.parsed.normalized == "door AND kind:door"
        await ui.press("guided-search")
        assert any(typed in text for text in ui.texts())
        await ui.press("guided-search")
        form = await ui.press_for_form("advanced")
        query_input = next(item for item in form.modal.walk_children() if isinstance(item, discord.ui.TextInput))
        assert query_input.default == typed
        assert "## Search results" in ui.texts()
        assert not any("Search by meaning" in text for text in ui.texts())
        ui.assert_within_limits()


async def test_record_category_shortcut_includes_all_statuses() -> None:
    records = AsyncMock(spec=PublicRecordQueryService)
    records.list_page.return_value = Page(items=(), total=0, next=None, prev=None)
    screen = _screen(initial_id=None, records=records, initial_records=True)
    async with stage(screen, owner=object()) as ui:
        await ui.press("smallest")
        request = cast(AsyncMock, screen._search_service).search.call_args.args[0]
        assert request.query == "criterion:smallest"
        assert request.scope is SearchScope.RECORDS
        assert "## Search results" in ui.texts()


async def test_record_versions_filter_browse_and_search() -> None:
    records = AsyncMock(spec=PublicRecordQueryService)

    async def list_page(*, filters: RecordListFilters, **kwargs: object) -> Page[PublishedRecordResult]:
        version_scope = filters.version_scope
        assert version_scope is not None
        current = version_scope is VersionScope.UP_TO_DATE
        record = PublishedRecordResult(
            result_id=2 if current else 1,
            definition_id=1,
            record_id=uuid4(),
            title="Fastest piston door" if current else "Smallest piston door",
            subtitle=None,
            criterion=RecordCriterion.FASTEST if current else RecordCriterion.SMALLEST,
            build_kind=BuildKind.DOOR,
            version_scope=version_scope,
            category_key="door:door|2x3|t[]|Door:r[]:p[]",
            holder_build_ids=(7,),
            computed_at=datetime.now(UTC),
        )
        return Page(items=(record,), total=1, next=None, prev=None)

    records.list_page.side_effect = list_page
    screen = _screen(initial_id=None, records=records, initial_records=True)
    service = cast(AsyncMock, screen._search_service)
    async with stage(screen, owner=object()) as ui:
        assert "• Smallest piston door" in ui.texts()
        assert ui.control("all_time").payload["style"] == 3
        await ui.press("up_to_date")
        assert "• Fastest piston door" in ui.texts()
        assert ui.control("up_to_date").payload["style"] == 3
        await ui.press("fastest")
        request = service.search.call_args.args[0]
        assert request.parsed_query.normalized == "version_scope:up_to_date AND criterion:fastest"
        await ui.press("all_time")
        request = service.search.call_args.args[0]
        assert request.parsed_query.normalized == "criterion:fastest AND version_scope:all_time"
        assert ui.control("all_time").payload["style"] == 3
        ui.assert_within_limits()


async def test_record_toggles_preserve_text_and_sort_and_can_be_combined_or_removed() -> None:
    screen = _screen(initial_id=None)
    screen._mode = "records"
    service = cast(AsyncMock, screen._search_service)
    async with stage(screen, owner=object()) as ui:
        await submit_search(ui, 'title:"vault door"', "-created_at")
        await ui.press("smallest")
        assert ui.control("smallest").payload["style"] == 3
        await ui.press("fastest")
        request = service.search.call_args.args[0]
        assert request.query == 'title:"vault door" AND (criterion:fastest OR criterion:smallest)'
        assert request.sort is not None
        assert request.sort.field == "created_at"
        assert screen._guided is not None
        assert screen._query == screen._guided.query
        assert screen._query.text == request.query
        assert screen._sort == "-created_at"
        await submit_search(ui, request.query, "-created_at")
        assert service.search.call_args.args[0].query == request.query
        await ui.press("smallest")
        assert ui.control("smallest").payload["style"] == 2
        assert service.search.call_args.args[0].query == 'title:"vault door" AND criterion:fastest'
        await ui.press("fastest")
        assert service.search.call_args.args[0].query == 'title:"vault door"'
        ui.assert_within_limits()


@pytest.mark.parametrize("query", ["smallest door", 'door AND CRITERION:"smallest"'])
async def test_record_buttons_edit_typed_title_filters(query: str) -> None:
    screen = _screen(initial_id=None)
    screen._mode = "records"
    async with stage(screen, owner=object()) as ui:
        await submit_search(ui, query, "-created_at")
        assert ui.control("smallest").payload["style"] == 3
        assert ui.control("all-criteria").payload["style"] == 2
        await ui.press("fastest")
        assert screen._guided is not None
        assert "(criterion:fastest OR criterion:smallest)" in screen._guided.query.text
        await ui.press("smallest")
        expression = screen._query.parsed.expression
        assert expression in (
            SearchQueryParser().parse("door AND criterion:fastest").expression,
            SearchQueryParser().parse("criterion:fastest AND door").expression,
        )
        await ui.press("remove-0" if screen._query.text.startswith("criterion") else "remove-1")
        assert ui.control("all-criteria").payload["style"] == 3
        assert screen._guided.query.text == "door"
        assert screen._sort == "-created_at"


@pytest.mark.parametrize(
    "query",
    [
        "NOT criterion:smallest",
        "criterion:smallest OR kind:door",
        "criterion:smallest AND criterion:fastest",
        "criterion:fastest_smallest",
    ],
)
async def test_record_buttons_leave_complex_title_filters_to_the_editor(query: str) -> None:
    screen = _screen(initial_id=None)
    screen._mode = "records"
    async with stage(screen, owner=object()) as ui:
        await submit_search(ui, query)
        for key in ("smallest", "fastest", "first", "all-criteria"):
            assert ui.control(key).payload["disabled"]


async def test_all_types_preserves_literal_keywords() -> None:
    screen = _screen(initial_id=None)
    screen._mode = "records"
    async with stage(screen, owner=object()) as ui:
        await submit_search(ui, "fastest AND criterion:smallest")
        await ui.press("all-criteria")
        assert screen._query.text == "(fastest)"
        assert ui.control("all-criteria").payload["style"] == 3


async def test_all_types_clears_title_filters_and_preserves_other_filters() -> None:
    records = AsyncMock(spec=PublicRecordQueryService)
    records.list_page.return_value = Page(items=(), total=0, next=None, prev=None)
    screen = _screen(initial_id=None, records=records, initial_records=True)
    service = cast(AsyncMock, screen._search_service)
    async with stage(screen, owner=object()) as ui:
        assert ui.control("smallest").payload["label"] == "Smallest"
        await ui.press("smallest")
        await ui.press("all-criteria")
        assert screen._query.text == ""
        assert screen._results is None
        assert ui.control("smallest").payload["style"] == 2
        assert "## Records" in ui.texts()
        # With a typed query, the query and sort stay and the search reruns without types.
        await submit_search(ui, "door criterion:smallest", "-created_at")
        await ui.press("all-criteria")
        assert screen._query.text == "door"
        assert screen._sort == "-created_at"
        assert service.search.call_args.args[0].query == "door"
        ui.assert_within_limits()


@pytest.mark.parametrize("records", [False, True])
async def test_search_help_precedes_lists_and_results_without_refetching(records: bool) -> None:
    record_service = AsyncMock(spec=PublicRecordQueryService)
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
        await ui.press("close-help")
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
            ui.control(category)
        await ui.press("first")
        assert "## Search results" in ui.texts()
        assert screen._selected is None
        assert screen._query.text == "criterion:first"


async def test_failed_toggle_keeps_the_previous_selection_and_results() -> None:
    screen = _screen(initial_id=None)
    screen._mode = "records"
    async with stage(screen, owner=object()) as ui:
        await ui.press("smallest")
        original_results = screen._results
        cast(AsyncMock, screen._search_service).search.side_effect = RuntimeError("search unavailable")
        await ui.press("fastest")
        assert screen._query.text == "criterion:smallest"
        assert screen._results is original_results
        assert ui.control("smallest").payload["style"] == 3
        assert ui.control("fastest").payload["style"] == 2


async def test_catalogue_shows_exactly_one_active_workspace() -> None:
    records = AsyncMock(spec=PublicRecordQueryService)
    inspection = AsyncMock(spec=RecordService)
    inspection.gaps.return_value = ()
    inspection.title_gaps.return_value = ()
    tools = RecordsScreen(inspection, AsyncMock(), grants=grants(RECORD_ENTRY_INSPECT))
    screen = _screen(initial_id=None, records=records, record_tools=tools)
    keys = ("all-builds", "all-records", "record-tools")
    async with stage(screen, owner=object()) as ui:
        for selected in keys:
            if selected != "all-builds":
                await ui.press(selected)
            for key in keys:
                control = ui.control(key).payload
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
        if records:
            assert any("volume<=100 opening_time<=10gt" in text for text in ui.texts())
        await ui.press("search-reference.syntax")
        assert any("leaves matches out" in text for text in ui.texts())
        ui.assert_within_limits()
        await ui.press("search-reference.examples")
        ui.assert_within_limits()
        await ui.press("example-0")
        assert "## Search results" in ui.texts()
        assert "## Search help" not in ui.texts()
        if records:
            assert ui.control("fastest").payload["style"] == 3
        assert screen._query.text == (
            "kind:door AND criterion:fastest AND version_scope:all_time AND door_height>=3"
            if records
            else "door AND width<=5 AND height<=6"
        )


async def test_video_editor_is_inside_schematic_tools() -> None:
    async with stage(_screen(schematics_available=True, video_enabled=True), owner=object()) as ui:
        assert "Schematic info" not in ui.texts()
        assert "Render video" not in ui.texts()
        await ui.press("schematic-tools")
        ui.control("schematic-info")
        ui.control("guided-video")
        assert "Advanced video recipe" not in ui.texts()
        await ui.press("schematic-tools")
        assert "Schematic info" not in ui.texts()
        assert "Render video" not in ui.texts()


async def test_catalogue_detail_crosses_a_service_keyset_boundary_using_its_offset_source() -> None:
    screen = _screen(initial_id=None)
    queries = cast(AsyncMock, screen._queries)
    queries.list_page.side_effect = (
        Page(
            items=tuple(
                DoorBuild(id=identifier, submission_status=BuildStatus.APPROVED) for identifier in range(27, 12, -1)
            ),
            total=16,
            next=PageAnchor(cursor=PageCursor(BuildListSort.ID, 13, 13)),
            prev=None,
        ),
        Page(
            items=(DoorBuild(id=12, submission_status=BuildStatus.APPROVED),),
            total=16,
            next=None,
            prev=PageAnchor(offset=0),
        ),
    )
    queries.get.side_effect = lambda identifier, *, context: DoorBuild(
        id=identifier, submission_status=BuildStatus.APPROVED
    )
    async with stage(screen, owner=object()) as ui:
        await ui.select("builds.open", ["13"])
        assert "Build #13" in ui.texts()
        await ui.press("builds.item-next")
        assert "Build #12" in ui.texts()
        queries.list_page.assert_awaited_with(
            context=ActingContext.current(),
            statuses=frozenset({BuildStatus.APPROVED, BuildStatus.PENDING}),
            selector=PageSelector(offset=15),
            page_size=15,
        )


async def test_search_help_precedes_the_selected_build() -> None:
    screen = _screen()
    async with stage(screen, owner=object()) as ui:
        await toggle_help(ui)
        assert ui.texts().index("## Search help") < ui.texts().index("Build #7")
        for tab in ("fields", "syntax", "examples"):
            await ui.press(f"search-reference.{tab}")
            assert ui.texts().index("## Search help") < ui.texts().index("Build #7")
            ui.assert_within_limits()
            if tab != "examples":
                assert ui.root.plan is not None
                assert ui.root.plan.allocation is not None
        await toggle_help(ui)
        assert "Build #7" in ui.texts()
        ui.assert_within_limits()


async def test_help_keeps_an_open_builds_schematic_tools() -> None:
    screen = _screen(status=BuildStatus.PENDING, schematics_available=True, video_enabled=True)
    screen._render_build = lambda build: render_build_card(build, MediaPreviewClient())
    async with stage(screen, owner=object()) as ui:
        await ui.press("schematic-tools")
        ui.assert_within_limits()
        await toggle_help(ui)
        ui.control("schematic-info")
        await ui.press("close-help")
        await ui.press("schematic-info")
        ui.control("all-builds")


@pytest.mark.parametrize("records", [False, True])
async def test_help_with_full_catalogue_and_search_pages(records: bool) -> None:
    record_service = AsyncMock(spec=PublicRecordQueryService)
    record_service.list_page.return_value = Page(
        items=tuple(
            PublishedRecordResult(
                result_id=index,
                definition_id=index,
                record_id=uuid4(),
                title=f"Smallest door {index}",
                subtitle=None,
                criterion=RecordCriterion.SMALLEST,
                build_kind=BuildKind.DOOR,
                version_scope=VersionScope.ALL_TIME,
                category_key="door:door|2x3|t[]|Door:r[]:p[]",
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
        items=tuple(DoorBuild(id=index, submission_status=BuildStatus.APPROVED) for index in range(1, 26)),
        total=25,
        next=None,
        prev=None,
    )
    service = cast(AsyncMock, screen._search_service)
    service.search.return_value = SearchPage(
        hits=tuple(
            RecordSearchHit(str(index), f"Fastest door {index}", "Java", index, f"Door {index}", "fastest", "all_time")
            if records
            else BuildSearchHit(str(index), f"Door {index}", "approved", "A detailed door description")
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
            await ui.press(f"search-reference.{tab}")
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

    async def page(
        *, context: ActingContext, statuses: frozenset[BuildStatus], selector: PageSelector, page_size: int
    ) -> Page[Build]:
        assert BuildStatus.APPROVED in statuses
        offset = selector.offset or 0
        return Page(
            items=tuple(
                DoorBuild(id=index + 1, submission_status=BuildStatus.APPROVED)
                for index in range(offset, offset + page_size)
            ),
            total=400,
            next=PageAnchor(offset=offset + page_size) if offset + page_size < 400 else None,
            prev=PageAnchor(offset=max(0, offset - page_size)) if offset else None,
        )

    queries.list_page.side_effect = page
    async with stage(screen, owner=object()) as ui:
        form = await ui.press_for_form("builds.seek.jump")
        await form.submit({"page": "13"})
        assert queries.list_page.await_args.kwargs["selector"] == PageSelector(offset=180)
        assert any("Page 13 of 27" in text for text in ui.texts())
        ui.assert_within_limits()


async def test_schematic_conversion_separates_releases_from_maintenance() -> None:
    async with stage(_screen(schematics_available=True), owner=object()) as ui:
        await ui.press("schematic-tools")
        form = await ui.press_for_form("convert")
        assert {
            getattr(item, "custom_id", None) for item in form.modal.walk_children() if hasattr(item, "custom_id")
        } == {
            "format",
        }
        for key in ("schematic-download", "schematic-render", "schematic-info"):
            ui.control(key)
        ui.control("convert-version")
        maintenance = await ui.press_for_form("conversion-maintenance")
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
        interaction = await ui.press("guided-search")
        assert not interaction.sends
        editor = screen._guided
        assert editor is not None
        service.search.assert_not_awaited()
        for query in ("width:3 AND height:5", "width:7"):
            form = await ui.press_for_form("advanced")
            await form.submit({"query": query})
            assert screen._guided is editor
            assert screen._query.text == query
            assert service.search.call_args.args[0].query == query
            assert service.search.call_args.args[0].page_size == 15
            assert not ui.root.finished
            assert "## Search results" in ui.texts()
        await ui.press("remove-0")
        assert screen._query.text == ""
        assert screen._guided is editor
        ui.assert_within_limits()


async def test_failed_live_search_preserves_query_and_results() -> None:
    screen = _screen(initial_id=None)
    service = cast(AsyncMock, screen._search_service)
    async with stage(screen, owner=object()) as ui:
        await ui.press("guided-search")
        results = screen._results
        service.search.side_effect = RuntimeError("search unavailable")
        form = await ui.press_for_form("advanced")
        await form.submit({"query": "width:3"})
        assert screen._guided is not None
        assert screen._guided.query == screen._query == CatalogueQuery()
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
        hits=tuple(BuildSearchHit(str(index), f"Door {index}", "approved", "A door") for index in range(5)),
        total=5,
        next=None,
        prev=None,
    )
    screen._query = CatalogueQuery.from_text(screen._search_parser, "width:3 AND height:5")
    async with stage(screen, owner=object()) as ui:
        monkeypatch.setattr(
            ui._client,
            "actors",
            SimpleNamespace(resolve=AsyncMock(return_value=ActingContext.current().effective)),
            raising=False,
        )
        monkeypatch.setattr(ui._client, "services", SimpleNamespace(suggestions=suggestions), raising=False)
        await ui.press("guided-search")
        form = await ui.press_for_form("advanced")
        await form.submit({"query": screen._query.text})
        editor = screen._guided
        results = screen._results
        assert editor is not None
        assert editor in screen._find_nodes()
        ui.control("open-id")
        ui.control("guided-search")
        ui.control("advanced")

        await ui.press("field")
        await ui.press("sort")
        with anyio.fail_after(2):
            while not all(
                any(key.endswith(f"{picker}.results") for key in ui.root.snapshot().handler_keys)
                for picker in ("filter-lookup", "sort-lookup")
            ):
                await anyio.sleep(0.01)
        # Both panels, the filter rows, and a full page of results fit within the workspace.
        ui.assert_within_limits()
        ui.control("open-id")
        ui.control("guided-search")
        assert "## Search results" in ui.texts()
        assert screen._results is results
        for key in ("edit-0", "remove-0", "edit-1", "remove-1"):
            ui.control(key)
        await ui.select("filter-lookup.results", ["width"])
        sort_picker = editor.sort_picker
        for value in ("7", "8", "9"):
            form = await ui.press_for_form("value")
            await form.submit({"value": value, "operator": "2"})
            assert screen._guided is editor
            assert editor.filter_open
            assert editor.selected is not None
            assert editor.selected.name == "width"
            assert editor.sort_picker is sort_picker
            assert screen._query == editor.query
            assert screen._query.text.endswith(f"width<={value}")
            assert "## Search results" in ui.texts()
            ui.assert_within_limits()
        ui.control("edit-4")
        results = screen._results
        query = screen._query
        cast(AsyncMock, screen._search_service).search.side_effect = RuntimeError("search unavailable")
        form = await ui.press_for_form("value")
        await form.submit({"value": "10", "operator": "2"})
        assert screen._query == editor.query == query
        assert screen._results is results
        assert editor.filter_open
        assert editor.selected is not None
        assert editor.selected.name == "width"
        ui.control("edit-4")
        await ui.press("field")
        assert not editor.filter_open
        assert screen._guided is editor
        assert screen._results is results
        await ui.press("guided-search")
        assert screen._guided is None
        assert screen._results is results
        ui.assert_within_limits()


async def test_records_default_to_browse_and_highlight_active_controls() -> None:
    records = AsyncMock(spec=PublicRecordQueryService)
    records.list_page.return_value = Page(items=(), total=0, next=None, prev=None)
    screen = _screen(initial_id=None, records=records, initial_records=True)
    async with stage(screen, owner=object()) as ui:
        assert "## Records" in ui.texts()
        assert "## Find a record" not in ui.texts()
        assert ui.control("all-criteria").payload["style"] == 3
        assert ui.control("guided-search").payload["style"] == 2
        ui.control("open-id")
        await ui.press("guided-search")
        assert ui.control("guided-search").payload["style"] == 3
        ui.control("open-id")
        assert not any(key.endswith("cancel") for key in ui.root.snapshot().handler_keys)
        await toggle_help(ui)
        assert ui.control("search-help").payload["style"] == 3
        assert ui.texts().index("## Search help") < ui.texts().index("## Records")
        form = await ui.press_for_form("advanced")
        await form.submit({"query": "kind:door"})
        assert "## Search help" not in ui.texts()
        # A typed query is not a type, so All types stays highlighted until a type is chosen.
        assert ui.control("all-criteria").payload["style"] == 3
        await ui.press("smallest")
        assert ui.control("all-criteria").payload["style"] == 2
        await ui.press("reset")
        assert ui.control("all-criteria").payload["style"] == 3
        assert screen._guided is not None
        assert screen._guided.query.text == ""
        assert "## Records" in ui.texts()


async def test_collapsed_query_and_sort_share_summary_with_reset() -> None:
    screen = _screen(initial_id=None)
    async with stage(screen, owner=object()) as ui:
        await submit_search(ui, "width:3", "-height")
        await ui.press("guided-search")
        summaries = [text for text in ui.texts() if "width:3" in text]
        assert summaries == ["`width:3` · Height, largest first"]
        assert screen._guided is None
        assert not any(key.endswith("reset") for key in ui.root.snapshot().handler_keys)
        await ui.press("guided-search")
        ui.control("reset")
        await ui.press("reset")
        assert screen._query == CatalogueQuery()
        assert screen._sort == ""
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
            else BuildSearchHit(str(index), f"Door {index}", "approved", "A detailed door description")
            for index in range(25)
        ),
        total=25,
        next=None,
        prev=None,
    )
    async with stage(screen, owner=object()) as ui:
        await ui.press("guided-search")
        form = await ui.press_for_form("advanced")
        await form.submit({"query": "kind:door AND title:door" if records else "width:3 AND height:5"})
        await toggle_help(ui)
        for tab in ("fields", "syntax", "examples"):
            await ui.press(f"search-reference.{tab}")
            assert ui.texts().index("## Search help") < ui.texts().index("## Search results")
            ui.control("advanced")
            ui.control("reset")
            ui.assert_within_limits()
        await ui.press("close-help")
        assert "## Search results" in ui.texts()


async def test_editor_expansion_survives_switching_catalogue_tabs() -> None:
    records = AsyncMock(spec=PublicRecordQueryService)
    records.list_page.return_value = Page(items=(), total=0, next=None, prev=None)
    screen = _screen(initial_id=None, records=records)
    async with stage(screen, owner=object()) as ui:
        await ui.press("guided-search")
        for tab, scope in (("all-records", "records"), ("all-builds", "builds")):
            await ui.press(tab)
            assert screen._guided is not None
            assert screen._guided.scope == scope
            ui.control("advanced")
            assert ui.control("guided-search").payload["label"] == "Done"
            assert ui.control("guided-search").payload["style"] == 3
        await ui.press("guided-search")
        await ui.press("all-records")
        assert screen._guided is None
        assert not any(key.endswith("advanced") for key in ui.root.snapshot().handler_keys)


async def test_catalogue_tabs_share_filters_and_restore_local_queries_and_sort() -> None:
    records = AsyncMock(spec=PublicRecordQueryService)
    records.list_page.return_value = Page(items=(), total=0, next=None, prev=None)
    screen = _screen(initial_id=None, records=records)
    service = cast(AsyncMock, screen._search_service)
    async with stage(screen, owner=object()) as ui:
        shared = '  (title:"vault door" or piston)   KIND:door  '
        await submit_search(ui, shared)
        await ui.press("all-records")
        assert screen._query.text == shared
        assert service.search.call_args.args[0].scope is SearchScope.RECORDS
        assert service.search.call_args.args[0].query == shared

        record_query = shared + "criterion:smallest version_scope:all_time"
        await submit_search(ui, record_query, "-updated_at")
        await ui.press("all-builds")
        assert screen._query.text == shared
        assert screen._sort == ""

        build_query = 'kind:door (title:"vault door" OR piston) width<=5 status:approved'
        await submit_search(ui, build_query, "-width")
        await ui.press("all-records")
        assert (
            screen._query.text
            == 'kind:door AND (title:"vault door" OR piston) AND criterion:smallest AND version_scope:all_time'
        )
        assert screen._sort == "-updated_at"
        assert ui.control("smallest").payload["style"] == 3

        await submit_search(ui, "kind:door criterion:fastest version_scope:all_time", "-updated_at")
        await ui.press("all-builds")
        request = service.search.call_args.args[0]
        assert request.scope is SearchScope.BUILDS
        assert request.query == "kind:door AND width<=5 AND status:approved"
        assert screen._sort == "-width"

        await ui.press("reset")
        await ui.press("all-records")
        assert screen._query.text == "criterion:fastest AND version_scope:all_time"
        assert screen._sort == "-updated_at"
        assert ui.control("fastest").payload["style"] == 3
        ui.assert_within_limits()


async def test_catalogue_keeps_mixed_boolean_groups_local_across_record_tools() -> None:
    records = AsyncMock(spec=PublicRecordQueryService)
    inspection = AsyncMock(spec=RecordService)
    records.list_page.return_value = Page(items=(), total=0, next=None, prev=None)
    inspection.gaps.return_value = ()
    tools = RecordsScreen(inspection, AsyncMock(), grants=grants(RECORD_ENTRY_INSPECT))
    screen = _screen(initial_id=None, records=records, record_tools=tools)
    async with stage(screen, owner=object()) as ui:
        build_query = "  NOT (title:door AND width:5)  "
        await submit_search(ui, build_query)
        await ui.press("record-tools")
        await ui.press("all-records")
        assert screen._query == CatalogueQuery(RecordQuery(None, ""))
        record_query = "title:door OR criterion:fastest"
        await submit_search(ui, record_query)

        await ui.press("record-tools")
        form = await ui.press_for_form("open-id")
        await form.submit({"id": "7"})
        assert screen._query.text == build_query
        assert "Build #7" in ui.texts()
        await ui.press("linked-build.back")
        assert "## Search results" in ui.texts()

        await ui.press("all-records")
        assert screen._query.text == record_query
        assert screen._guided is not None
        assert screen._guided.query == screen._query
        ui.assert_within_limits()


async def test_shared_keywords_keep_their_meaning_when_entering_records() -> None:
    records = AsyncMock(spec=PublicRecordQueryService)
    records.list_page.return_value = Page(items=(), total=0, next=None, prev=None)
    screen = _screen(initial_id=None, records=records)
    async with stage(screen, owner=object()) as ui:
        typed = "  FASTEST   "
        await submit_search(ui, typed)
        assert screen._query.parsed.expression == TextExpression("FASTEST")
        await ui.press("all-records")
        assert isinstance(screen._query.parsed, RecordQuery)
        assert screen._query.text == "(FASTEST)"
        assert screen._query.parsed.expression == TextExpression("FASTEST")
        await ui.press("all-builds")
        assert screen._query.text == typed


async def test_expanded_reset_is_disabled_until_query_sort_or_categories_change() -> None:
    screen = _screen(initial_id=None)
    async with stage(screen, owner=object()) as ui:
        await ui.press("guided-search")
        assert ui.control("reset").payload["disabled"]
        form = await ui.press_for_form("advanced")
        await form.submit({"query": "width:3"})
        assert not ui.control("reset").payload.get("disabled", False)
        await ui.press("reset")
        assert ui.control("reset").payload["disabled"]
        await submit_search(ui, "", "-width")
        assert not ui.control("reset").payload.get("disabled", False)
        await ui.press("reset")
        assert ui.control("reset").payload["disabled"]


async def test_open_id_is_available_beside_record_tools() -> None:
    records = AsyncMock(spec=PublicRecordQueryService)
    inspection = AsyncMock(spec=RecordService)
    inspection.gaps.return_value = ()
    inspection.title_gaps.return_value = ()
    tools = RecordsScreen(inspection, AsyncMock(), grants=grants(RECORD_ENTRY_INSPECT))
    screen = _screen(initial_id=None, records=records, record_tools=tools)
    async with stage(screen, owner=object()) as ui:
        await ui.press("record-tools")
        form = await ui.press_for_form("open-id")
        await form.submit({"id": "7"})
        assert screen._mode == "builds"
        assert "Build #7" in ui.texts()
        ui.control("open-id")


async def test_examples_keep_help_and_editor_in_sync() -> None:
    screen = _screen(initial_id=None)
    async with stage(screen, owner=object()) as ui:
        await toggle_help(ui)
        editor = screen._guided
        await ui.press("search-reference.examples")
        for index in (0, 1):
            if index:
                await toggle_help(ui)
                await ui.press("search-reference.examples")
            await ui.press(f"example-{index}")
            assert not screen._show_search_help
            assert screen._guided is editor
            assert editor is not None
            assert editor.query == screen._query
            assert "## Search results" in ui.texts()
            ui.control("advanced")
            assert not ui.control("reset").payload.get("disabled", False)


async def test_expanded_search_filters_and_help_survive_expanding_a_build() -> None:
    screen = _screen(initial_id=None, schematics_available=True, video_enabled=True)
    screen._render_build = lambda build: render_build_card(build, MediaPreviewClient())
    build = DoorBuild(
        id=7,
        submission_status=BuildStatus.APPROVED,
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
    hit = BuildSearchHit("7", "Door 7", "approved", "A detailed door description")
    cast(AsyncMock, screen._search_service).search.return_value = SearchPage(hits=(hit,), total=1, next=None, prev=None)
    async with stage(screen, owner=object()) as ui:
        await toggle_help(ui)
        form = await ui.press_for_form("advanced")
        await form.submit({"query": "width:3 AND height:5 AND depth:2"})
        await ui.select("search.open", ["build:7"])
        await ui.press("schematic-tools")
        ui.control("schematic-info")
        ui.control("search.back")
        ui.assert_within_limits()
        await ui.press(
            next(key for key in ui.root.snapshot().handler_keys if "__cursor_next." in key and key.endswith("clauses"))
        )
        ui.control("edit-2")
        await ui.press(
            next(
                key
                for key in ui.root.snapshot().handler_keys
                if "__cursor_previous." in key and key.endswith("clauses")
            )
        )
        await toggle_help(ui)
        for tab in ("fields", "syntax", "examples"):
            await ui.press(f"search-reference.{tab}")
            ui.control("advanced")
            ui.assert_within_limits()
        await ui.press("close-help")
        ui.control("schematic-info")
        await ui.press("search.back")
        ui.control("search.open")
        assert screen._guided is not None
        assert screen._query.text == "width:3 AND height:5 AND depth:2"


async def test_records_start_unfiltered_and_reset_clears_a_field_filter() -> None:
    records = AsyncMock(spec=PublicRecordQueryService)
    records.list_page.return_value = Page(items=(), total=0, next=None, prev=None)
    screen = _screen(initial_id=None, records=records, initial_records=True)
    service = cast(AsyncMock, screen._search_service)
    async with stage(screen, owner=object()) as ui:
        assert screen._query.text == ""
        await ui.press("guided-search")
        assert screen._guided is not None
        assert screen._guided.query.text == ""
        form = await ui.press_for_form("advanced")
        await form.submit({"query": "version_scope:up_to_date"})
        await ui.press("remove-0")
        assert screen._query.text == ""
        assert service.search.call_args.args[0].query == ""
        assert screen._results is not None
        await ui.press("all-criteria")
        assert screen._results is None
        form = await ui.press_for_form("advanced")
        await form.submit({"query": "version_scope:up_to_date"})
        await ui.press("reset")
        assert screen._query == screen._guided.query == CatalogueQuery(RecordQuery(None, ""))
        assert screen._results is None


async def test_catalogue_reopens_with_field_filters_and_sort() -> None:
    accounts = AsyncMock(spec=AccountService)
    screen = _screen(initial_id=None)
    screen._accounts = accounts
    async with stage(screen, owner=object()) as ui:
        await submit_search(ui, "piston (width<=5 OR width>=10)", "-width")
    args = dict(accounts.save_catalogue_preference.await_args.kwargs)
    preference = CataloguePreference(account_id=args.pop("context").effective.require_account_id(), **args)
    assert preference.account_id == 1
    assert preference.scope == "builds"
    assert "piston" not in preference.filters
    accounts.catalogue_preferences.return_value = [
        preference,
        CataloguePreference(account_id=1, scope="records", filters="criterion:fastest", sort=""),
    ]
    reopened = _screen(initial_id=None, records=AsyncMock(spec=PublicRecordQueryService))
    reopened._accounts = accounts
    await reopened._restore_preferences()
    assert reopened._sort == "-width"
    assert reopened._query.parsed == reopened._search_parser.parse("width<=5 OR width>=10")
    assert reopened._results is not None
    await reopened._switch("records", press_event())
    assert reopened._query.text == "criterion:fastest"
    await reopened._reset_search(press_event())
    reset = accounts.save_catalogue_preference.await_args.kwargs
    assert (reset["scope"], reset["filters"], reset["sort"]) == ("records", "", "")
