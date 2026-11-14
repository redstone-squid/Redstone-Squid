"""Mounted squid-ui component for interactive search results."""

from collections.abc import Awaitable, Callable
from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING, Any, override

import squid_ui as sl
import squid_ui_discord as sd
import squid_ui_widgets as sp
from squid.bot.app import RedstoneSquid
from squid.bot.ui import tr, truncate_display_text
from squid.builds.domain import Status
from squid.builds.models import Build
from squid.search.domain import (
    BuildSearchHit,
    RecordSearchHit,
    SearchHit,
    SearchPage,
    SearchRequest,
    SearchScope,
    SearchText,
)
from squid_i18n import Text, md_tr, strong
from squid_reactivity import untracked
from squid_ui.sources import window_fingerprint

if TYPE_CHECKING:
    from squid.search.application import SearchService


BuildLoader = Callable[[int], Awaitable[Build | None]]
type BuildRenderer = Callable[[Build], Awaitable[sl.LayoutNode[sl.ComponentsV2Target, sd.Request[Any, RedstoneSquid]]]]


def _hit_identity(hit: SearchHit) -> str:
    return f"{hit.resource_kind}:{hit.source_id}"


class _SearchSource:
    """Adapt search pages to browser windows while retaining their warning metadata."""

    capabilities = sl.sources.SourceCapabilities(
        backward=True,
        offsets=True,
        jumpable=True,
        count=sl.sources.CountPrecision.EXACT,
    )

    def __init__(self, service: SearchService, request: SearchRequest, initial: SearchPage) -> None:
        self._service = service
        self.base_request = request
        self._initial: SearchPage | None = initial
        self._metadata: dict[tuple[int, str], SearchPage] = {}

    def request_at(self, offset: int) -> SearchRequest:
        return replace(self.base_request, offset=offset)

    def _loaded(self, page: SearchPage, offset: int) -> sl.sources.LoadedWindow[SearchHit]:
        window = sl.sources.Window(
            sl.sources.Position(_hit_identity(page.hits[0]) if page.hits else None, offset),
            page.hits,
            previous=sl.sources.Position(offset=page.prev.offset)
            if page.prev is not None and page.prev.offset is not None
            else None,
            next=sl.sources.Position(offset=page.next.offset)
            if page.next is not None and page.next.offset is not None
            else None,
            total=page.total,
        )
        fingerprint = window_fingerprint(page.hits, _hit_identity)
        # Offset alone cannot distinguish an older visible page from a refreshed result set.
        self._metadata[(offset, fingerprint)] = page
        return sl.sources.LoadedWindow(window, fingerprint)

    def initial_loaded(self) -> sl.sources.LoadedWindow[SearchHit]:
        assert self._initial is not None
        page = self._initial
        self._initial = None
        return self._loaded(page, self.base_request.offset)

    async def fetch(self, position: sl.sources.Position, extent: int) -> sl.sources.Window[SearchHit]:
        request = replace(self.base_request, offset=position.offset, page_size=extent)
        page = await self._service.search(request)
        return self._loaded(page, position.offset).window

    def page_for(self, loaded: sl.sources.LoadedWindow[SearchHit]) -> SearchPage:
        page = self._metadata.get((loaded.position.offset, loaded.fingerprint))
        if page is None:
            message = "search source has no metadata for the loaded window"
            raise sl.errors.LayoutInvariantError(message)
        return page


class _SearchDetail(sl.Component[sl.ComponentsV2Target, sd.Request[Any, RedstoneSquid]]):
    _build_node: sl.LayoutNode[sl.ComponentsV2Target, sd.Request[Any, RedstoneSquid]] | None = sl.state(
        None, persist=False, opaque=True
    )

    def __init__(
        self,
        hit: SearchHit,
        *,
        load_build: BuildLoader | None,
        render_build: BuildRenderer | None,
    ) -> None:
        self.hit = hit
        self._load_build = load_build
        self._render_build = render_build

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request[Any, RedstoneSquid]], ...]:
        detail = self._build_node or sl.section(
            sl.heading(self.hit.title),
            sl.paragraph(_detail_text(self.hit)).truncate(),
            accent=sl.Tone.SUCCESS,
        )
        build_id = _build_id(self.hit)
        return (
            detail,
            *(
                (
                    sl.action_controls(
                        sl.action_control(
                            tr(t"bot-submission-search-view-view-build = View build"),
                            self._open_build,
                            key="open-build",
                        ),
                        key="build-actions",
                    ),
                )
                if build_id is not None
                else ()
            ),
        )

    async def _open_build(self, event: sl.ActionEvent) -> None:
        build_id = _build_id(self.hit)
        if build_id is None or self._load_build is None or self._render_build is None:
            await event.notice(tr(t"build-search-build-unavailable = That build is no longer available."))
            return
        build = await self._load_build(build_id)
        if build is None:
            await event.notice(tr(t"build-search-build-unavailable = That build is no longer available."))
            return
        self._build_node = await self._render_build(build)


