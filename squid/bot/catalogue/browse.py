"""Canonical build catalogue, review, and schematic workspace."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from functools import partial
from typing import Any, Literal, Self, cast, override

import squid_ui as sl
import squid_ui.ext.discord as sd
import squid_ui_widgets as sp
from squid.bot.accounts.consent import with_consented_account
from squid.bot.app import RedstoneSquid
from squid.bot.catalogue.record_browser import CATALOGUE_PAGE_SIZE, record_browser
from squid.bot.catalogue.records import RecordsScreen
from squid.bot.catalogue.rows import build_row
from squid.bot.catalogue.schematics.tools import SchematicTools
from squid.bot.catalogue.search.clauses import CatalogueQuery, combine_clauses, query_leaves, replace_clause
from squid.bot.catalogue.search.filters import GuidedSearch, SearchSelection
from squid.bot.catalogue.search.help import search_help
from squid.bot.catalogue.search.labels import filter_label, sort_label
from squid.bot.catalogue.search.results import SearchScreen
from squid.bot.edit_controller import BoundEdit, EditController
from squid.bot.routes import AppRequest, ScreenUnavailable, router
from squid.bot.submission.build_card import render_build_card
from squid.bot.submission.ui import controls as build_controls
from squid.bot.ui import tr, truncate_display_text
from squid.bot.utils.permissions import allows
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
    BUILD_SUBMISSION_EDIT,
    BUILD_SUBMISSION_REJECT,
    BUILD_SUBMISSION_VIEW_PENDING,
    RECORD_ENTRY_INSPECT,
    RECORD_ENTRY_REBUILD,
)
from squid.records.application import RecordService
from squid.records.domain import BaseTitle
from squid.schematics.application import SchematicService
from squid.search.application import SearchService
from squid.search.application.fields import DEFAULT_FIELD_REGISTRY
from squid.search.application.parser import SearchQueryParser
from squid.search.domain import RecordQuery, SearchMode, SearchRequest, SearchScope, SearchSort
from squid.search.domain.query import (
    BooleanExpression,
    BooleanOperator,
    ComparisonOperator,
    FieldExpression,
    NotExpression,
    QueryExpression,
)
from squid_i18n import Text, inline_code
from squid_reactivity import Topic, TopicBus
from squid_ui.ext.discord import primitives
from squid_ui.ext.discord.target_types import ComponentsV2Target
from squid_ui.sources import window_fingerprint

type BuildRenderer = Callable[[Build], Awaitable[sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]]]]
type BuildAuthorizer = Callable[[PermissionNode], Awaitable[bool]]
type RefreshPosts = Callable[[int], Awaitable[None]]

_RECORD_TITLE_BUTTONS = (BaseTitle.SMALLEST, BaseTitle.FASTEST, BaseTitle.FIRST)


@dataclass(frozen=True, slots=True)
class BuildCapabilities:
    """Initial visibility choices for build details and actions, not authorization to execute them."""

    view_pending: bool
    approve: bool
    reject: bool
    edit: bool


class _BuildSource:
    capabilities = sl.sources.SourceCapabilities(
        backward=True,
        offsets=True,
        fixed_groups=True,
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
        # This source exposes offset jumps; the service may return ID links at offset zero.
        return sl.sources.Window(
            sl.sources.Position(str(page.items[0].id) if page.items else None, position.offset),
            page.items,
            previous=sl.sources.Position(offset=max(0, position.offset - extent)) if page.prev is not None else None,
            next=sl.sources.Position(offset=position.offset + len(page.items)) if page.next is not None else None,
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


class _BuildDetail(sl.Component[ComponentsV2Target, sd.Request[Any, RedstoneSquid]]):
    """Show one build and refresh its card when the build topic changes.

    Given only an ID, the build loads on first render; a `Build` is shown immediately.

    Capabilities control which actions appear. Review actions recheck permission
    through authorize; the routed editor checks its own access.
    """

    _decision: sp.ComponentDriver[sp.DecisionState, ComponentsV2Target, sd.Request[Any, RedstoneSquid]] | None = (
        sl.state(None, opaque=True)
    )
    _review: BoundEdit | None = sl.state(None, opaque=True)
    _pending_review: Literal["approve", "reject"] | None = sl.state(None)
    _show_schematics: bool = sl.state(False)

    def __init__(
        self,
        build: Build | int,
        *,
        changes: Topic,
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
        if isinstance(build, int):
            build_id, seed = build, None
        else:
            assert build.id is not None
            build_id, seed = build.id, build
        self._changes = changes
        self._build_id = build_id
        self._seed: Build | None = seed
        self.controller = EditController(edits, actor, self._refresh_review)
        self._queries = queries
        self._builds = builds
        self._edits = edits
        self._actor = actor
        self._schematics = schematics
        self.schematics = SchematicTools(
            build_id,
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
    async def projection(
        self,
    ) -> tuple[Build, sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]], bool]:
        self._changes.watch()
        build, self._seed = self._seed, None
        if build is None:
            build = await self._queries.get(self._build_id)
        if build is None:
            message = f"build {self._build_id} no longer exists"
            raise LookupError(message)
        has_schematic = (
            self._schematics.available and await self._schematics.featured_for_build(self._build_id) is not None
        )
        return build, await self._render_build(build), has_schematic

    @override
    def render(self) -> sl.DocumentLike[ComponentsV2Target, sd.Request[Any, RedstoneSquid]]:
        state = self.projection.status
        if not isinstance(state, sl.resources.Ready) and state.previous is None:
            return (sl.status(tr(t"bot-submission-build-browse-loading-build = Loading build.")),)
        if isinstance(state, sl.resources.Ready):
            build, node, has_schematic = state.value
        else:
            assert state.previous is not None
            build, node, has_schematic = state.previous.value
        if self._decision is not None:
            return (node, self._decision)
        nodes: list[sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]]] = [node]
        nodes.append(self._detail_actions(build, has_schematic=has_schematic))
        nodes.extend(self.controller.controls())
        if has_schematic:
            nodes.append(self.schematics)
        return nodes

    async def _toggle_schematics(self, _event: sl.PressEvent) -> None:
        self._show_schematics = not self._show_schematics

    def _detail_actions(
        self, build: Build, *, has_schematic: bool
    ) -> sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]]:
        actions: list[sl.semantic.ActionControl | sl.semantic.RoutedActionControl] = [
            sl.routed_action_control(
                tr(t"build-browse-tags = Build tags"),
                build_controls.build_tags.custom_id(build_id=self._build_id),
                key="tags",
            )
        ]
        if self._capabilities.edit or (
            build.submission_status is Status.PENDING
            and self._actor_account_id is not None
            and build.submitter_account_id == self._actor_account_id
        ):
            actions.append(
                sl.routed_action_control(
                    tr(t"bot-submission-build-browse-edit = Edit"),
                    build_controls.edit_build.custom_id(build_id=self._build_id),
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
        if has_schematic:
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

    def _review_prompt(self) -> sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]]:
        build_id = self._build_id
        if self._pending_review == "approve":
            return sl.section(
                sl.heading(tr(t"build-review-approve-title = Approve build #{build_id!s}?")),
                sl.paragraph(tr(t"build-review-approve-effect = The build becomes visible in the public catalogue.")),
                accent=sl.Tone.SUCCESS,
            )
        return sl.section(
            sl.heading(tr(t"build-review-reject-title = Reject build #{build_id!s}?")),
            sl.paragraph(tr(t"build-review-reject-effect = The build is marked as denied and leaves the catalogue.")),
            accent=sl.Tone.DANGER,
        )

    async def _request_approve(self, revision: int, _event: sl.PressEvent) -> None:
        self._request_review("approve", revision)

    async def _request_reject(self, revision: int, _event: sl.PressEvent) -> None:
        self._request_review("reject", revision)

    def _request_review(self, action: Literal["approve", "reject"], revision: int) -> None:
        self._review = self.controller.bind(BUILD_STATUS_EDIT, build_target(self._build_id), revision)
        self._pending_review = action
        confirm = (
            sp.DecisionOption("confirm", tr(t"bot-submission-build-browse-approve = Approve"), sl.Tone.SUCCESS)
            if action == "approve"
            else sp.DecisionOption("confirm", tr(t"bot-submission-build-browse-reject = Reject"), sl.Tone.DANGER)
        )
        self._decision = sp.Decision[ComponentsV2Target, sd.Request[Any, RedstoneSquid]](
            self._review_prompt(),
            (
                confirm,
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

    async def _may(self, event: sl.ActionEvent[Any], node: PermissionNode) -> bool:
        if await self._authorize(node):
            return True
        await event.notice(
            tr(t"build-operation-permission-denied = You are no longer allowed to perform this build operation.")
        )
        return False


@dataclass(frozen=True, slots=True)
class _TabSearch:
    query: CatalogueQuery = CatalogueQuery()
    sort: str = ""
    mode: SearchMode = SearchMode.LEXICAL


class BuildBrowseScreen(sd.Screen[None, Any, RedstoneSquid]):
    """A live build workspace that ends when replaced or timed out."""

    # Expanded filters, a populated build with tools, and search help can each
    # need their own message. Keep all three available while browsing.
    policy = sd.ResponseSpec(
        audience="public",
        nav=sd.navigation_controls.page_jump_nav,
        timeout=300,
        live_updates=True,
        session=sd.SessionSpec("build-browse"),
        max_messages=3,
    )

    _results: SearchScreen | None = sl.state(None, opaque=True)
    _show_search_help: bool = sl.state(False)
    _query: CatalogueQuery = sl.state(CatalogueQuery())
    _sort: str = sl.state("")
    _search_mode: SearchMode = sl.state(SearchMode.LEXICAL)
    _build_search: _TabSearch = sl.state(_TabSearch())
    _record_search: _TabSearch = sl.state(_TabSearch(query=CatalogueQuery(RecordQuery(None, ""))))
    _guided: GuidedSearch | None = sl.state(None, opaque=True)
    _mode: Literal["builds", "records", "tools"] = sl.state("builds")
    _selected: sp.Browser[Build, ComponentsV2Target, sd.Request[Any, RedstoneSquid]] | None = sl.state(
        None, opaque=True
    )

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
        self._search_fields = DEFAULT_FIELD_REGISTRY
        self._mode = "records" if initial_records and records is not None else "builds"
        self._query = self._record_search.query if self._mode == "records" else self._build_search.query
        self._records = record_browser(records, self._open_holder) if records is not None else None
        self._record_tools = record_tools
        if record_tools is not None:
            record_tools.open_build = self._open_holder
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
            label=_build_label,
            # `_BuildSource` filters on `submission_status`, so every listed build carries one; the
            # domain type stays optional only for a build that has not been submitted yet.
            summary=_build_summary,
            detail=self._detail,
            page_size=CATALOGUE_PAGE_SIZE,
            show_page_size=True,
            title=tr(t"bot-submission-build-browse-build-catalogue = Build catalogue"),
            empty=tr(t"build-list-empty = No builds are available."),
            # `_BuildSource` uses the default listing order: highest, so newest, build ID first.
            chronology=sl.sources.Chronology.NEWEST_FIRST,
        )

    @classmethod
    async def create(
        cls,
        request: AppRequest,
        *,
        build_id: int | None = None,
        initial_records: bool = False,
    ) -> Self:
        """Build the catalogue with the requesting user's capabilities.

        Args:
            build_id: Open this build's detail once the screen loads.
            initial_records: Start on the record standings instead of the build list.
        """
        subject = await request.client.actors.resolve(request)
        permissions = request.client.services.permissions

        async def held(node: PermissionNode) -> bool:
            return (await permissions.check(subject, node)).allowed

        capabilities = BuildCapabilities(
            view_pending=await held(BUILD_SUBMISSION_VIEW_PENDING),
            approve=await held(BUILD_SUBMISSION_APPROVE),
            reject=await held(BUILD_SUBMISSION_REJECT),
            edit=await held(BUILD_SUBMISSION_EDIT),
        )

        async def authorize(node: PermissionNode) -> bool:
            return await allows(request, node)

        async def refresh_posts(build_id: int) -> None:
            await request.client.refresh_posts("build", str(build_id))

        can_inspect = request.guild is not None and await held(RECORD_ENTRY_INSPECT)
        can_rebuild = request.guild is not None and await held(RECORD_ENTRY_REBUILD)
        record_tools = (
            RecordsScreen(
                request.client.services.records,
                request.client.services.record_computation,
                can_inspect=can_inspect,
                can_rebuild=can_rebuild,
                authorize=authorize,
            )
            if can_inspect or can_rebuild
            else None
        )

        screen = cls(
            request.client.services.build_queries,
            request.client.services.builds,
            request.client.services.edits,
            lambda: request.client.actors.resolve(request, refresh=True),
            request.client.services.schematics,
            topics=request.client.topic_bus,
            initial_id=build_id,
            render_build=lambda build: render_build_card(
                build, request.client.media_previews, schematic_summaries=request.client.services.schematic_summaries
            ),
            capabilities=capabilities,
            actor_account_id=subject.account_id,
            authorize=authorize,
            refresh_posts=refresh_posts,
            records=request.client.services.records,
            search=request.client.services.search,
            record_tools=record_tools,
            initial_records=initial_records,
        )
        screen._search_fields = await request.client.services.search.fields()
        return screen

    @classmethod
    async def create_search(
        cls,
        request: AppRequest,
        *,
        query: str,
        records: bool = False,
        sort: str = "",
        mode: SearchMode = SearchMode.LEXICAL,
    ) -> Self:
        """Open search results in the catalogue."""
        screen = await cls.create(request, initial_records=records)
        screen._search_mode = mode
        await screen._run_search(CatalogueQuery.from_text(screen._search_parser, query), sort)
        return screen

    @router.route("/builds")
    @classmethod
    async def open_index(cls, request: AppRequest) -> Self:
        """Open the catalogue at its build list."""
        return await cls.create(request)

    @router.route("/builds/{build_id}")
    @classmethod
    async def open_detail(cls, request: AppRequest, build_id: int) -> Self:
        """Open one build, unless it does not exist or the opener may not see it while pending."""
        screen = await cls.create(request, build_id=build_id)
        build = await request.client.services.build_queries.get(build_id)
        if build is None or (build.submission_status is not Status.CONFIRMED and not screen._capabilities.view_pending):
            raise ScreenUnavailable(tr(t"navigation-build-unavailable = That build is unavailable."))
        return screen

    @override
    async def on_load(self) -> None:
        if self._initial_id is not None:
            await self._select(self._initial_id)

    @override
    def render(self) -> tuple[sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]], ...]:
        if self._mode == "tools" and self._record_tools is not None:
            # A build opened from Record tools replaces them until its Back returns there.
            return (self._workspace_controls(), self._selected or self._record_tools)
        nodes = [self._workspace_controls(), *self._find_nodes()]
        if self._show_search_help:
            nodes.append(self._record_search_help if self._mode == "records" else self._build_search_help)
            nodes.append(
                sl.action_controls(
                    sl.action_control(
                        tr(t"catalogue-search-help-close = Close help"), self._toggle_search_help, key="close-help"
                    ),
                    key="help-actions",
                )
            )
        if self._selected is not None:
            nodes.append(self._selected)
        elif self._results is not None:
            nodes.append(self._results)
        else:
            nodes.append(self._records if self._mode == "records" and self._records is not None else self._browser)
        return tuple(nodes)

    def _workspace_controls(self) -> sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]]:
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
            sl.form(
                tr(t"bot-submission-build-browse-open-by-id = Open by ID"),
                _OpenBuildForm().spec(),
                key="open-id",
                on_submit=self._open_id,
            ),
            key="catalogue-views",
            display=sl.semantic.ControlDisplay.INDIVIDUAL,
        )

    async def _switch(self, mode: Literal["builds", "records", "tools"], event: sl.ActionEvent) -> None:
        if mode == self._mode:
            return
        if self._mode != "tools":
            active = _TabSearch(self._query, self._sort, self._search_mode)
            if self._mode == "builds":
                self._build_search = active
                self._record_search = replace(
                    self._record_search,
                    query=self._record_search.query.with_shared_from(
                        SearchQueryParser(self._search_fields, scope=SearchScope.RECORDS), active.query
                    ),
                )
            else:
                self._record_search = active
                self._build_search = replace(
                    self._build_search,
                    query=self._build_search.query.with_shared_from(
                        SearchQueryParser(self._search_fields, scope=SearchScope.BUILDS), active.query
                    ),
                )
        self._results = None
        self._mode = mode
        self._selected = None
        if mode != "tools":
            expanded = self._guided is not None
            self._guided = None
            saved = self._record_search if mode == "records" else self._build_search
            self._query, self._sort, self._search_mode = saved.query, saved.sort, saved.mode
            if saved.query.parsed.expression is not None or saved.sort:
                await self._run_search(saved.query, saved.sort)
            if expanded:
                self._open_search_editor()
        await self._browser._back(event)

    async def _open_holder(self, build_id: int, event: sl.ActionEvent[Any]) -> None:
        if not await self._select(build_id):
            await event.notice(tr(t"build-id-not-visible = No visible build has that ID."))

    def _find_nodes(self) -> list[sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]]]:
        nodes: list[sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]]] = []
        query = _query_summary(self._query, records=self._mode == "records")
        sort = sort_label(self._sort)
        nodes.append(
            sd.v2.section(
                tr(t"browse-query-summary = {query} · {sort}"),
                accessory=sd.v2.button(
                    tr(t"build-browser-close-search = Done")
                    if self._guided is not None
                    else tr(t"build-browser-edit-search = Edit search"),
                    self._guided_search,
                    key="guided-search",
                    style=primitives.ActionStyle.SUCCESS
                    if self._guided is not None
                    else primitives.ActionStyle.SECONDARY,
                ),
            )
        )
        if self._guided is not None:
            nodes.append(self._guided)
        if self._mode == "records":
            selection = _record_title_filter(self._query.parsed.expression)
            titles = selection[1] if selection is not None else frozenset()
            nodes.extend(
                (
                    sl.action_controls(
                        *(
                            # Buttons rather than toggles: a toggle labels itself "Smallest: Off".
                            sl.action_control(
                                label,
                                partial(self._toggle_record_category, category),
                                key=category,
                                tone=sl.Tone.SUCCESS if category in titles else sl.Tone.NEUTRAL,
                                available=selection is not None,
                            )
                            for category, label in (
                                (BaseTitle.SMALLEST, tr(t"record-browser-smallest = Smallest")),
                                (BaseTitle.FASTEST, tr(t"record-browser-fastest = Fastest")),
                                (BaseTitle.FIRST, tr(t"record-browser-first = First")),
                            )
                        ),
                        sl.action_control(
                            tr(t"record-browser-all = All types"),
                            self._all_types,
                            key="all-standings",
                            tone=sl.Tone.SUCCESS if selection is not None and not titles else sl.Tone.NEUTRAL,
                            available=selection is not None,
                        ),
                        key="record-categories",
                        display=sl.semantic.ControlDisplay.INDIVIDUAL,
                    ),
                )
            )
        return nodes

    def _search_actions(self) -> tuple[sl.semantic.ActionControl[sd.Request[Any, RedstoneSquid]], ...]:
        return (
            sl.action_control(
                tr(t"catalogue-search-help = Search help"),
                self._toggle_search_help,
                key="search-help",
                tone=sl.Tone.SUCCESS if self._show_search_help else sl.Tone.NEUTRAL,
            ),
            sl.action_control(
                tr(t"guided-search-reset = Reset"),
                self._reset_search,
                key="reset",
                available=bool(self._query.text or self._sort),
            ),
        )

    async def _toggle_search_help(self, _event: sl.PressEvent) -> None:
        self._show_search_help = not self._show_search_help

    async def _apply_search_example(self, query: str, _event: sl.PressEvent) -> None:
        await self._run_search(CatalogueQuery.from_text(self._search_parser, query, typed=False), "")
        if self._guided is not None:
            self._guided.close_panels()

    def _detail(self, build: Build | int) -> _BuildDetail:
        build_id = build if isinstance(build, int) else build.id
        return _BuildDetail(
            build,
            changes=self._topics.topic("build", str(build_id)),
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
            label=_build_label,
            detail=self._detail,
            chronology=sl.sources.Chronology.NEWEST_FIRST,
            # The browser's own list would hold only the opened build; Back returns to the
            # catalogue, search results, or record that the build was opened from.
            on_back=self._back,
        )
        # Prepare the detail before a modal switches views. The parent keeps its own
        # collection and focus while this explicitly opened child browses by build ID.
        await browser._detail.resolve()
        detail = cast(_BuildDetail, browser._detail.value)
        await detail.projection.resolve()
        self._selected = browser

    async def _open_id(self, event: sl.SubmitEvent) -> None:
        form = _OpenBuildForm.from_submission(event)
        build_id = form.id
        if not await self._select(build_id):
            await event.notice(tr(t"build-id-not-visible = No visible build has that ID."))
        elif self._mode == "tools":
            selected = self._selected
            await self._switch("builds", event)
            self._selected = selected

    async def _guided_search(self, event: sl.PressEvent) -> None:
        if self._guided is not None:
            self._guided = None
            return
        self._open_search_editor()

    def _open_search_editor(self) -> None:
        assert self._search_service is not None

        async def selected(selection: SearchSelection) -> None:
            await self._run_search(selection.query, selection.sort)

        self._guided = GuidedSearch(
            self._search_service,
            scope=self._search_scope,
            query=self._query,
            sort=self._sort,
            on_apply=selected,
            actions=self._search_actions,
        )

    @property
    def _search_scope(self) -> SearchScope:
        return SearchScope.RECORDS if self._mode == "records" else SearchScope.BUILDS

    @property
    def _search_parser(self) -> SearchQueryParser:
        return SearchQueryParser(self._search_fields, scope=self._search_scope)

    async def _toggle_record_category(self, category: BaseTitle, _event: sl.PressEvent) -> None:
        await self._edit_record_titles(category)

    async def _all_types(self, _event: sl.PressEvent) -> None:
        await self._edit_record_titles(None)

    async def _edit_record_titles(self, toggle: BaseTitle | None) -> None:
        expression = self._query.parsed.expression
        selection = _record_title_filter(expression)
        if selection is None:
            return
        path, titles = selection
        titles = titles ^ {toggle} if toggle is not None else frozenset()
        parser = self._search_parser
        field = parser.registry.resolve("base_title")
        assert field is not None
        clauses = tuple(FieldExpression(field, ComparisonOperator.EQUAL, title) for title in sorted(titles))
        replacement = combine_clauses(BooleanOperator.OR, *clauses)
        if expression is not None and path is not None:
            expression = replace_clause(expression, path, replacement)
        elif expression is not None and replacement is not None:
            expression = combine_clauses(BooleanOperator.AND, expression, replacement)
        elif expression is None:
            expression = replacement
        query = self._query.with_expression(parser, expression)
        if expression is not None or self._sort:
            if query != self._query:
                await self._run_search(query, self._sort)
        else:
            self._query = query
            self._results = None
            self._selected = None
            if self._guided is not None:
                self._guided.query = query

    async def _reset_search(self, _event: sl.PressEvent) -> None:
        self._query = CatalogueQuery.from_text(self._search_parser, "", typed=False)
        self._sort = ""
        self._results = None
        self._selected = None
        if self._guided is not None:
            self._guided.close_panels()
            self._guided.query = self._query
            self._guided.sort = self._sort

    async def _run_search(self, query: CatalogueQuery, sort: str) -> None:
        if self._search_service is None:
            message = "The catalogue search service is not configured"
            raise RuntimeError(message)
        request = SearchRequest(
            query.text,
            scope=self._search_scope,
            mode=self._search_mode,
            sort=SearchSort.parse(sort or None),
            page_size=CATALOGUE_PAGE_SIZE,
            visible_statuses=frozenset(status.name.lower() for status in self._statuses),
            # One row per build, listing the records it holds, as /search shows results.
            group_by_build=True,
            parsed_query=query.parsed,
        )
        page = await self._search_service.search(request)
        self._results = SearchScreen(
            self._search_service,
            request,
            page,
            load_build=self._queries.get,
            render_build=self._render_build,
            build_detail=self._detail,
        )
        self._query = query
        self._sort = sort
        if self._guided is not None:
            self._guided.query = query
            self._guided.sort = sort
        self._selected = None
        # A new search shows its results, so help gives way to them.
        self._show_search_help = False

    async def _back(self, _event: sl.ActionEvent[Any]) -> None:
        self._selected = None


def _record_title_filter(
    expression: QueryExpression | None,
) -> tuple[tuple[int, ...] | None, frozenset[BaseTitle]] | None:
    """Find the single title clause or OR group that the buttons can edit."""
    if expression is None:
        return None, frozenset()
    conjuncts = (
        tuple(((index,), clause) for index, clause in enumerate(expression.operands))
        if isinstance(expression, BooleanExpression) and expression.operator is BooleanOperator.AND
        else (((), expression),)
    )
    path = None
    titles: set[BaseTitle] = set()
    for clause_path, clause in conjuncts:
        alternatives = (
            clause.operands
            if isinstance(clause, BooleanExpression) and clause.operator is BooleanOperator.OR
            else (clause,)
        )
        if not any(
            isinstance(leaf, FieldExpression) and leaf.field.name == "base_title"
            for _, item in query_leaves(clause)
            for leaf in (item.operand if isinstance(item, NotExpression) else item,)
        ):
            continue
        if path is not None:
            return None
        for item in alternatives:
            if (
                not isinstance(item, FieldExpression)
                or item.field.name != "base_title"
                or item.operator is not ComparisonOperator.EQUAL
                or item.value not in _RECORD_TITLE_BUTTONS
            ):
                return None
            titles.add(BaseTitle(item.value))
        path = clause_path
    return path, frozenset(titles)


def _query_summary(query: CatalogueQuery, *, records: bool) -> sl.TextLike:
    """Describe the active query as its filters, or as everything when it is empty.

    Typed queries keep their spelling. A query with OR keeps its syntax to show the grouping.
    """
    expression = query.parsed.expression
    if expression is None:
        return (
            tr(t"browse-query-all-standings = All standings")
            if records
            else tr(t"browse-query-all-builds = All builds")
        )
    if query.source is not None or _has_or(expression):
        return inline_code(query.text)
    return Text.join(" · ", [filter_label(leaf) for _, leaf in query_leaves(expression)])


def _has_or(expression: QueryExpression) -> bool:
    match expression:
        case BooleanExpression(operator=BooleanOperator.OR):
            return True
        case BooleanExpression(operands=operands):
            return any(_has_or(item) for item in operands)
        case NotExpression(operand=operand):
            return _has_or(operand)
        case _:
            return False


def _build_label(build: Build) -> str:
    """A plain-text label for selection menus, which cannot show the title's Markdown."""
    return truncate_display_text(f"#{build.id} {build.one_line_title}", 100)


def _build_summary(build: Build) -> sl.TextLike:
    """A compact list entry; the full card is reserved for an opened build."""
    assert build.id is not None
    return build_row(build.id, build.one_line_title, build.dimensions)


class _OpenBuildForm(sl.forms.Form[sd.Request[Any, Any]]):
    title = tr(t"bot-submission-build-browse-open-build = Open build")

    id = sl.forms.IntField(label=tr(t"bot-submission-build-browse-build-id = Build ID"), minimum=1)
