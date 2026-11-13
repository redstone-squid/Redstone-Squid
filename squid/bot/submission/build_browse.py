"""Canonical build catalogue, review, and schematic workspace."""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from functools import partial
from typing import Any, Literal, cast, override

import squid_ui as sl
import squid_ui_discord as sd
import squid_ui_widgets as sp
from squid.bot.app import RedstoneSquid
from squid.bot.consent import with_consented_account
from squid.bot.edit_controller import BoundEdit, EditController
from squid.bot.submission.record_browser import record_browser
from squid.bot.submission.records import RecordsScreen
from squid.bot.submission.schematic_tools import SchematicTools
from squid.bot.submission.search_help import search_help
from squid.bot.submission.search_view import SearchScreen
from squid.bot.submission.ui import controls as build_controls
from squid.bot.ui import tr
from squid.builds.application import BuildQueryService, BuildService
from squid.builds.application.edits import BUILD_STATUS_EDIT, build_target
from squid.builds.domain import Status
from squid.builds.models import Build
from squid.core.actors import Actor
from squid.core.pagination import PageSelector
from squid.edits.application import EditRunner
from squid.edits.domain import EditValues
from squid.permissions.domain import PermissionNode
from squid.permissions.domain.catalogue import (
    BUILD_SUBMISSION_APPROVE,
    BUILD_SUBMISSION_DEBUG,
    BUILD_SUBMISSION_REJECT,
)
from squid.records.application import RecordService
from squid.schematics.application import SchematicService
from squid.search.application import SearchService
from squid.search.domain import SearchRequest, SearchScope, SearchSort
from squid_i18n import md_tr
from squid_reactivity import TopicBus, TopicHandle
from squid_ui.sources import window_fingerprint

type BuildRenderer = Callable[[Build], Awaitable[sl.LayoutNode[sl.ComponentsV2Target, sd.Request]]]
type BuildAuthorizer = Callable[[PermissionNode], Awaitable[bool]]
type RefreshPosts = Callable[[int], Awaitable[None]]


def _debug_dump(build: Build) -> str:
    """Serialize a build's internal state as readable JSON without its embedding."""
    state: dict[str, Any] = {key: value for key, value in build.edit_values().items() if key != "embedding"}
    state["category"] = build.category
    state["embedding_dimensions"] = len(build.embedding) if build.embedding is not None else None
    return json.dumps(_jsonable(state), indent=2, sort_keys=True, default=str)


def _jsonable(value: Any) -> Any:
    """Render enums by name, since an IntEnum otherwise serializes as the integer."""
    match value:
        case Enum():
            return value.name
        case dict():
            return {str(key): _jsonable(item) for key, item in value.items()}
        case list() | tuple():
            return [_jsonable(item) for item in value]
        case _:
            return value


@dataclass(frozen=True, slots=True)
class BuildCapabilities:
    """Initial visibility choices for build details and actions, not authorization to execute them."""

    view_pending: bool
    approve: bool
    reject: bool
    debug: bool
    edit: bool


class _BuildSource:
    capabilities = sl.sources.SourceCapabilities(
        backward=True,
        offsets=True,
        jumpable=True,
        count=sl.sources.CountPrecision.EXACT,
    )

    def __init__(self, queries: BuildQueryService, statuses: frozenset[Status]) -> None:
        self._queries = queries
        self._statuses = statuses

    async def fetch(self, position: sl.sources.Position, extent: int) -> sl.sources.Window[Build]:
        page = await self._queries.list_page(
            statuses=self._statuses,
            selector=PageSelector(offset=position.offset),
            page_size=extent,
        )
        return sl.sources.Window(
            sl.sources.Position(str(page.items[0].id) if page.items else None, position.offset),
            page.items,
            previous=sl.sources.Position(offset=page.prev.offset)
            if page.prev is not None and page.prev.offset is not None
            else None,
            next=sl.sources.Position(offset=page.next.offset)
            if page.next is not None and page.next.offset is not None
            else None,
            total=page.total,
        )

    def fingerprint(self, window: sl.sources.Window[Build]) -> str:
        return window_fingerprint(window.items, lambda build: str(build.id))


