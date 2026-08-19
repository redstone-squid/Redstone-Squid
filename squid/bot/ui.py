"""squid-layouts glue: localized chrome, house colours, and the semantic layout presets.

This module is the bot's front door to the `squid_layouts` package. The package itself is
i18n-free by architecture rule, so every framework string is built here (where Babel extracts
`_()` markers) and passed in pre-translated through `Chrome`.

The layout helpers keep the exact signatures of their `squid.bot.utils.components`
predecessors, so call sites migrate by changing an import line.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

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
    "Private",
    "Visibility",
    "card_layout",
    "chrome_for",
    "create_mount",
    "display_text_length",
    "error_layout",
    "help_layout",
    "info_layout",
    "link_layout",
    "render_item",
    "reply",
    "send_component",
    "text_layout",
    "truncate_display_text",
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
        back=t(locale, _("Back")),
        home=t(locale, _("Home")),
        close=t(locale, _("Close")),
        page_footer=lambda page, pages: t(locale, _("Page {page} of {pages}"), page=page, pages=pages),
    )


@dataclass(frozen=True, slots=True)
class Private:
    """Deliver where a channel can never see it: ephemeral or DM, with `reason` explaining why."""

    reason: str


type Visibility = Private | Literal["public", "personal"]


async def reply(
    ctx: Context[Any],
    view: discord.ui.LayoutView,
    *,
    visibility: Visibility = "public",
    locale: str | None = None,
    file: discord.File | None = None,
) -> discord.Message | None:
    """The one reply entry point: send `view` with an explicit audience.

    "public" answers in the channel; "personal" is ephemeral where the transport allows it
    (see `squid.bot.utils.visibility.personal`); `Private(reason)` must never reach a channel
    and falls back to a DM on the prefix side.
    """
    # Imported lazily: visibility -> utils.components -> this module would otherwise cycle.
    from squid.bot.utils.visibility import deliver_privately, personal

    if isinstance(visibility, Private):
        return await deliver_privately(ctx, view, reason=visibility.reason, locale=locale, file=file)
    extra: dict[str, Any] = {"file": file} if file is not None else {}
    ephemeral = visibility == "personal" and personal(ctx)
    return await ctx.send(view=view, ephemeral=ephemeral, allowed_mentions=ui.discord.delivery.no_mentions(), **extra)


def render_item(node: ui.primitives.Node, *, locale: str | None = None, reserved_text: int = 0) -> discord.ui.Item[Any]:
    """Render one node to a detached item, for composition into a larger layout.

    The build card uses this: it renders as an engine-solved Container that callers (vote
    cards, search detail) then embed or extend. A detached item escapes the engine's view of
    the message, so callers pass ``reserved_text`` for whatever else the message will carry;
    composing the whole document with `ui.discord.render_static` is better still where possible.
    """
    view = ui.discord.render_static([node], chrome=chrome_for(locale), reserved_text=reserved_text)
    item = view.children[0]
    view.remove_item(item)
    return item


def display_text_length(view: discord.ui.LayoutView) -> int:
    """Display characters already spent in `view`.

    Hand-assembled V1 views compose engine-solved items with items of their own; passing this
    as `reserved_text` tells the engine how much of the message budget is already gone.
    """
    return sum(len(item.content) for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay))


def truncate_display_text(content: str, limit: int) -> str:
    """Fit text into a Discord display budget with an explicit marker."""
    if len(content) <= limit:
        return content
    if limit <= 1:
        return "\u2026"[:limit]
    return content[: limit - 1].rstrip() + "\u2026"


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
) -> ui.discord.Mount:
    """A mount wired to the bot's chrome and shared interaction error handler."""
    return ui.discord.Mount(
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
) -> ui.discord.Mount:
    """Mount a component and send it as the reply to a command."""
    mount = create_mount(component, locale=locale, timeout=timeout, lock_to=lock_to)
    view = mount.build_view()
    message = await ctx.send(
        view=view,
        files=mount.attachment_files(),
        ephemeral=ephemeral,
        allowed_mentions=ui.discord.delivery.no_mentions(),
    )
    mount.bind(message, view)
    return mount


class PagedList(ui.Component):
    """A card holding one page of a pre-rendered list, plus the controls to walk it.

    The reactive successor to `squid.bot.utils.pagination.ListPaginator`: `page_size` entries
    per page is a deliberate UX pin, expressed as the engine's count-based `Paginate`.
    Passing ``None`` lets the engine fill each page from the target's measured text budget.
    The mount owns paging, the author lock, and expiry. It does not fetch — every caller
    holds its whole list before rendering.
    """

    def __init__(
        self,
        title: str,
        entries: Sequence[str],
        *,
        empty: str,
        locale: str | None = None,
        page_size: int | None = 10,
        separator: str = "\n\n",
        accent_colour: int = DISCORD_GREEN,
    ) -> None:
        self.title = title
        self.entries = tuple(entries)
        self.empty = empty
        self.locale = locale
        self.page_size = None if page_size is None else max(1, page_size)
        self.separator = separator
        self.accent_colour = accent_colour

    def render(self) -> Sequence[ui.primitives.Node]:
        # An entry list that fits on one page produces no pager, and so no controls: a row of
        # two dead buttons reads as a broken control rather than as an absent one.
        body: ui.primitives.Node = (
            ui.primitives.Lines(
                self.entries,
                join=self.separator,
                overflow=ui.primitives.Paginate(key="entries", per=self.page_size, footer=self._page_footer),
            )
            if self.entries
            else ui.primitives.Text(self.empty)
        )
        return [ui.primitives.Panel(children=(ui.primitives.Heading(self.title), body), accent=self.accent_colour)]

    def _page_footer(self, page: int, pages: int) -> str:
        return t(
            self.locale,
            _("Page {page} of {pages} · {total} in total"),
            page=page,
            pages=pages,
            total=len(self.entries),
        )

    async def send(self, ctx: Context[Any], *, ephemeral: bool = False) -> ui.discord.Mount:
        """Send the first page bound to a mount that owns paging, locking, and expiry."""
        return await send_component(
            ctx, self, locale=self.locale, lock_to=ctx.author.id if ctx.author else None, ephemeral=ephemeral
        )


def _fields(fields: Sequence[CardField]) -> tuple[ui.primitives.presets.Field, ...]:
    return tuple(ui.primitives.presets.Field(field.name, field.value) for field in fields)


def _groups(sections: Sequence[CardSection]) -> tuple[ui.primitives.FieldGroup, ...]:
    return tuple(ui.primitives.FieldGroup(section.title, _fields(section.fields)) for section in sections)


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
    node = ui.primitives.card(
        title,
        description,
        accent=accent_colour,
        fields=_fields(fields),
        groups=_groups(sections),
        footer=footer,
        media=media,
    )
    return ui.discord.render_static([node])


def text_layout(content: str, *, accent_colour: int | None = None) -> discord.ui.LayoutView:
    """Create a simple V2 text response."""
    return ui.discord.render_static([ui.primitives.banner(content, accent=accent_colour)])


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
    node = ui.primitives.card(
        title, description, accent=DISCORD_GREEN, rows=(ui.primitives.Row((ui.primitives.LinkButton(label, url),)),)
    )
    return ui.discord.render_static([node])
