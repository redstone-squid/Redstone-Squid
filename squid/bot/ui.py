"""squid-layouts glue: localized chrome, house colours, and the semantic layout presets.

This module is the bot's front door to the `squid_layouts` package. The package itself is
i18n-free by architecture rule, so every framework string is built here (where Babel extracts
`_()` markers) and passed in pre-translated through `Chrome`.

The layout helpers keep the exact signatures of their `squid.bot.utils.components`
predecessors, so call sites migrate by changing an import line.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import discord
from discord.ext.commands import Context

import squid_layouts as ui
from squid.bot.i18n import t
from squid.core.i18n import _

DISCORD_RED = 0xF04747
DISCORD_YELLOW = 0xFAA61A
DISCORD_GREEN = 0x43B581
DISCORD_BLUE = 0x5865F2
DISCORD_GREY = 0x4F545C

__all__ = [
    "DISCORD_BLUE",
    "DISCORD_GREEN",
    "DISCORD_GREY",
    "DISCORD_RED",
    "DISCORD_YELLOW",
    "CardField",
    "CardSection",
    "PagedList",
    "card_layout",
    "chrome_for",
    "create_mount",
    "error_layout",
    "help_layout",
    "info_layout",
    "link_layout",
    "send_component",
    "text_layout",
    "warning_layout",
]


@dataclass(frozen=True, slots=True)
class CardField:
    """A labelled value rendered inside a card."""

    name: str
    value: str


@dataclass(frozen=True, slots=True)
class CardSection:
    """A titled group of related values rendered inside a card."""

    title: str
    fields: Sequence[CardField]


def chrome_for(locale: str | None) -> ui.Chrome:
    """Build the framework's chrome strings for `locale`."""
    return ui.Chrome(
        and_n_more=lambda count: t(locale, _("…and {count} more."), count=count),
        see_attachment=t(locale, _("See attachment")),
        not_yours=t(locale, _("These list controls belong to someone else.")),
        previous=t(locale, _("Previous")),
        next=t(locale, _("Next")),
        page_footer=lambda page, pages: t(locale, _("Page {page} of {pages}"), page=page, pages=pages),
    )


def render_item(node: ui.Node, *, locale: str | None = None) -> discord.ui.Item[Any]:
    """Render one node to a detached item, for composition into a larger layout.

    The build card uses this: it renders as an engine-solved Container that callers (vote
    cards, search detail) then embed or extend.
    """
    view = ui.render_static([node], chrome=chrome_for(locale))
    item = view.children[0]
    view.remove_item(item)
    return item


async def _component_error_hook(interaction: discord.Interaction, error: Exception, source: str) -> None:
    # Imported lazily: errors.py -> utils.components -> this module would otherwise cycle.
    from squid.bot.errors import handle_interaction_error

    await handle_interaction_error(interaction, error, surface=f"component:{source}")


def create_mount(
    component: ui.Component,
    *,
    locale: str | None = None,
    chrome: ui.Chrome | None = None,
    timeout: float = 180,
    lock_to: int | None = None,
) -> ui.Mount:
    """A mount wired to the bot's chrome and shared interaction error handler."""
    return ui.Mount(
        component,
        chrome=chrome if chrome is not None else chrome_for(locale),
        timeout=timeout,
        lock_to=lock_to,
        on_error=_component_error_hook,
    )


async def send_component(
    ctx: Context[Any],
    component: ui.Component,
    *,
    locale: str | None = None,
    timeout: float = 180,
    lock_to: int | None = None,
    ephemeral: bool = False,
) -> ui.Mount:
    """Mount a component and send it as the reply to a command."""
    mount = create_mount(component, locale=locale, timeout=timeout, lock_to=lock_to)
    view = mount.build_view()
    message = await ctx.send(view=view, ephemeral=ephemeral, allowed_mentions=ui.deliver.no_mentions())
    mount.bind(message, view)
    return mount