class _NeighbourBuildSource:
    """An ID-addressed build context for direct links and related-build screens."""

    capabilities = sl.sources.SourceCapabilities(backward=True)

    def __init__(self, queries: BuildQueryService, statuses: frozenset[Status]) -> None:
        self.queries = queries
        self.statuses = statuses

    def initial(self, build: Build) -> sl.sources.LoadedWindow[Build]:
        key = str(build.id)
        window = sl.sources.Window(
            sl.sources.Position(key),
            (build,),
            previous=sl.sources.Position(key, direction=sl.sources.Direction.BACKWARD),
            next=sl.sources.Position(key, direction=sl.sources.Direction.FORWARD),
        )
        return sl.sources.LoadedWindow(window, window_fingerprint(window.items, lambda item: str(item.id)))

    async def fetch(self, position: sl.sources.Position, extent: int) -> sl.sources.Window[Build]:
        assert position.anchor is not None
        build_id = int(position.anchor)
        if position.direction is sl.sources.Direction.AROUND:
            build = await self.queries.get(build_id)
            if build is None or build.submission_status not in self.statuses:
                return sl.sources.Window(sl.sources.Position(), ())
            return self.initial(build).window
        selector = (
            PageSelector(before_id=build_id)
            if position.direction is sl.sources.Direction.BACKWARD
            else PageSelector(after_id=build_id)
        )
        page = await self.queries.list_page(statuses=self.statuses, selector=selector, page_size=extent)
        return sl.sources.Window(
            sl.sources.Position(str(page.items[0].id) if page.items else None),
            page.items,
            previous=sl.sources.Position(str(page.prev.before_id), direction=sl.sources.Direction.BACKWARD)
            if page.prev is not None and page.prev.before_id is not None
            else None,
            next=sl.sources.Position(str(page.next.after_id), direction=sl.sources.Direction.FORWARD)
            if page.next is not None and page.next.after_id is not None
            else None,
        )


