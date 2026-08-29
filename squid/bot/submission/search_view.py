"""Mounted squid-ui component for interactive search results."""

from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import TYPE_CHECKING

from discord.utils import escape_markdown

import squid_ui as sl
import squid_ui_discord as sd
import squid_ui_widgets as sp
from squid.bot.ui import DISCORD_GREEN, L
from squid.builds.domain import Build
from squid.search.domain import BuildSearchHit, RecordSearchHit, SearchHit, SearchPage, SearchRequest
from squid_ui.sources import window_fingerprint

if TYPE_CHECKING:
    from squid.search.application import SearchService


BuildLoader = Callable[[int], Awaitable[Build | None]]
BuildRenderer = Callable[[Build], Awaitable[sl.LayoutNode[sl.ComponentsV2Target]]]


def _hit_identity(hit: SearchHit) -> str:
    return f"{hit.resource_kind}:{hit.source_id}"


class _SearchSource:
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
            has_previous=page.prev is not None,
            has_next=page.next is not None,
            total=page.total,
        )
        fingerprint = window_fingerprint(page.hits, _hit_identity)
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


class _SearchDetail(sl.Component[sl.ComponentsV2Target]):
    _build_node: sl.LayoutNode[sl.ComponentsV2Target] | None = sl.state(None, persist=False, opaque=True)

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

    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        detail = self._build_node or sl.section(
            sl.heading(_detail_title(self.hit)),
            sl.truncate(sl.paragraph(_detail_text(self.hit))),
            accent=DISCORD_GREEN,
        )
        build_id = _build_id(self.hit)
        return (
            detail,
            *(
                (
                    sl.action_controls(
                        sl.action_control(L(t"View build"), self._open_build, key="open-build"),
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
            await event.notice(L(t"That build is no longer available."))
            return
        build = await self._load_build(build_id)
        if build is None:
            await event.notice(L(t"That build is no longer available."))
            return
        self._build_node = await self._render_build(build)


class SearchScreen(sd.UserSessionScreen):
    """A resource-backed search workspace that ends when closed, replaced, or timed out."""

    session_name = "search"
    timeout = 180
    visibility = "public"

    closed: bool = sl.state(default=False)

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
            label=lambda hit: hit.title,
            summary=_result_description,
            detail=self._detail,
            overview=self._overview,
            page_size=request.page_size,
            title=L(t"Search results"),
            empty=L(t"No results match this query."),
            copy=sp.LoadingCopy(
                loading=L(t"Loading search results…"),
                failed=L(t"Could not load search results."),
                retry=L(t"Retry"),
            ),
        )
        self._browser.window.replace(self._source.initial_loaded())

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

    @property
    def detail_index(self) -> int | None:
        """Return the opened page index for compatibility with the historical view."""
        if self._browser.opened is None:
            return None
        identity = _hit_identity(self._browser.opened)
        return next((index for index, hit in enumerate(self.hits) if _hit_identity(hit) == identity), None)

    @detail_index.setter
    def detail_index(self, index: int | None) -> None:
        if index is None:
            self._browser.opened = None
            self._browser._detail_value = None
            return
        hit = self.hits[index]
        self._browser.opened = hit
        self._browser._detail_value = self._detail(hit)

    def _visible_window(self) -> sl.sources.LoadedWindow[SearchHit]:
        state = self._browser.window.status
        if isinstance(state, sl.resources.Ready):
            return state.value
        if isinstance(state, sl.resources.Pending | sl.resources.Failed) and state.previous is not None:
            return state.previous.value
        message = "search browser has no visible window"
        raise sl.errors.LayoutInvariantError(message)

    def _detail(self, hit: SearchHit) -> _SearchDetail:
        return _SearchDetail(
            hit,
            load_build=self._load_build,
            render_build=self._render_build,
        )

    def _overview(self, loaded: sl.sources.LoadedWindow[SearchHit]) -> sl.LayoutNode[sl.ComponentsV2Target] | tuple[()]:
        warnings = self._source.page_for(loaded).warnings
        if not warnings:
            return ()
        return sl.note("\n".join(f"⚠ {escape_markdown(item)}" for item in warnings))

    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        if self.closed:
            return (
                sl.section(
                    sl.heading(L(t"Search closed")),
                    sl.truncate(sl.paragraph(L(t"This search is closed."))),
                ),
            )
        return (
            self.boundary(self._browser, key="results"),
            sl.action_controls(sl.action_control(L(t"Close"), self._close, key="close"), key="search-actions"),
        )

    async def _close(self, event: sl.PressEvent) -> None:
        self.closed = True
        await event.finish()


def _build_id(hit: SearchHit) -> int | None:
    if isinstance(hit, RecordSearchHit):
        return hit.build_id
    if isinstance(hit, BuildSearchHit) and hit.source_id.isdigit():
        return int(hit.source_id)
    return None


def _detail_title(hit: SearchHit) -> str:
    return hit.title


def _metadata_label(kind: str) -> sl.TextLike:
    match kind:
        case "restriction":
            return L(t"Restriction")
        case "pattern":
            return L(t"Pattern")
        case "showcase":
            return L(t"Showcase tag")
        case "creator":
            return L(t"Creator")
        case "version":
            return L(t"Version")
        case "tag":
            return L(t"Tag")
        case _:
            return kind


def _result_description(hit: SearchHit) -> sl.TextLike:
    if isinstance(hit, RecordSearchHit):
        record_class = hit.record_class
        build_title = hit.build_title
        return L(t"Record · {record_class} · {build_title}")
    if isinstance(hit, BuildSearchHit):
        status = hit.status
        return L(t"Build · {status}")
    return _metadata_label(hit.metadata_kind)


def _detail_text(hit: SearchHit) -> sl.TextLike:
    if isinstance(hit, RecordSearchHit):
        tags = ", ".join(escape_markdown(tag) for tag in hit.tags)
        build_title = hit.build_title
        build_id = hit.build_id
        record_class = hit.record_class
        version_scope = hit.version_scope
        fields = L(t"**Build**\n{build_title} ({build_id})\n**Class**\n{record_class} · {version_scope}")
        if hit.metrics:
            metrics = ", ".join(
                f"{escape_markdown(key)}: {escape_markdown(str(value))}" for key, value in hit.metrics.items()
            )
            metrics = sl.md(metrics)
            fields = L(t"{fields}\n**Metrics**\n{metrics}")
        description = hit.subtitle or ""
    elif isinstance(hit, BuildSearchHit):
        tags = ", ".join(escape_markdown(tag) for tag in hit.tags)
        status = hit.status
        fields = L(t"**Status**\n{status}")
        description = hit.description or ""
    else:
        tags = ", ".join(escape_markdown(alias) for alias in hit.aliases)
        kind = _metadata_label(hit.metadata_kind)
        fields = L(t"**Kind**\n{kind}")
        description = hit.description or ""
    if tags:
        tags = sl.md(tags)
        fields = L(t"{fields}\n**Tags**\n{tags}")
    return L(t"{description}\n{fields}")