class PagedList(ui.Component):
    """A card holding one page of a pre-rendered list, plus the controls to walk it.

    The reactive successor to `squid.bot.utils.pagination.ListPaginator`: count-based pages
    (a deliberate UX pin), author lock and expiry handled by the mount. It does not fetch —
    every caller holds its whole list before rendering.
    """

    page: int = ui.state(0)

    def __init__(
        self,
        title: str,
        entries: Sequence[str],
        *,
        empty: str,
        locale: str | None = None,
        page_size: int = 10,
        separator: str = "\n\n",
        accent_colour: int = DISCORD_GREEN,
    ) -> None:
        self.title = title
        self.entries = tuple(entries)
        self.empty = empty
        self.locale = locale
        self.page_size = max(1, page_size)
        self.separator = separator
        self.accent_colour = accent_colour

    @property
    def page_count(self) -> int:
        return max(1, -(-len(self.entries) // self.page_size))

    def render(self) -> Sequence[ui.Node]:
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
        nodes: list[ui.Node] = [
            ui.card(
                self.title,
                self.separator.join(shown) if shown else self.empty,
                accent=self.accent_colour,
                footer=footer,
            )
        ]
        # A single page has nothing to page through, and a row of two dead buttons reads as
        # a broken control rather than as an absent one.
        if self.page_count > 1:
            nodes.append(
                ui.Row(
                    (
                        ui.Button(
                            label=t(self.locale, _("Previous")),
                            on_click=self._previous,
                            key="prev",
                            disabled=self.page == 0,
                        ),
                        ui.Button(
                            label=t(self.locale, _("Next")),
                            on_click=self._next,
                            key="next",
                            disabled=self.page >= self.page_count - 1,
                        ),
                    )
                )
            )
        return nodes

    async def _previous(self, interaction: discord.Interaction) -> None:
        self.page = max(0, self.page - 1)

    async def _next(self, interaction: discord.Interaction) -> None:
        self.page = min(self.page + 1, self.page_count - 1)

    async def send(self, ctx: Context[Any], *, ephemeral: bool = False) -> ui.Mount:
        """Send the first page bound to a mount that owns paging, locking, and expiry."""
        return await send_component(
            ctx, self, locale=self.locale, lock_to=ctx.author.id if ctx.author else None, ephemeral=ephemeral
        )


def _fields(fields: Sequence[CardField]) -> tuple[ui.Field, ...]:
    return tuple(ui.Field(field.name, field.value) for field in fields)


def _groups(sections: Sequence[CardSection]) -> tuple[ui.FieldGroup, ...]:
    return tuple(ui.FieldGroup(section.title, _fields(section.fields)) for section in sections)


def card_layout(
    title: str,
    description: str | None = None,
    *,
    accent_colour: int = DISCORD_GREEN,
    fields: Sequence[CardField] = (),
    sections: Sequence[CardSection] = (),
    footer: str | None = None,
    media: Sequence[str] = (),
) -> discord.ui.LayoutView:
    """Create a standalone V2 card."""
    node = ui.card(
        title,
        description,
        accent=accent_colour,
        fields=_fields(fields),
        groups=_groups(sections),
        footer=footer,
        media=media,
    )
    return ui.render_static([node])


def text_layout(content: str, *, accent_colour: int | None = None) -> discord.ui.LayoutView:
    """Create a simple V2 text response."""
    return ui.render_static([ui.banner(content, accent=accent_colour)])


def error_layout(title: str, description: str | None) -> discord.ui.LayoutView:
    return card_layout(title, f":x: {description or ''}", accent_colour=DISCORD_RED)


def warning_layout(title: str, description: str | None) -> discord.ui.LayoutView:
    return card_layout(f":warning: {title}", description, accent_colour=DISCORD_YELLOW)


def info_layout(title: str, description: str | None) -> discord.ui.LayoutView:
    return card_layout(title, description, accent_colour=DISCORD_GREEN)


def help_layout(
    title: str,
    description: str | None,
    *,
    sections: Sequence[CardSection] = (),
    footer: str | None = None,
) -> discord.ui.LayoutView:
    return card_layout(title, description, accent_colour=DISCORD_BLUE, sections=sections, footer=footer)


def link_layout(
    title: str, url: str, *, description: str | None = None, label: str = "Open link"
) -> discord.ui.LayoutView:
    """Create a card whose primary action opens a URL."""
    node = ui.card(title, description, accent=DISCORD_GREEN, rows=(ui.Row((ui.LinkButton(label, url),)),))
    return ui.render_static([node])
