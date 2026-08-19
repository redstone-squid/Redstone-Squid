"""squid-layouts glue: localized chrome, house colours, and the semantic layout presets.

This module is the bot's front door to the `squid_layouts` package. The package itself is
i18n-free by architecture rule, so every framework string is built here (where Babel extracts
`_()` markers) and passed in pre-translated through `Chrome`.

The layout helpers keep the exact signatures of their `squid.bot.utils.components`
predecessors, so call sites migrate by changing an import line.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import discord

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
    "card_layout",
    "chrome_for",
    "error_layout",
    "help_layout",
    "info_layout",
    "link_layout",
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
