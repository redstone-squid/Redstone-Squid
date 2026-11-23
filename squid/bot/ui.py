"""Provide the bot's translated chrome, colour palette, and reusable layout helpers.

Chrome is the shared text for controls and notices, such as Close and Loading.
The node helpers build composable layout descriptions; render_payload turns a
complete description into a Discord message payload. The squid-ui packages plan
the layout and handle delivery, using the caller's render-time localization.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

import discord

import squid_ui as ui
import squid_ui.ext.discord as sd
from squid.bot.chrome import CHROME
from squid.core.i18n import tr
from squid_i18n import NEUTRAL, Text, resolve_text
from squid_ui.ext.discord.target_types import ComponentsV2Target


class DiscordColour(IntEnum):
    """Exact Discord house colours used where a semantic palette tone is insufficient."""

    RED = 0xF04747
    YELLOW = 0xFAA61A
    GREEN = 0x43B581
    BLUE = 0x5865F2
    GREY = 0x4F545C


DISCORD_RED = DiscordColour.RED
DISCORD_YELLOW = DiscordColour.YELLOW
DISCORD_GREEN = DiscordColour.GREEN
DISCORD_BLUE = DiscordColour.BLUE
DISCORD_GREY = DiscordColour.GREY

__all__ = [
    "CHROME",
    "DISCORD_BLUE",
    "DISCORD_GREEN",
    "DISCORD_GREY",
    "DISCORD_RED",
    "DISCORD_YELLOW",
    "HOST_DEFAULTS",
    "PALETTES",
    "CardField",
    "CardSection",
    "DiscordColour",
    "card_node",
    "error_node",
    "info_node",
    "link_node",
    "render_item",
    "render_payload",
    "text_node",
    "tr",
    "truncate_display_text",
    "user_inline_text",
    "user_markdown_paragraph",
]


@dataclass(frozen=True, slots=True)
class CardField:
    """A labelled value rendered inside a card."""

    name: ui.TextLike
    value: ui.TextLike


@dataclass(frozen=True, slots=True)
class CardSection:
    """A titled group of related values rendered inside a card."""

    title: ui.TextLike
    fields: Sequence[CardField]


_OPEN_LINK = tr(t"bot-ui-open-link = Open link")

PALETTES = ui.PaletteRegistry(
    {
        "squid": ui.Palette(
            brand=DISCORD_BLUE,
            neutral=DISCORD_GREY,
            info=DISCORD_BLUE,
            success=DISCORD_GREEN,
            warning=DISCORD_YELLOW,
            danger=DISCORD_RED,
        )
    },
    default="squid",
)


def render_item(
    node: ui.LayoutNode[ComponentsV2Target, sd.Request],
    *,
    localization: ui.text.Localization = ui.text.NEUTRAL,
    reservation: sd.ResourceCost = sd.EMPTY_COST,
) -> discord.ui.Item[Any]:
    """Render one node to a detached item through the bot's chrome and catalogue."""
    return sd.render_item(
        node,
        chrome=CHROME,
        localization=localization,
        palette=PALETTES.resolve(),
        reservation=reservation,
    )


def render_payload(
    nodes: ui.DocumentLike[ComponentsV2Target, sd.Request],
    *,
    localization: ui.text.Localization = ui.text.NEUTRAL,
    strict: bool = False,
    composition: ui.planning.CompositionOptions | None = None,
    reservation: sd.ResourceCost = sd.EMPTY_COST,
) -> sd.messages.payload.MessagePayload:
    """Render a complete Discord payload through the bot's chrome and catalogue."""
    return sd.render_static(
        nodes,
        chrome=CHROME,
        localization=localization,
        palette=PALETTES.resolve(),
        strict=strict,
        composition=composition,
        reservation=reservation,
    )


def user_inline_text(content: str) -> Text:
    """Render user text literally on a single line."""
    return ui.plain(" ".join(content.split()))


