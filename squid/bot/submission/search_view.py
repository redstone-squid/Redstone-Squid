"""Mounted squid-layouts component for interactive search results."""

from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import TYPE_CHECKING

from discord.utils import escape_markdown

import squid_layouts as sl
from squid.bot.i18n import t
from squid.bot.ui import DISCORD_GREEN, create_mount
from squid.builds.domain import Build
from squid.core.i18n import _
from squid.search.domain import BuildSearchHit, RecordSearchHit, SearchHit, SearchPage, SearchRequest
from squid_layouts.sources import window_fingerprint

if TYPE_CHECKING:
    from squid.search.application import SearchService


BuildLoader = Callable[[int], Awaitable[Build | None]]
BuildRenderer = Callable[[Build], Awaitable[sl.LayoutNode]]


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


class _SearchDetail(sl.Component):
    _build_node: sl.LayoutNode | None = sl.state(None, persist=False, opaque=True)

    def __init__(
        self,
        hit: SearchHit,
        *,
        locale: str | None,
        load_build: BuildLoader | None,
        render_build: BuildRenderer | None,
    ) -> None:
        self.hit = hit
        self.locale = locale
        self._load_build = load_build
        self._render_build = render_build

    def render(self) -> tuple[sl.LayoutNode, ...]:
        detail = self._build_node or sl.section(
            sl.heading(_detail_title(self.hit)),
            sl.truncate(sl.paragraph(_detail_text(self.hit, self.locale))),
            accent=DISCORD_GREEN,
        )
        build_id = _build_id(self.hit)
        return (
            detail,
            *(
                (
                    sl.actions(
                        sl.action(t(self.locale, _("View build")), self._open_build, key="open-build"),
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
            await event.notice(t(self.locale, _("That build is no longer available.")))
            return
        build = await self._load_build(build_id)
        if build is None:
            await event.notice(t(self.locale, _("That build is no longer available.")))
            return
        self._build_node = await self._render_build(build)


class SearchResultsView(sl.Component):
    """Compatibility wrapper around the shared resource-backed Browser pattern."""

    closed: bool = sl.state(default=False)

    def __init__(
        self,
        service: SearchService,
        request: SearchRequest,
        page: SearchPage,
        *,
        author_id: int,
        locale: str | None = None,
        load_build: BuildLoader | None = None,
        render_build: BuildRenderer | None = None,
    ) -> None:
        self._source = _SearchSource(service, request, page)
        self._author_id = author_id
        self.locale = locale
        self._load_build = load_build
        self._render_build = render_build
        self._browser = sl.patterns.Browser(
            self._source,
            key="search",
            identity=_hit_identity,
            label=lambda hit: hit.title,
            summary=lambda hit: _result_description(hit, self.locale),
            detail=self._detail,
            overview=self._overview,
            page_size=request.page_size,
            title=t(self.locale, _("Search results")),
            empty=t(self.locale, _("No results match this query.")),
            loading=t(self.locale, _("Loading search results…")),
            load_failed=t(self.locale, _("Could not load search results.")),
            retry=t(self.locale, _("Retry")),
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
            locale=self.locale,
            load_build=self._load_build,
            render_build=self._render_build,
        )

    def _overview(self, loaded: sl.sources.LoadedWindow[SearchHit]) -> sl.LayoutNode | tuple[()]:
        warnings = self._source.page_for(loaded).warnings
        if not warnings:
            return ()
        return sl.note("\n".join(f"⚠ {escape_markdown(item)}" for item in warnings))

    def render(self) -> tuple[sl.LayoutNode, ...]:
        if self.closed:
            return (
                sl.section(
                    sl.heading(t(self.locale, _("Search closed"))),
                    sl.truncate(sl.paragraph(t(self.locale, _("This search is closed.")))),
                ),
            )
        return (
            self.boundary(self._browser, key="results"),
            sl.primitives.Row((sl.primitives.Button(t(self.locale, _("Close")), self._close, "close"),)),
        )

    async def _close(self, event: sl.PressEvent) -> None:
        self.closed = True
        await event.finish()

    def mount(self, *, source: sl.discord.host.HostSource) -> sl.discord.Mount:
        """Create the mount used by the command transport."""
        return create_mount(
            self, source=source, access=sl.discord.Owner(self._author_id), locale=self.locale, timeout=180
        )


def _build_id(hit: SearchHit) -> int | None:
    if isinstance(hit, RecordSearchHit):
        return hit.build_id
    if isinstance(hit, BuildSearchHit) and hit.source_id.isdigit():
        return int(hit.source_id)
    return None


def _detail_title(hit: SearchHit) -> str:
    return hit.title


_METADATA_LABELS = {
    "restriction": _("Restriction"),
    "pattern": _("Pattern"),
    "showcase": _("Showcase tag"),
    "creator": _("Creator"),
    "version": _("Version"),
    "tag": _("Tag"),
}


def _metadata_label(kind: str, locale: str | None) -> str:
    label = _METADATA_LABELS.get(kind)
    return t(locale, label) if label is not None else kind


def _result_description(hit: SearchHit, locale: str | None) -> str:
    if isinstance(hit, RecordSearchHit):
        return f"Record · {hit.record_class} · {hit.build_title}"
    if isinstance(hit, BuildSearchHit):
        return f"Build · {hit.status}"
    return _metadata_label(hit.metadata_kind, locale)


def _detail_text(hit: SearchHit, locale: str | None) -> str:
    if isinstance(hit, RecordSearchHit):
        tags = ", ".join(escape_markdown(tag) for tag in hit.tags)
        fields = t(
            locale,
            _("**Build**\n{build_title} ({build_id})\n**Class**\n{record_class} · {version_scope}"),
            build_title=escape_markdown(hit.build_title),
            build_id=hit.build_id,
            record_class=escape_markdown(hit.record_class),
            version_scope=escape_markdown(hit.version_scope),
        )
        if hit.metrics:
            metrics = ", ".join(
                f"{escape_markdown(key)}: {escape_markdown(str(value))}" for key, value in hit.metrics.items()
            )
            fields += t(locale, _("\n**Metrics**\n{metrics}"), metrics=metrics)
        description = hit.subtitle or ""
    elif isinstance(hit, BuildSearchHit):
        tags = ", ".join(escape_markdown(tag) for tag in hit.tags)
        fields = t(locale, _("**Status**\n{status}"), status=escape_markdown(hit.status))
        description = hit.description or ""
    else:
        tags = ", ".join(escape_markdown(alias) for alias in hit.aliases)
        fields = t(locale, _("**Kind**\n{kind}"), kind=escape_markdown(_metadata_label(hit.metadata_kind, locale)))
        description = hit.description or ""
    if tags:
        fields += t(locale, _("\n**Tags**\n{tags}"), tags=tags)
    return f"{escape_markdown(description)}\n{fields}"
