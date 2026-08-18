"""Interactive Components V2 search results."""

from __future__ import annotations

import math
from dataclasses import replace
from typing import TYPE_CHECKING, Any, cast, override

import discord
from discord.utils import escape_markdown

from squid.bot.errors import ExpiringLayoutView
from squid.bot.i18n import t
from squid.bot.utils.components import DISCORD_GREEN, edit_interaction_layout, no_mentions
from squid.core.i18n import _
from squid.core.pagination import PageAnchor
from squid.search.domain import BuildSearchHit, MetadataSearchHit, RecordSearchHit, SearchHit, SearchPage, SearchRequest

if TYPE_CHECKING:
    from squid.search.application import SearchService


class SearchResultsView(ExpiringLayoutView):
    """A message-bound offset paginator with inline result details."""

    def __init__(
        self,
        service: SearchService,
        request: SearchRequest,
        page: SearchPage,
        *,
        author_id: int,
        locale: str | None = None,
    ) -> None:
        super().__init__(timeout=180)
        self._service = service
        self._request = request
        self._page = page
        self._author_id = author_id
        self.locale = locale
        self.render_results()

    @override
    async def interaction_check(self, interaction: discord.Interaction[discord.Client], /) -> bool:
        if interaction.user.id == self._author_id:
            return True
        await interaction.response.send_message(
            t(self.locale, _("These search controls belong to someone else.")),
            ephemeral=True,
            allowed_mentions=no_mentions(),
        )
        return False

    def render_results(self) -> None:
        """Render the current result page and its controls."""
        self.clear_items()
        lines = [_result_line(index, hit, self.locale) for index, hit in enumerate(self._page.hits, start=1)]
        if not lines:
            lines.append(t(self.locale, _("No results match this query.")))
        warning = "\n".join(f"-# ⚠ {escape_markdown(item)}" for item in self._page.warnings)
        body = t(
            self.locale,
            _("## Search results\n{lines}\n-# Page {page} of {pages}"),
            lines=" ".join(lines),
            page=self._request.offset // self._request.page_size + 1,
            pages=max(1, math.ceil(self._page.total / self._request.page_size)),
        )
        if warning:
            body += f"\n{warning}"
        self.add_item(discord.ui.Container(discord.ui.TextDisplay(body), accent_colour=DISCORD_GREEN))

        if self._page.hits:
            self.add_item(discord.ui.ActionRow(SearchResultSelect(self)))

        controls = discord.ui.ActionRow()
        controls.add_item(SearchPreviousButton(self))
        controls.add_item(SearchNextButton(self))
        controls.add_item(SearchStopButton(self))
        self.add_item(controls)

    def render_detail(self, hit: SearchHit) -> None:
        """Replace the list with one result's searchable details."""
        self.clear_items()
        self.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay(_detail_text(hit, self.locale)),
                accent_colour=DISCORD_GREEN,
            )
        )
        row = discord.ui.ActionRow()
        build_id = _build_id(hit)
        if build_id is not None:
            row.add_item(SearchOpenBuildButton(self, build_id))
        row.add_item(SearchBackButton(self))
        row.add_item(SearchStopButton(self))
        self.add_item(row)

    def hit_at(self, index: int) -> SearchHit:
        """Return a result on the current page."""
        return self._page.hits[index]

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

    async def next_page(self) -> None:
        """Fetch and render the following page."""
        await self._go_to(self._page.next)

    async def previous_page(self) -> None:
        """Fetch and render the preceding page."""
        await self._go_to(self._page.prev)

    async def _go_to(self, anchor: PageAnchor | None) -> None:
        if anchor is None or anchor.offset is None:
            return
        offset = anchor.offset
        # `replace` rather than positional reconstruction: the visibility policy and every other
        # field the caller set have to survive paging.
        self._request = replace(self._request, offset=offset)
        self._page = await self._service.search(self._request)
        self.render_results()

    def disable_controls(self) -> None:
        """Disable every interactive component."""
        for child in self.walk_children():
            if isinstance(child, discord.ui.Button | discord.ui.Select):
                child.disabled = True
        self.stop()