class _BuildDetail(sl.Component[sl.ComponentsV2Target, sd.Request]):
    """Show one build and refresh its card when the build topic changes.

    Capabilities control which actions appear. Review, debug, and timing
    actions recheck permission through authorize; the routed editor checks its own access.
    """

    _decision: sp.ComponentDriver[sp.DecisionState, sl.ComponentsV2Target, sd.Request] | None = sl.state(
        None, opaque=True
    )
    _review: BoundEdit | None = sl.state(None, opaque=True)
    _pending_review: str | None = sl.state(None, persist=False)
    _asset: sl.document.Asset | None = sl.state(None, opaque=True)
    _show_schematics: bool = sl.state(default=False)

    def __init__(
        self,
        build: Build,
        *,
        changes: TopicHandle,
        queries: BuildQueryService,
        builds: BuildService,
        edits: EditRunner,
        actor: Callable[[], Awaitable[Actor]],
        schematics: SchematicService,
        render_build: BuildRenderer,
        capabilities: BuildCapabilities,
        actor_account_id: int | None,
        authorize: BuildAuthorizer,
        refresh_posts: RefreshPosts,
    ) -> None:
        assert build.id is not None
        self._changes = changes
        self._build_id = build.id
        self._seed: Build | None = build
        self.controller = EditController(edits, actor, self._refresh_review)
        self._queries = queries
        self._builds = builds
        self._edits = edits
        self._actor = actor
        self.schematics = SchematicTools(
            build.id,
            schematics=schematics,
            actor_account_id=actor_account_id,
            authorize=authorize,
            show_details=lambda: self._show_schematics,
        )
        self._render_build = render_build
        self._capabilities = capabilities
        self._actor_account_id = actor_account_id
        self._authorize = authorize
        self._refresh_posts = refresh_posts

    @sl.resource(pending=sl.resources.PendingMode.ATOMIC)
    async def projection(self) -> tuple[Build, sl.LayoutNode[sl.ComponentsV2Target, sd.Request]]:
        self._changes.watch()
        build, self._seed = self._seed, None
        if build is None:
            build = await self._queries.get(self._build_id)
        if build is None:
            message = f"build {self._build_id} no longer exists"
            raise LookupError(message)
        return build, await self._render_build(build)

    @override
    def render(self) -> sl.DocumentLike[sl.ComponentsV2Target, sd.Request]:
        state = self.projection.status
        if not isinstance(state, sl.resources.Ready) and state.previous is None:
            return (sl.status(tr(t"bot-submission-build-browse-loading-build = Loading build.")),)
        if isinstance(state, sl.resources.Ready):
            build, node = state.value
        else:
            assert state.previous is not None
            build, node = state.previous.value
        if self._decision is not None:
            return (node, self._review_prompt(), self._decision)
        nodes: list[sl.LayoutNode[sl.ComponentsV2Target, sd.Request]] = [node]
        nodes.append(self._detail_actions(build))
        nodes.extend(self.controller.controls())
        nodes.append(self.schematics)
        if self._asset is not None:
            nodes.append(
                sl.download(
                    self._asset,
                    label=tr(t"bot-submission-build-browse-download-result = Download result"),
                    key="build-download",
                )
            )
        return nodes

    async def _toggle_schematics(self, _event: sl.PressEvent) -> None:
        self._show_schematics = not self._show_schematics

    def _detail_actions(self, build: Build) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request]:
        actions: list[sl.semantic.ActionControl | sl.semantic.RoutedActionControl] = []
        if self._capabilities.edit or (
            build.submission_status is Status.PENDING
            and self._actor_account_id is not None
            and build.submitter_account_id == self._actor_account_id
        ):
            actions.append(
                sl.routed_action_control(
                    tr(t"bot-submission-build-browse-edit = Edit"),
                    build_controls.edit_build.id(build_id=self._build_id),
                    key="edit",
                )
            )
        if build.submission_status is Status.PENDING and self._capabilities.approve:
            actions.append(
                sl.action_control(
                    tr(t"bot-submission-build-browse-approve = Approve"),
                    partial(self._request_approve, build.revision),
                    key="approve",
                    tone=sl.Tone.SUCCESS,
                )
            )
        if build.submission_status is Status.PENDING and self._capabilities.reject:
            actions.append(
                sl.action_control(
                    tr(t"bot-submission-build-browse-reject = Reject"),
                    partial(self._request_reject, build.revision),
                    key="reject",
                    tone=sl.Tone.DANGER,
                )
            )
        if self._capabilities.debug:
            actions.append(
                sl.action_control(
                    tr(t"bot-submission-build-browse-debug-download = Debug download"), self._debug, key="debug"
                )
            )
        actions.append(
            sl.action_control(
                tr(t"build-schematic-tools-hide = Hide schematic tools")
                if self._show_schematics
                else tr(t"build-schematic-tools-show = Schematic tools"),
                self._toggle_schematics,
                key="schematic-tools",
            )
        )
        return sl.action_controls(*actions, key="build-actions", display=sl.semantic.ControlDisplay.INDIVIDUAL)

    def _review_prompt(self) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request]:
        action = self._pending_review or "review"
        build_id = self._build_id
        return sl.section(
            sl.heading(tr(t"build-review-confirmation-title = Confirm review decision")),
            sl.paragraph(
                md_tr(
                    t"bot-submission-build-browse-build = {action} build #{build_id!s}?",
                )
            ),
        )

    async def _request_approve(self, revision: int, _event: sl.PressEvent) -> None:
        self._request_review("approve", revision)

    async def _request_reject(self, revision: int, _event: sl.PressEvent) -> None:
        self._request_review("reject", revision)

    def _request_review(self, action: str, revision: int) -> None:
        self._review = self.controller.bind(BUILD_STATUS_EDIT, build_target(self._build_id), revision)
        self._pending_review = action
        self._decision = sp.Decision[sl.ComponentsV2Target, sd.Request](
            tr(t"build-review-state-warning = This changes the build's review state."),
            (
                sp.DecisionOption("confirm", tr(t"bot-submission-build-browse-confirm = Confirm"), sl.Tone.DANGER),
                sp.DecisionOption("cancel", tr(t"bot-submission-build-browse-cancel = Cancel")),
            ),
            key="build-review",
        ).build_component(on_decide=self._decide_review)

    async def _decide_review(self, event: sp.TransitionEvent[sp.DecisionState], choice: str) -> None:
        action = self._pending_review
        review = self._review
        self._pending_review = None
        self._decision = None
        if choice == "cancel" or action is None:
            return
        node = BUILD_SUBMISSION_APPROVE if action == "approve" else BUILD_SUBMISSION_REJECT
        if not await self._may(event.source, node):
            return

        async def apply_review(source: sd.ActionEvent, _account_id: int) -> None:
            if not await self._may(source, node):
                return
            assert review is not None
            await review.save(
                EditValues.of(
                    {"submission_status": (Status.CONFIRMED if action == "approve" else Status.DENIED).value}
                ),
                tr(t"build-review-state-updated = Build review state updated."),
            )
            await source.notice(tr(t"build-review-state-updated = Build review state updated."))

        source = cast(sd.ActionEvent, event.source)
        if self._actor_account_id is not None:
            await apply_review(source, self._actor_account_id)
        else:
            await with_consented_account(
                source, cast(RedstoneSquid, source.context.client).services.accounts, apply_review
            )

    async def _refresh_review(self) -> None:
        self.projection.invalidate()
        await self._refresh_posts(self._build_id)

    async def _debug(self, event: sl.PressEvent) -> None:
        if not await self._may(event, BUILD_SUBMISSION_DEBUG):
            return
        build = await self._queries.get(self._build_id)
        if build is None:
            await event.notice(tr(t"build-no-longer-available = That build is no longer available."))
            return
        data = _debug_dump(build).encode()
        self._asset = sl.file(data, filename=f"build-{self._build_id}-debug.json")

    async def _may(self, event: sl.ActionEvent, node: PermissionNode) -> bool:
        if await self._authorize(node):
            return True
        await event.notice(
            tr(t"build-operation-permission-denied = You are no longer allowed to perform this build operation.")
        )
        return False


