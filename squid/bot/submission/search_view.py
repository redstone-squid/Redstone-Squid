"""Interactive Components V2 search results."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

import discord
from discord.utils import escape_markdown

from squid.bot.errors import ErrorHandledLayoutView
from squid.bot.i18n import t
from squid.bot.utils.components import DISCORD_GREEN, edit_interaction_layout, no_mentions
from squid.core.i18n import _
from squid.search.domain import BuildSearchHit, MetadataSearchHit, RecordSearchHit, SearchHit, SearchPage, SearchRequest

if TYPE_CHECKING:
    from squid.search.application import SearchService


class SearchResultsView(ErrorHandledLayoutView):
    """A message-bound cursor paginator with inline result details."""

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
        self._cursor_history: list[str | None] = [request.cursor]
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
        lines = [_result_line(index, hit) for index, hit in enumerate(self._page.hits, start=1)]
        if not lines:
            lines.append(t(self.locale, _("No results match this query.")))
        warning = "\n".join(f"-# ⚠ {escape_markdown(item)}" for item in self._page.warnings)
        page_number = len(self._cursor_history)
        body = t(
            self.locale,
            _("## Search results\n{lines}\n-# Page {page}"),
            lines=" ".join(lines),
            page=page_number,
        )
        if warning:
            body += f"\n{warning}"
        self.add_item(discord.ui.Container(discord.ui.TextDisplay(body), accent_colour=DISCORD_GREEN))

        result_row = discord.ui.ActionRow()
        for index, _hit in enumerate(self._page.hits):
            result_row.add_item(SearchResultButton(self, index))
        if self._page.hits:
            self.add_item(result_row)

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
        row.add_item(SearchBackButton(self))
        row.add_item(SearchStopButton(self))
        self.add_item(row)

    def hit_at(self, index: int) -> SearchHit:
        """Return a result on the current page."""
        return self._page.hits[index]

    @property
    def can_go_back(self) -> bool:
        """Return whether a previous cursor is available."""
        return len(self._cursor_history) > 1

    @property
    def can_go_forward(self) -> bool:
        """Return whether a next cursor is available."""
        return self._page.next_cursor is not None

    async def next_page(self) -> None:
        """Fetch and render the next cursor page."""
        if self._page.next_cursor is None:
            return
        self._cursor_history.append(self._page.next_cursor)
        self._request = SearchRequest(
            self._request.query,
            self._request.scope,
            self._request.mode,
            self._request.page_size,
            self._page.next_cursor,
            self._request.sort,
        )
        self._page = await self._service.search(self._request)
        self.render_results()

    async def previous_page(self) -> None:
        """Fetch and render the previous cursor page."""
        if len(self._cursor_history) == 1:
            return
        self._cursor_history.pop()
        cursor = self._cursor_history[-1]
        self._request = SearchRequest(
            self._request.query,
            self._request.scope,
            self._request.mode,
            self._request.page_size,
            cursor,
            self._request.sort,
        )
        self._page = await self._service.search(self._request)
        self.render_results()

    def disable_controls(self) -> None:
        """Disable every interactive component."""
        for child in self.walk_children():
            if isinstance(child, discord.ui.Button | discord.ui.Select):
                child.disabled = True
        self.stop()


class SearchResultButton(discord.ui.Button[SearchResultsView]):
    """Open one result without sending another message."""

    def __init__(self, view: SearchResultsView, index: int) -> None:
        super().__init__(label=str(index + 1), style=discord.ButtonStyle.secondary)
        self._search_view = view
        self._index = index

    @override
    async def callback(self, interaction: discord.Interaction[discord.Client]) -> None:
        self._search_view.render_detail(self._search_view.hit_at(self._index))
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
        super().__init__(label=t(view.locale, _("Stop")), style=discord.ButtonStyle.danger)
        self._search_view = view

    @override
    async def callback(self, interaction: discord.Interaction[discord.Client]) -> None:
        self._search_view.disable_controls()
        await edit_interaction_layout(interaction, self._search_view)


def _result_line(index: int, hit: SearchHit) -> str:
    subtitle = ""
    if isinstance(hit, RecordSearchHit):
        subtitle = f" — {hit.build_title}"
    elif isinstance(hit, BuildSearchHit) and hit.description:
        subtitle = f" — {hit.description}"
    elif isinstance(hit, MetadataSearchHit):
        subtitle = f" — {hit.metadata_kind}"
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
                f"{escape_markdown(str(key))}: {escape_markdown(str(value))}" for key, value in hit.metrics.items()
            )
            fields += t(locale, _("\n**Metrics**\n{metrics}"), metrics=metrics)
        description = hit.subtitle or ""
    elif isinstance(hit, BuildSearchHit):
        tags = ", ".join(escape_markdown(tag) for tag in hit.tags)
        fields = t(locale, _("**Status**\n{status}"), status=escape_markdown(hit.status))
        description = hit.description or ""
    else:
        tags = ", ".join(escape_markdown(alias) for alias in hit.aliases)
        fields = t(locale, _("**Kind**\n{kind}"), kind=escape_markdown(hit.metadata_kind))
        description = hit.description or ""
    if tags:
        fields += t(locale, _("\n**Tags**\n{tags}"), tags=tags)
    return f"## {escape_markdown(hit.title)}\n{escape_markdown(description)}\n{fields}"