class _GroupedBuildDetail(sl.Component[sl.ComponentsV2Target, sd.Request[Any, RedstoneSquid]]):
    _show_records: bool = sl.state(False)
    _holder_id: int | None = sl.state(None)

    def __init__(
        self,
        hit: BuildSearchHit,
        scope: SearchScope,
        *,
        load_build: BuildLoader | None,
        render_build: BuildRenderer | None,
    ) -> None:
        self.hit = hit
        self._load_build = load_build
        self._render_build = render_build
        self._records_label = (
            tr(t"search-grouped-matching-records = Matching records")
            if scope is SearchScope.RECORDS
            else tr(t"search-grouped-records = Records")
        )
        self._records = sp.Browser(
            sl.sources.list_source(hit.records),
            key="search-records",
            identity=lambda record: record.source_id,
            label=lambda record: truncate_display_text(record.title, 80),
            summary=lambda record: Text.join(
                "\n",
                (
                    _highlighted(record.title_match or SearchText(record.title)),
                    record.subtitle or record.version_scope,
                ),
            ),
            detail=self._record_detail,
            page_size=5,
            title=self._records_label,
        )

    @sl.resource(pending=sl.resources.PendingMode.ATOMIC)
    async def card(self) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request[Any, RedstoneSquid]]:
        build_id = self._holder_id or int(self.hit.source_id)
        if self._load_build is None or self._render_build is None:
            return sl.section(sl.heading(self.hit.title), sl.paragraph(_detail_text(self.hit)))
        build = await self._load_build(build_id)
        if build is None or build.submission_status is not Status.CONFIRMED:
            return sl.note(tr(t"build-search-build-unavailable = That build is no longer available."))
        return await self._render_build(build)

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request[Any, RedstoneSquid]], ...]:
        if self._show_records and self._holder_id is None:
            return (
                self._records,
                sl.action_controls(
                    sl.action_control(
                        tr(t"search-grouped-build-details = Build details"),
                        self._back_to_build,
                        key="search-build-details",
                    ),
                    key="search-build-detail-actions",
                ),
            )
        state = self.card.status
        if isinstance(state, sl.resources.Ready):
            node = state.value
        elif isinstance(state, sl.resources.Failed):
            node = sl.stack(
                sl.note(tr(t"build-search-build-unavailable = That build is no longer available.")),
                sl.action_controls(
                    sl.action_control(
                        tr(t"bot-submission-search-view-retry = Retry"), self._retry, key="search-build-retry"
                    ),
                    key="search-build-retry-actions",
                ),
            )
        else:
            node = sl.note(tr(t"search-grouped-loading-build = Loading build…"))
        if self._holder_id is not None:
            return (
                node,
                sl.action_controls(
                    sl.action_control(
                        self._records_label,
                        self._back_to_records,
                        key="search-back-records",
                    ),
                    key="search-holder-actions",
                ),
            )
        if self.hit.records:
            return (
                node,
                sl.action_controls(
                    sl.action_control(
                        self._records_label,
                        self._back_to_records,
                        key="search-show-records",
                    ),
                    key="search-record-actions",
                ),
            )
        return (node,)

    def _record_detail(
        self, record: RecordSearchHit
    ) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request[Any, RedstoneSquid]]:
        others = tuple(build_id for build_id in record.holder_build_ids if build_id != int(self.hit.source_id))
        metrics = ", ".join(f"{key}: {value}" for key, value in record.metrics.items())
        return sl.section(
            sl.heading(record.title),
            sl.paragraph(record.subtitle) if record.subtitle else None,
            sl.note(record.version_scope.replace("_", " ")),
            sl.paragraph(metrics) if metrics else None,
            sl.note(tr(t"search-grouped-tied-holders = Tied holders")) if others else None,
            sl.action_controls(
                *(
                    sl.action_control(
                        tr(t"search-grouped-open-holder = View build #{build_id!s}"),
                        partial(self._open_holder, build_id),
                        key=f"search-holder-{build_id}",
                    )
                    for build_id in others
                ),
                key="search-tied-holders",
            )
            if others
            else None,
        )

    async def _open_holder(self, build_id: int, _event: sl.ActionEvent) -> None:
        self._holder_id = build_id

    async def _back_to_records(self, _event: sl.ActionEvent) -> None:
        self._holder_id = None
        self._show_records = True

    async def _back_to_build(self, _event: sl.ActionEvent) -> None:
        self._show_records = False

    async def _retry(self, _event: sl.ActionEvent) -> None:
        self.card.invalidate()