class BuildBrowseScreen(sd.Screen):
    """A live build workspace that ends when replaced or timed out."""

    policy = sd.ResponseSpec(timeout=300, live_updates=True, session=sd.SessionSpec("build-browse"))

    _results: SearchScreen | None = sl.state(None, opaque=True)
    _record_categories: frozenset[str] = sl.state(frozenset())
    _show_search_help: bool = sl.state(default=False)
    _show_standings: bool = sl.state(default=False)
    _query: str = sl.state("")
    _sort: str = sl.state("")
    _mode: Literal["builds", "records", "tools"] = sl.state("builds")
    _selected: sp.Browser[Build, sl.ComponentsV2Target, sd.Request] | None = sl.state(None, opaque=True)

    def __init__(
        self,
        queries: BuildQueryService,
        builds: BuildService,
        edits: EditRunner,
        actor: Callable[[], Awaitable[Actor]],
        schematics: SchematicService,
        *,
        topics: TopicBus,
        initial_id: int | None,
        render_build: BuildRenderer,
        capabilities: BuildCapabilities,
        actor_account_id: int | None,
        authorize: BuildAuthorizer,
        refresh_posts: RefreshPosts,
        records: RecordService | None = None,
        record_tools: RecordsScreen | None = None,
        initial_records: bool = False,
        search: SearchService | None = None,
    ) -> None:
        self._build_search_help = search_help(records=False, apply_example=self._apply_search_example)
        self._record_search_help = search_help(records=True, apply_example=self._apply_search_example)
        self._search_service = search
        self._mode = "records" if initial_records and records is not None else "builds"
        self._records = record_browser(records, self._open_holder) if records is not None else None
        self._record_tools = record_tools
        self._topics = topics
        self._queries = queries
        self._builds = builds
        self._edits = edits
        self._actor = actor
        self._schematics = schematics
        self._initial_id = initial_id
        self._render_build = render_build
        self._capabilities = capabilities
        self._actor_account_id = actor_account_id
        self._authorize = authorize
        self._refresh_posts = refresh_posts
        statuses = {Status.CONFIRMED}
        if capabilities.view_pending:
            statuses.add(Status.PENDING)
        self._statuses = frozenset(statuses)
        self._browser = sp.Browser(
            _BuildSource(queries, frozenset(statuses)),
            key="builds",
            identity=lambda build: str(build.id),
            label=lambda build: f"#{build.id} {build.title}",
            # `_BuildSource` filters on `submission_status`, so every listed build carries one; the
            # domain type stays optional only for a build that has not been submitted yet.
            summary=_build_summary,
            detail=self._detail,
            page_size=8,
            title=tr(t"bot-submission-build-browse-build-catalogue = Build catalogue"),
            empty=tr(t"build-list-empty = No builds are available."),
            # `_BuildSource` uses the default listing order: highest, so newest, build ID first.
            chronology=sl.sources.Chronology.NEWEST_FIRST,
        )

    @override
    async def on_load(self) -> None:
        if self._initial_id is not None:
            await self._select(self._initial_id)

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        if self._selected is not None:
            return (
                self._workspace_controls(),
                *self._find_nodes(),
                sl.action_controls(
                    sl.action_control(tr(t"build-browse-list = List view"), self._back, key="back"),
                    key="selected-actions",
                    display=sl.semantic.ControlDisplay.INDIVIDUAL,
                ),
                self._selected,
            )
        if self._mode == "tools" and self._record_tools is not None:
            return (self._workspace_controls(), self._record_tools)
        if self._results is not None:
            return (self._workspace_controls(), *self._find_nodes(), sl.note(self._query), self._results)
        if self._mode == "records" and not self._show_standings:
            return (
                self._workspace_controls(),
                sl.heading(tr(t"record-browser-home = Find a record")),
                sl.paragraph(tr(t"record-browser-guide = Choose a record type, or search by name and filters.")),
                *self._find_nodes(),
            )
        listing = self._records if self._mode == "records" and self._records is not None else self._browser
        return (self._workspace_controls(), *self._find_nodes(), listing)

    def _workspace_controls(self) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request]:
        return sl.action_controls(
            sl.action_control(
                tr(t"build-browse-all-builds = Builds"),
                partial(self._switch, "builds"),
                key="all-builds",
                emphasis=sl.semantic.Emphasis.STRONG if self._mode == "builds" else sl.semantic.Emphasis.NORMAL,
                available=self._mode != "builds",
            ),
            self._records is not None
            and sl.action_control(
                tr(t"build-browse-records = Records"),
                partial(self._switch, "records"),
                key="all-records",
                emphasis=sl.semantic.Emphasis.STRONG if self._mode == "records" else sl.semantic.Emphasis.NORMAL,
                available=self._mode != "records",
            ),
            self._record_tools is not None
            and sl.action_control(
                tr(t"build-browse-record-tools = Record tools"),
                partial(self._switch, "tools"),
                key="record-tools",
                emphasis=sl.semantic.Emphasis.STRONG if self._mode == "tools" else sl.semantic.Emphasis.NORMAL,
                available=self._mode != "tools",
            ),
            key="catalogue-views",
            display=sl.semantic.ControlDisplay.INDIVIDUAL,
        )

    async def _switch(self, mode: Literal["builds", "records", "tools"], _event: sl.PressEvent) -> None:
        if mode == self._mode:
            return
        self._results = None
        self._query = ""
        self._sort = ""
        self._show_standings = False
        self._record_categories = frozenset()
        self._mode = mode
        self._selected = None
        await self._browser._back(_event)

    async def _open_holder(self, build_id: int, event: sl.PressEvent) -> None:
        if not await self._select(build_id):
            await event.notice(tr(t"build-id-not-visible = No visible build has that ID."))

    def _find_nodes(self) -> list[sl.LayoutNode[sl.ComponentsV2Target, sd.Request]]:
        nodes: list[sl.LayoutNode[sl.ComponentsV2Target, sd.Request]] = [
            sl.action_controls(
                sl.form(
                    tr(t"bot-submission-build-browse-open-by-id = Open by ID"),
                    _OpenBuildForm().spec(),
                    key="open-id",
                    on_submit=self._open_id,
                ),
                sl.form(
                    tr(t"build-browser-search-filters = Search & filters"),
                    _SearchForm(query=self._query, sort=self._sort).spec(),
                    key="catalogue-search",
                    on_submit=self._search,
                ),
                sl.action_control(
                    tr(t"catalogue-hide-search-help = Hide search help")
                    if self._show_search_help
                    else tr(t"catalogue-search-help = Search help"),
                    self._toggle_search_help,
                    key="search-help",
                ),
                key="find-forms",
            ),
        ]
        if self._mode == "records":
            nodes.extend(
                (
                    sl.action_controls(
                        *(
                            sl.toggle(
                                label,
                                key=category,
                                on=sl.controlled(
                                    category in self._record_categories, partial(self._toggle_record_category, category)
                                ),
                                tone=sl.Tone.SUCCESS if category in self._record_categories else sl.Tone.NEUTRAL,
                            )
                            for category, label in (
                                ("smallest", tr(t"record-browser-smallest = Smallest")),
                                ("fastest", tr(t"record-browser-fastest = Fastest")),
                                ("first", tr(t"record-browser-first = First")),
                            )
                        ),
                        sl.action_control(
                            tr(t"record-browser-all = All standings"),
                            self._all_standings,
                            key="all-standings",
                        ),
                        key="record-categories",
                        display=sl.semantic.ControlDisplay.INDIVIDUAL,
                    ),
                    sl.note(
                        tr(
                            t"record-filter-hint = Toggle any record types; none selected means all types. "
                            t"Search keeps your selection."
                        )
                    ),
                )
            )
        else:
            nodes.append(
                sl.note(
                    tr(
                        t"build-filter-hint = Search by name, or add filters such as creator:Steve or width:5. "
                        t"See Search help for examples."
                    )
                )
            )
        if self._show_search_help:
            nodes.append(self._record_search_help if self._mode == "records" else self._build_search_help)
        return nodes

    async def _toggle_search_help(self, _event: sl.PressEvent) -> None:
        self._show_search_help = not self._show_search_help

    async def _apply_search_example(self, query: str, _event: sl.PressEvent) -> None:
        self._record_categories = frozenset()
        await self._run_search(query, "")
        self._show_search_help = False

    def _detail(self, build: Build) -> _BuildDetail:
        return _BuildDetail(
            build,
            changes=self._topics.topic("build", str(build.id)),
            queries=self._queries,
            builds=self._builds,
            edits=self._edits,
            actor=self._actor,
            schematics=self._schematics,
            render_build=self._render_build,
            capabilities=self._capabilities,
            actor_account_id=self._actor_account_id,
            authorize=self._authorize,
            refresh_posts=self._refresh_posts,
        )

    async def _select(self, build_id: int) -> bool:
        build = await self._queries.get(build_id)
        if build is None or (build.submission_status is not Status.CONFIRMED and not self._capabilities.view_pending):
            return False
        await self._show_build(build)
        return True

    async def _show_build(self, build: Build) -> None:
        source = _NeighbourBuildSource(self._queries, self._statuses)
        session = sp.BrowseSession(
            source, identity=lambda item: str(item.id), page_size=1, initial=source.initial(build)
        )
        session.open(str(build.id), revision=session.revision)
        browser = sp.Browser(
            session,
            key="linked-build",
            identity=lambda item: str(item.id),
            label=lambda item: f"#{item.id} {item.title}",
            detail=self._detail,
            chronology=sl.sources.Chronology.NEWEST_FIRST,
        )
        # Prepare the detail before a modal switches views. The parent keeps its own
        # collection and focus while this explicitly opened child browses by build ID.
        await browser._detail._load()
        detail = cast(_BuildDetail, browser._detail.value)
        await detail.projection._load()
        self._selected = browser

    async def _open_id(self, event: sl.SubmitEvent) -> None:
        form = _OpenBuildForm.from_submission(event)
        build_id = form.id
        if not await self._select(build_id):
            await event.notice(tr(t"build-id-not-visible = No visible build has that ID."))

    async def _search(self, event: sl.SubmitEvent) -> None:
        form = _SearchForm.from_submission(event)
        await self._run_search(form.query or "", form.sort or "")

    async def _toggle_record_category(self, category: str, event: sl.ToggleEvent) -> None:
        if event.value:
            self._record_categories = self._record_categories | {category}
        else:
            self._record_categories = self._record_categories - {category}
        await self._run_search(self._query, self._sort)

    async def _all_standings(self, _event: sl.PressEvent) -> None:
        self._record_categories = frozenset()
        self._query = ""
        self._sort = ""
        self._results = None
        self._selected = None
        self._show_standings = True

    async def _run_search(self, query: str, sort: str) -> None:
        if self._search_service is None:
            message = "The catalogue search service is not configured"
            raise RuntimeError(message)
        scope = SearchScope.RECORDS if self._mode == "records" else SearchScope.BUILDS
        # Enforce standings eligibility outside the user's expression, including OR queries.
        expression = f"status:resolved AND ({query})" if query.strip() else "status:resolved"
        if self._record_categories:
            categories = " OR ".join(f"base_title:{category}" for category in sorted(self._record_categories))
            expression += f" AND ({categories})"
        request = SearchRequest(
            expression if scope is SearchScope.RECORDS else query,
            scope=scope,
            sort=SearchSort.parse(sort or None),
            page_size=8,
            visible_statuses=frozenset(status.name.lower() for status in self._statuses),
        )
        page = await self._search_service.search(request)
        self._results = SearchScreen(
            self._search_service,
            request,
            page,
            load_build=self._queries.get,
            render_build=self._render_build,
        )
        self._query = query
        self._sort = sort
        self._selected = None

    async def _back(self, _event: sl.PressEvent) -> None:
        self._selected = None