class SearchResultSelect(discord.ui.Select[SearchResultsView]):
    """Choose a result by name rather than correlating numbered buttons."""

    def __init__(self, view: SearchResultsView) -> None:
        options = [
            discord.SelectOption(
                label=hit.title[:100],
                value=str(index),
                description=_result_description(hit, view.locale)[:100],
            )
            for index, hit in enumerate(view.hits)
        ]
        super().__init__(placeholder=t(view.locale, _("Choose a result to inspect")), options=options)
        self._search_view = view

    @override
    async def callback(self, interaction: discord.Interaction[discord.Client]) -> None:
        self._search_view.render_detail(self._search_view.hit_at(int(self.values[0])))
        await edit_interaction_layout(interaction, self._search_view)


class SearchOpenBuildButton(discord.ui.Button[SearchResultsView]):
    def __init__(self, view: SearchResultsView, build_id: int) -> None:
        super().__init__(label=t(view.locale, _("View build")), style=discord.ButtonStyle.primary)
        self._search_view = view
        self._build_id = build_id

    @override
    async def callback(self, interaction: discord.Interaction[discord.Client]) -> None:
        client = cast(Any, interaction.client)
        build = await client.services.build_queries.get(self._build_id)
        if build is None:
            await interaction.response.send_message(
                t(self._search_view.locale, _("That build is no longer available.")),
                ephemeral=True,
                allowed_mentions=no_mentions(),
            )
            return
        self._search_view.clear_items()
        self._search_view.add_item(await client.for_build(build).render_container())
        row = discord.ui.ActionRow()
        row.add_item(SearchBackButton(self._search_view))
        row.add_item(SearchStopButton(self._search_view))
        self._search_view.add_item(row)
        await edit_interaction_layout(interaction, self._search_view)


class SearchPreviousButton(discord.ui.Button[SearchResultsView]):
    def __init__(self, view: SearchResultsView) -> None:
        super().__init__(label=t(view.locale, _("Previous")), disabled=not view.can_go_back)
        self._search_view = view

    @override
    async def callback(self, interaction: discord.Interaction[discord.Client]) -> None:
        await self._search_view.previous_page()
        await edit_interaction_layout(interaction, self._search_view)


class SearchNextButton(discord.ui.Button[SearchResultsView]):
    def __init__(self, view: SearchResultsView) -> None:
        super().__init__(label=t(view.locale, _("Next")), disabled=not view.can_go_forward)
        self._search_view = view

    @override
    async def callback(self, interaction: discord.Interaction[discord.Client]) -> None:
        await self._search_view.next_page()
        await edit_interaction_layout(interaction, self._search_view)


class SearchBackButton(discord.ui.Button[SearchResultsView]):
    def __init__(self, view: SearchResultsView) -> None:
        super().__init__(label=t(view.locale, _("Back")))
        self._search_view = view

    @override
    async def callback(self, interaction: discord.Interaction[discord.Client]) -> None:
        self._search_view.render_results()
        await edit_interaction_layout(interaction, self._search_view)


class SearchStopButton(discord.ui.Button[SearchResultsView]):
    def __init__(self, view: SearchResultsView) -> None:
        super().__init__(label=t(view.locale, _("Close")), style=discord.ButtonStyle.secondary)
        self._search_view = view

    @override
    async def callback(self, interaction: discord.Interaction[discord.Client]) -> None:
        self._search_view.disable_controls()
        await edit_interaction_layout(interaction, self._search_view)


def _build_id(hit: SearchHit) -> int | None:
    if isinstance(hit, RecordSearchHit):
        return hit.build_id
    if isinstance(hit, BuildSearchHit) and hit.source_id.isdigit():
        return int(hit.source_id)
    return None


_METADATA_LABELS = {
    "restriction": _("Restriction"),
    "pattern": _("Pattern"),
    "showcase": _("Showcase tag"),
    "creator": _("Creator"),
    "version": _("Version"),
    "tag": _("Tag"),
}
"""What a taxonomy result calls itself, in the reader's vocabulary.

The index stores the internal kind. Naming these here rather than title-casing the stored
value keeps the mapping translatable, and leaves an unknown kind rendering as itself instead
of as a blank.
"""


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
            _("**Build**\n{build_title} (`{build_id}`)\n**Class**\n{record_class} · {version_scope}"),
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
    return f"## {escape_markdown(hit.title)}\n{escape_markdown(description)}\n{fields}"