class SearchScreen(sd.Screen[None, Any, RedstoneSquid]):
    """A resource-backed search workspace that ends when replaced or timed out."""

    policy = sd.ResponseSpec(
        nav=sd.navigation_controls.page_jump_nav, timeout=180, audience="public", session=sd.SessionSpec("search")
    )

    def __init__(
        self,
        service: SearchService,
        request: SearchRequest,
        page: SearchPage,
        *,
        load_build: BuildLoader | None = None,
        render_build: BuildRenderer | None = None,
    ) -> None:
        self._source = _SearchSource(service, request, page)
        self._load_build = load_build
        self._render_build = render_build
        self._browser = sp.Browser(
            self._source,
            key="search",
            identity=_hit_identity,
            label=lambda hit: _result_label(hit, request.scope),
            summary=lambda hit: _result_description(hit, request.scope),
            detail=self._detail,
            overview=self._overview,
            page_size=request.page_size,
            title=tr(t"bot-submission-search-view-search-results = Search results"),
            empty=tr(t"build-search-no-results = No results match this query."),
            copy=sp.LoadingCopy(
                loading=tr(t"build-search-loading = Loading search results…"),
                failed=tr(t"build-search-load-failed = Could not load search results."),
                retry=tr(t"bot-submission-search-view-retry = Retry"),
            ),
        )
        self._browser.session.replace(self._source.initial_loaded())

    @property
    def request(self) -> SearchRequest:
        """Return the request currently displayed by the component."""
        return self._source.request_at(self._visible_window().position.offset)

    @property
    def page(self) -> SearchPage:
        """Return the current result page."""
        return self._source.page_for(self._visible_window())

    @property
    def hits(self) -> tuple[SearchHit, ...]:
        """Return the results currently displayed."""
        return self._visible_window().window.items

    @property
    def can_go_back(self) -> bool:
        """Return whether an earlier page exists."""
        return self._visible_window().window.has_previous

    @property
    def can_go_forward(self) -> bool:
        """Return whether a later page exists."""
        return self._visible_window().window.has_next

    def _visible_window(self) -> sl.sources.LoadedWindow[SearchHit]:
        """Keep the last loaded page visible while a fetch is pending or has failed."""
        current = self._browser.session.snapshot
        if current is None:
            message = "search browser has no visible window"
            raise sl.errors.LayoutInvariantError(message)
        return current.loaded

    def _detail(self, hit: SearchHit) -> _SearchDetail | _GroupedBuildDetail:
        if isinstance(hit, BuildSearchHit) and hit.grouped:
            # The nested browser owns its state; reading it while constructing the
            # detail must not make the outer browser recreate it on navigation.
            with untracked():
                return _GroupedBuildDetail(
                    hit, self._source.base_request.scope, load_build=self._load_build, render_build=self._render_build
                )
        return _SearchDetail(
            hit,
            load_build=self._load_build,
            render_build=self._render_build,
        )

    def _overview(
        self, loaded: sl.sources.LoadedWindow[SearchHit]
    ) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request[Any, RedstoneSquid]] | tuple[()]:
        page = self._source.page_for(loaded)
        notes: list[sl.TextLike] = []
        if self.request.group_by_build:
            total = page.total
            notes.append(
                tr(t"search-grouped-build-count = {total} builds")
                if self.request.scope in {SearchScope.BUILDS, SearchScope.RECORDS}
                else tr(t"search-grouped-result-count = {total} results")
            )
        notes.extend(f"⚠ {item}" for item in page.warnings)
        return sl.note(Text.join("\n", notes)) if notes else ()

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request[Any, RedstoneSquid]], ...]:
        return (self._browser,)


def _build_id(hit: SearchHit) -> int | None:
    if isinstance(hit, RecordSearchHit):
        return hit.build_id
    if isinstance(hit, BuildSearchHit) and hit.source_id.isdigit():
        return int(hit.source_id)
    return None


