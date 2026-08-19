"""Mounted squid-layouts component for interactive search results."""

from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any

import discord
from discord.utils import escape_markdown

import squid_layouts as sl
from squid.bot.i18n import t
from squid.bot.ui import DISCORD_GREEN, create_mount
from squid.builds.domain import Build
from squid.core.i18n import _
from squid.core.pagination import PageAnchor
from squid.search.domain import BuildSearchHit, MetadataSearchHit, RecordSearchHit, SearchHit, SearchPage, SearchRequest

if TYPE_CHECKING:
    from squid.search.application import SearchService


BuildLoader = Callable[[int], Awaitable[Build | None]]
BuildRenderer = Callable[[Build], Awaitable[sl.primitives.Node]]


class SearchResultsView(sl.Component):
    """A cursor-driven search surface rendered and owned by a squid-layouts mount.

    The historical class name is retained because commands and extensions import it, but the
    object is now a portable component rather than a discord.py LayoutView. Compatibility
    helpers at the bottom keep structural callers useful while the command path uses mount.
    """

    detail_index: int | None = sl.state(None)
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
        self._service = service
        self._request = request
        self._page = page
        self._author_id = author_id
        self.locale = locale
        self._load_build = load_build
        self._render_build = render_build
        self._build_node: sl.primitives.Node | None = None
        self._compat_mount: sl.discord.Mount | None = None
        self._compat_disabled = False
        self._bound_message: discord.Message | None = None

    @property
    def request(self) -> SearchRequest:
        """Return the request currently displayed by the component."""
        return self._request

    @property
    def page(self) -> SearchPage:
        """Return the current result page."""
        return self._page

    @property
    def hits(self) -> tuple[SearchHit, ...]:
        """Return the results currently displayed."""
        return self._page.hits

    @property
    def can_go_back(self) -> bool:
        """Return whether an earlier page exists."""
        return self._page.prev is not None

    @property
    def can_go_forward(self) -> bool:
        """Return whether a later page exists."""
        return self._page.next is not None

    def render(self) -> Sequence[sl.LayoutNode]:
        """Describe the current search page or selected result."""
        if self.closed:
            return [sl.primitives.card(t(self.locale, _("Search closed")), t(self.locale, _("This search is closed.")))]
        if self.detail_index is not None:
            return self._render_detail(self.hits[self.detail_index])
        return self._render_results()

    def _render_results(self) -> Sequence[sl.LayoutNode]:
        lines = [_result_line(index, hit, self.locale) for index, hit in enumerate(self.hits, start=1)]
        if not lines:
            lines.append(t(self.locale, _("No results match this query.")))
        page_count = max(1, -(-self._page.total // self._request.page_size))
        body = t(
            self.locale,
            _("{lines}\n-# Page {page} of {pages}"),
            lines=" ".join(lines),
            page=self._request.offset // self._request.page_size + 1,
            pages=page_count,
        )
        if self._page.warnings:
            body += "\n" + "\n".join(f"-# ⚠ {escape_markdown(item)}" for item in self._page.warnings)
        nodes: list[sl.LayoutNode] = [
            sl.primitives.card(t(self.locale, _("Search results")), body, accent=DISCORD_GREEN)
        ]
        if self.hits:
            nodes.append(
                sl.primitives.SelectMenu(
                    tuple(
                        sl.primitives.Option(
                            hit.title[:100],
                            str(index),
                            _result_description(hit, self.locale)[:100],
                        )
                        for index, hit in enumerate(self.hits)
                    ),
                    self._select_result,
                    "select-result",
                    placeholder=t(self.locale, _("Choose a result to inspect")),
                )
            )
        nodes.append(sl.primitives.Row(self._navigation_buttons()))
        return nodes

    def _render_detail(self, hit: SearchHit) -> Sequence[sl.LayoutNode]:
        detail: sl.LayoutNode = self._build_node or sl.primitives.card(
            _detail_title(hit), _detail_text(hit, self.locale), accent=DISCORD_GREEN
        )
        buttons: list[sl.primitives.Button] = []
        if _build_id(hit) is not None:
            buttons.append(
                sl.primitives.Button(
                    t(self.locale, _("View build")),
                    self._open_build,
                    "open-build",
                    style=sl.primitives.ActionStyle.PRIMARY,
                )
            )
        buttons.extend(
            (
                sl.primitives.Button(t(self.locale, _("Back")), self._back, "back"),
                sl.primitives.Button(t(self.locale, _("Close")), self._close, "close"),
            )
        )
        return [detail, sl.primitives.Row(tuple(buttons))]

    def _navigation_buttons(self) -> tuple[sl.primitives.Button, ...]:
        return (
            sl.primitives.Button(
                t(self.locale, _("Previous")),
                self._previous,
                "previous",
                disabled=not self.can_go_back,
            ),
            sl.primitives.Button(
                t(self.locale, _("Next")),
                self._next,
                "next",
                disabled=not self.can_go_forward,
            ),
            sl.primitives.Button(
                t(self.locale, _("Close")),
                self._close,
                "close",
                style=sl.primitives.ActionStyle.SECONDARY,
            ),
        )

    async def _select_result(self, event: sl.SelectionEvent) -> None:
        self.detail_index = int(event.values[0])
        self._build_node = None

    async def _open_build(self, event: sl.PressEvent) -> None:
        hit = self.hits[self.detail_index or 0]
        build_id = _build_id(hit)
        if build_id is None or self._load_build is None or self._render_build is None:
            await event.notice(t(self.locale, _("That build is no longer available.")))
            return
        build = await self._load_build(build_id)
        if build is None:
            await event.notice(t(self.locale, _("That build is no longer available.")))
            return
        self._build_node = await self._render_build(build)

    async def _previous(self, event: sl.PressEvent) -> None:
        await self._go_to(self._page.prev)

    async def _next(self, event: sl.PressEvent) -> None:
        await self._go_to(self._page.next)

    async def _go_to(self, anchor: PageAnchor | None) -> None:
        if anchor is None or anchor.offset is None:
            return
        self._request = self._request.__class__(
            self._request.query,
            scope=self._request.scope,
            mode=self._request.mode,
            sort=self._request.sort,
            offset=anchor.offset,
            page_size=self._request.page_size,
        )
        self._page = await self._service.search(self._request)
        self.detail_index = None
        self._build_node = None

    async def _back(self, event: sl.PressEvent) -> None:
        self.detail_index = None
        self._build_node = None

    async def _close(self, event: sl.PressEvent) -> None:
        self.closed = True
        await event.finish()

    def mount(self) -> sl.discord.Mount:
        """Create the mount used by the command transport."""
        return create_mount(self, locale=self.locale, timeout=180, lock_to=self._author_id)

    def bind_message(self, message: discord.Message) -> None:
        """Bind a compatibility message; production mounts bind through Mount.bind."""
        self._bound_message = message

    async def on_timeout(self) -> None:
        """Disable compatibility-rendered controls when an old caller owns the message."""
        self._compat_disabled = True
        if self._bound_message is not None:
            await self._bound_message.edit(view=self._compat_view())

    # Compatibility helpers for structural callers that used to inspect a discord.py view.
    def _compat_view(self) -> discord.ui.LayoutView:
        if self._compat_mount is None:
            self._compat_mount = self.mount()
        return self._compat_mount.build_view(disabled=self._compat_disabled)

    def to_components(self) -> list[dict[str, Any]]:
        """Return the current rendered Components V2 payload for inspection."""
        return self._compat_view().to_components()

    def walk_children(self) -> list[discord.ui.Item[Any]]:
        """Walk the compatibility-rendered view."""
        return list(self._compat_view().walk_children())

    def render_detail(self, hit: SearchHit) -> None:
        """Select a result for compatibility callers."""
        self.detail_index = self.hits.index(hit)
        self._build_node = None


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


def _result_line(index: int, hit: SearchHit, locale: str | None) -> str:
    subtitle = ""
    if isinstance(hit, RecordSearchHit):
        subtitle = f" — {hit.build_title}"
    elif isinstance(hit, BuildSearchHit) and hit.description:
        subtitle = f" — {hit.description}"
    elif isinstance(hit, MetadataSearchHit):
        subtitle = f" — {_metadata_label(hit.metadata_kind, locale)}"
    return f"\n**{index}. {escape_markdown(hit.title)}**{escape_markdown(subtitle)}"


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
