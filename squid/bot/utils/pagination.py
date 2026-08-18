"""A shared paginator for commands whose answer is a list.

Three commands had grown three different answers to "the list is too long": truncate it,
print the first N, or print all of it and let Discord cut the message off (audit C6). This is
the one answer, for lists that are already in memory.

It deliberately does not fetch. Every caller here holds its whole list before rendering, and a
paginator that also drives a cursor query is a different, larger thing —
`squid.bot.submission.search_view.SearchResultsView` is the shape to copy when a call site
needs one.
"""

from collections.abc import Sequence
from typing import Any, override

import discord
from discord.ext.commands import Context

from squid.bot.errors import ExpiringLayoutView
from squid.bot.i18n import t
from squid.bot.utils.components import DISCORD_GREEN, card_container, edit_interaction_layout, no_mentions
from squid.core.i18n import _

DEFAULT_PAGE_SIZE = 10
"""Entries per page. Chosen so a page of two-line entries still fits a card comfortably."""


class ListPaginator(ExpiringLayoutView):
    """A card holding one page of a list, plus the controls to walk it."""

    def __init__(
        self,
        title: str,
        entries: Sequence[str],
        *,
        author_id: int,
        empty: str,
        locale: str | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
        accent_colour: int = DISCORD_GREEN,
        timeout: float = 180,
    ) -> None:
        """Build a paginator over pre-rendered entries.

        Args:
            title: The card heading.
            entries: One rendered block of text per list item.
            author_id: Who may use the controls.
            empty: What to say instead when there are no entries at all.
            locale: The locale to translate the controls and footer in.
            page_size: How many entries a page holds.
            accent_colour: The card's accent.
            timeout: How long the controls stay live.
        """
        super().__init__(timeout=timeout)
        self.title = title
        self.entries = entries
        self.empty = empty
        self.locale = locale
        self.page_size = max(1, page_size)
        self.accent_colour = accent_colour
        self._author_id = author_id
        self.page = 0
        self.render()

    @property
    def page_count(self) -> int:
        """How many pages the entries fill, never fewer than one."""
        return max(1, -(-len(self.entries) // self.page_size))

    @override
    async def interaction_check(self, interaction: discord.Interaction[discord.Client], /) -> bool:  # pyright: ignore [reportIncompatibleMethodOverride]  # pyrefly: ignore[bad-override]
        if interaction.user.id == self._author_id:
            return True
        await interaction.response.send_message(
            t(self.locale, _("These list controls belong to someone else.")),
            ephemeral=True,
            allowed_mentions=no_mentions(),
        )
        return False

    def render(self) -> None:
        """Lay out the current page and the controls that fit it."""
        self.clear_items()
        start = self.page * self.page_size
        shown = self.entries[start : start + self.page_size]
        footer = None
        if self.page_count > 1:
            footer = t(
                self.locale,
                _("Page {page} of {pages} · {total} in total"),
                page=self.page + 1,
                pages=self.page_count,
                total=len(self.entries),
            )
        self.add_item(
            card_container(
                self.title,
                "\n\n".join(shown) if shown else self.empty,
                accent_colour=self.accent_colour,
                footer=footer,
            )
        )
        # A single page has nothing to page through, and a row of two dead buttons reads as a
        # broken control rather than as an absent one.
        if self.page_count > 1:
            self.add_item(discord.ui.ActionRow(_PreviousPageButton(self), _NextPageButton(self)))

    async def go_to(self, interaction: discord.Interaction[Any], page: int) -> None:
        """Move to a page and redraw in place."""
        self.page = min(max(page, 0), self.page_count - 1)
        self.render()
        await edit_interaction_layout(interaction, self)

    async def send(self, ctx: Context[Any]) -> None:
        """Send the first page and bind it, so expiry can disable the controls."""
        message = await ctx.send(view=self, allowed_mentions=no_mentions())
        self.bind_message(message)


class _PreviousPageButton(discord.ui.Button[ListPaginator]):
    def __init__(self, paginator: ListPaginator) -> None:
        super().__init__(
            label=t(paginator.locale, _("Previous")),
            style=discord.ButtonStyle.secondary,
            disabled=paginator.page == 0,
        )
        self.paginator = paginator

    @override
    async def callback(self, interaction: discord.Interaction[Any]) -> None:
        await self.paginator.go_to(interaction, self.paginator.page - 1)


class _NextPageButton(discord.ui.Button[ListPaginator]):
    def __init__(self, paginator: ListPaginator) -> None:
        super().__init__(
            label=t(paginator.locale, _("Next")),
            style=discord.ButtonStyle.secondary,
            disabled=paginator.page >= paginator.page_count - 1,
        )
        self.paginator = paginator

    @override
    async def callback(self, interaction: discord.Interaction[Any]) -> None:
        await self.paginator.go_to(interaction, self.paginator.page + 1)
