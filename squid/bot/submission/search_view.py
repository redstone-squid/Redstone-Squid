"""Mounted squid-ui component for interactive search results."""

from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Any, override

import squid_ui as sl
import squid_ui_discord as sd
import squid_ui_widgets as sp
from squid.bot.app import RedstoneSquid
from squid.bot.ui import tr, truncate_display_text
from squid.builds.models import Build
from squid.search.domain import BuildSearchHit, RecordSearchHit, SearchHit, SearchPage, SearchRequest
from squid_i18n import md_tr
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
            label=lambda hit: truncate_display_text(hit.title, 80),
            summary=_result_description,
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

    def _detail(self, hit: SearchHit) -> _SearchDetail:
        return _SearchDetail(
            hit,
            load_build=self._load_build,
            render_build=self._render_build,
        )

    def _overview(
        self, loaded: sl.sources.LoadedWindow[SearchHit]
    ) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request[Any, RedstoneSquid]] | tuple[()]:
        warnings = self._source.page_for(loaded).warnings
        if not warnings:
            return ()
        return sl.note("\n".join(f"⚠ {item}" for item in warnings))

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


def _result_description(hit: SearchHit) -> sl.TextLike:
    if isinstance(hit, RecordSearchHit):
        build_title = hit.build_title
        return sl.md(t"**{hit.title}** — {build_title}")
    if isinstance(hit, BuildSearchHit):
        status = hit.status
        return sl.md(t"**{hit.title}** · {status}")
    return _metadata_label(hit.metadata_kind)


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