def _metadata_label(kind: str) -> sl.TextLike:
    match kind:
        case "restriction":
            return tr(t"bot-submission-search-view-restriction = Restriction")
        case "pattern":
            return tr(t"bot-submission-search-view-pattern = Pattern")
        case "showcase":
            return tr(t"bot-submission-search-view-showcase-tag = Showcase tag")
        case "creator":
            return tr(t"bot-submission-search-view-creator = Creator")
        case "version":
            return tr(t"bot-submission-search-view-version = Version")
        case "tag":
            return tr(t"bot-submission-search-view-tag = Tag")
        case _:
            return kind


def _result_title(hit: SearchHit, scope: SearchScope) -> SearchText:
    if isinstance(hit, BuildSearchHit) and hit.grouped and scope is SearchScope.RECORDS and hit.records:
        record = hit.records[0]
        return record.title_match or SearchText(record.title)
    return hit.title_match or SearchText(hit.title)


def _result_label(hit: SearchHit, scope: SearchScope) -> str:
    title = _result_title(hit, scope).text
    if isinstance(hit, BuildSearchHit) and hit.grouped:
        # Tied holders can share the same heading; keep their picker labels distinct.
        suffix = f" · #{hit.source_id}"
        return truncate_display_text(title, 80 - len(suffix)) + suffix
    return truncate_display_text(title, 80)


def _highlighted(value: SearchText) -> Text:
    parts: list[sl.TextLike] = []
    position = 0
    for start, end in value.spans:
        parts.extend((value.text[position:start], strong(value.text[start:end])))
        position = end
    parts.append(value.text[position:])
    return Text.join("", parts)


def _result_description(hit: SearchHit, scope: SearchScope = SearchScope.RECORDS) -> sl.TextLike:
    if isinstance(hit, BuildSearchHit) and hit.grouped:
        title = _result_title(hit, scope)
        build_id = hit.source_id
        identity = tr(t"search-grouped-build-identity = Build #{build_id}")
        if scope is SearchScope.RECORDS and hit.title != title.text:
            identity += " · " + _highlighted(hit.title_match or SearchText(hit.title))
        records = hit.records[1:] if scope is SearchScope.RECORDS else hit.records
        if scope in {SearchScope.BUILDS, SearchScope.ALL} and len(records) == 1 and records[0].title == title.text:
            records = ()
        lines: list[sl.TextLike] = [_highlighted(title), identity]
        lines.extend(_highlighted(record.title_match or SearchText(record.title)) for record in records[:2])
        if len(records) > 2:
            count = len(records) - 2
            lines.append(tr(t"search-grouped-more-records = +{count} more records"))
        return Text.join("\n", lines)
    if isinstance(hit, RecordSearchHit):
        build_title = hit.build_title
        return sl.md(t"**{hit.title}** — {build_title}")
    if isinstance(hit, BuildSearchHit):
        status = hit.status
        return sl.md(t"**{hit.title}** · {status}")
    return Text.join("\n", (_highlighted(hit.title_match or SearchText(hit.title)), _metadata_label(hit.metadata_kind)))


def _detail_text(hit: SearchHit) -> sl.TextLike:
    if isinstance(hit, RecordSearchHit):
        tags = ", ".join(tag for tag in hit.tags)
        build_title = hit.build_title
        build_id = hit.build_id
        base_title = hit.base_title
        version_scope = hit.version_scope
        fields = md_tr(
            t"""
            bot-submission-search-view-build-base-title =
                {{ "*" }}*Build**
                {build_title} ({build_id!s})
                {{ "*" }}*Base title**
                {base_title} · {version_scope}
            """,
        )
        if hit.metrics:
            metrics = ", ".join(f"{key}: {value}" for key, value in hit.metrics.items())
            fields = md_tr(
                t"""
                bot-submission-search-view-metrics =
                    {fields}
                    {{ "*" }}*Metrics**
                    {metrics}
                """,
            )
        description = hit.subtitle or ""
    elif isinstance(hit, BuildSearchHit):
        tags = ", ".join(tag for tag in hit.tags)
        status = hit.status
        fields = md_tr(
            t"""
            bot-submission-search-view-status =
                {{ "*" }}*Status**
                {status}
            """,
        )
        description = hit.description or ""
    else:
        tags = ", ".join(alias for alias in hit.aliases)
        kind = _metadata_label(hit.metadata_kind)
        fields = md_tr(
            t"""
            bot-submission-search-view-kind =
                {{ "*" }}*Kind**
                {kind}
            """,
        )
        description = hit.description or ""
    if tags:
        fields = md_tr(
            t"""
            bot-submission-search-view-tags =
                {fields}
                {{ "*" }}*Tags**
                {tags}
            """,
        )
    return md_tr(
        t"""
        bot-submission-search-view-value =
            {description}
            {fields}
        """,
    )