def _build_summary(build: Build) -> sl.TextLike:
    """A compact list entry; the full card is reserved for an opened build."""
    title = build.title
    size = " x ".join(str(value) if value is not None else "?" for value in (build.width, build.height, build.depth))
    status = cast(Status, build.submission_status).name.lower()
    return sl.md(t"**#{build.id} {title}** — {size} · {build.category} · {status}")


class _OpenBuildForm(sl.forms.Form):
    title = tr(t"bot-submission-build-browse-open-build = Open build")

    id = sl.forms.IntField(label=tr(t"bot-submission-build-browse-build-id = Build ID"), minimum=1)


class _SearchForm(sl.forms.Form):
    title = tr(t"build-browser-search-filters = Search & filters")

    query = sl.forms.TextField(
        label=tr(t"build-browser-search-query = Name, keywords, or filters"),
        maximum=500,
        required=False,
        description=tr(
            t"build-browser-filter-help = Examples: title:door, creator:Steve, width:5, kind:door, base_title:smallest"
        ),
    )
    sort = sl.forms.TextField(
        label=tr(t"build-browser-search-sort = Sort (optional)"),
        required=False,
        maximum=80,
        description=tr(t"build-browser-sort-help = Examples: width, -width, -created_at. Leave blank for relevance."),
    )