def user_markdown_paragraph(content: str) -> ui.LayoutNode[ComponentsV2Target, sd.Request]:
    """Render user Markdown in its own Discord text component.

    Parse the fragment independently so unmatched delimiters are rendered as
    literal text. Use visible text for the excerpt when block formatting cannot
    be shortened.
    """
    markdown = ui.md(content)
    return ui.fallback(
        ui.paragraph(markdown).truncate(),
        ui.paragraph(resolve_text(markdown, NEUTRAL).plain().rstrip()).truncate(),
    )


def truncate_display_text(content: str, limit: int) -> str:
    """Fit text into a Discord display budget with an explicit marker."""
    if len(content) <= limit:
        return content
    if limit <= 1:
        return "\u2026"[:limit]
    return content[: limit - 1].rstrip() + "\u2026"


HOST_DEFAULTS = sd.MessageRootDefaults(chrome=CHROME, palette=PALETTES.resolve())
"""Shared chrome and palette defaults; `sd.install` supplies runtime services and challenge handling."""


def _fields(fields: Sequence[CardField]) -> tuple[ui.semantic.Field, ...]:
    return tuple(ui.field(field.name, field.value) for field in fields)


def _groups(sections: Sequence[CardSection]) -> tuple[ui.semantic.Section, ...]:
    # Separate field nodes can condense independently when the message budget is tight.
    return tuple(ui.section(ui.heading(s.title), ui.fields(*_fields(s.fields))) for s in sections if s.fields)


def card_node(
    title: ui.TextLike,
    description: ui.TextLike | None = None,
    *,
    accent_colour: ui.Accent = ui.Tone.SUCCESS,
    fields: Sequence[CardField] = (),
    sections: Sequence[CardSection] = (),
    footer: ui.TextLike | None = None,
    media: Sequence[str] = (),
) -> ui.LayoutNode[ComponentsV2Target, sd.Request]:
    """Build a semantic card that can be composed inside a component render."""
    extra_media = media[1:]
    return ui.section(
        ui.heading(title),
        # Shorten the description before reducing fields or the footer.
        description and ui.paragraph(description).truncate(),
        # `fields`/`extra_media` are tuples: an empty one is falsy but not `False`, and
        # `_children` only skips `None`/`False`, so the truthiness check must be explicit.
        bool(fields) and ui.fields(*_fields(fields)),
        *_groups(sections),
        bool(extra_media) and ui.media(*extra_media, key="media"),
        footer and ui.note(footer),
        accent=accent_colour,
        thumbnail=media[0] if media else None,
    )


def text_node(
    content: ui.TextLike, *, accent_colour: ui.Accent = None
) -> ui.LayoutNode[ComponentsV2Target, sd.Request]:
    """Build a truncating text response for composition inside a component render."""
    # Truncate-wrapped rather than bare: a plain paragraph lowers to Never, which *raises*
    # on an overlong message. This is the bot's most-used reply path, so it clips.
    node: ui.LayoutNode[ComponentsV2Target, sd.Request] = ui.paragraph(content).truncate()
    if accent_colour is not None:
        node = ui.block(node, accent=accent_colour)
    return node


def error_node(title: ui.TextLike, description: ui.TextLike | None) -> ui.LayoutNode[ComponentsV2Target, sd.Request]:
    """Build an error card for composition inside a component render."""
    return card_node(
        title,
        Text(t":x: {description or ''}"),
        accent_colour=ui.Tone.DANGER,
    )


def info_node(title: ui.TextLike, description: ui.TextLike | None) -> ui.LayoutNode[ComponentsV2Target, sd.Request]:
    """Build an informational card for composition inside a component render."""
    return card_node(title, description, accent_colour=ui.Tone.SUCCESS)


def link_node(
    title: ui.TextLike,
    url: str,
    *,
    description: ui.TextLike | None = None,
    label: ui.TextLike = _OPEN_LINK,
) -> ui.LayoutNode[ComponentsV2Target, sd.Request]:
    """Build a link card for composition inside a component render."""
    return ui.section(
        ui.heading(title),
        description and ui.paragraph(description).truncate(),
        ui.action_controls(ui.link(label, url, key="open-link"), key="link"),
        accent=ui.Tone.SUCCESS,
    )
