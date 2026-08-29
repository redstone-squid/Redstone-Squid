"""squid-ui glue: localized chrome, house colours, and the semantic layout vocabulary.

This module is the bot's front door to the `squid_ui` package. The package resolves text,
while this host supplies the gettext catalogue and translatable chrome messages.

The bot owns only localized chrome and audience policy; rendering and delivery stay in
``squid_ui``.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from math import ceil
from typing import Any

import discord

import squid_ui as ui
import squid_ui_discord as sd
from squid.core.i18n import tr

DISCORD_RED = 0xF04747
DISCORD_YELLOW = 0xFAA61A
DISCORD_GREEN = 0x43B581
DISCORD_BLUE = 0x5865F2
DISCORD_GREY = 0x4F545C

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
    "card_node",
    "error_node",
    "info_node",
    "link_node",
    "render_item",
    "render_payload",
    "text_node",
    "tr",
    "truncate_display_text",
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


def _try_again_in(seconds: float) -> ui.text.Message:
    """Round a guard's remaining cooldown up to whole seconds before wording it."""
    whole = max(1, ceil(seconds))
    return tr(t"Try again in {whole} seconds.")


CHROME = ui.chrome.Chrome(
    and_n_more=lambda count: tr(t"…and {count} more."),
    not_yours=tr(t"These list controls belong to someone else."),
    session_ended=tr(t"This session has ended."),
    not_now=tr(t"You can't do that right now."),
    try_again_in=_try_again_in,
    working=tr(t"Working…"),
    updates_paused=tr(t"Live updates paused — press any control to resume."),
    session_expiring=tr(t"This session is about to expire."),
    continue_session=tr(t"Continue Session"),
    sent_privately=tr(t"Sent by direct message."),
    dm_unavailable=tr(
        t"""This reply is too private for a channel, and your direct messages are closed. \
Run the command in a direct message, or allow direct messages and retry."""
    ),
    previous=tr(t"Previous"),
    next=tr(t"Next"),
    back=tr(t"Back"),
    home=tr(t"Home"),
    close=tr(t"Close"),
    on=tr(t"On"),
    off=tr(t"Off"),
    download=tr(t"Download"),
    confirm=tr(t"Confirm"),
    cancel=tr(t"Cancel"),
    apply=tr(t"Apply"),
    save=tr(t"Save"),
    unsaved=tr(t"Unsaved changes"),
    search=tr(t"Search"),
    no_results=tr(t"No results"),
    decided=lambda label: tr(t"You chose {label}."),
    add=tr(t"Add"),
    edit=tr(t"Edit"),
    remove=tr(t"Remove"),
    move_up=tr(t"Move up"),
    move_down=tr(t"Move down"),
    review=tr(t"Review"),
    finish=tr(t"Finish"),
    unanswered=tr(t"Not answered yet"),
    page_footer=lambda page, pages: tr(t"Page {page} of {pages}"),
)
_OPEN_LINK = tr(t"Open link")

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
    node: ui.LayoutNode[ui.ComponentsV2Target],
    *,
    localization: ui.text.Localization = ui.text.NEUTRAL,
    reservation: sd.ResourceCost = sd.EMPTY_RESERVATION,
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
    nodes: ui.DocumentLike[ui.ComponentsV2Target],
    *,
    localization: ui.text.Localization = ui.text.NEUTRAL,
    strict: bool = False,
    reservation: sd.ResourceCost = sd.EMPTY_RESERVATION,
) -> sd.message_payload.MessagePayload:
    """Render a complete Discord payload through the bot's chrome and catalogue."""
    return sd.render_static(
        nodes,
        chrome=CHROME,
        localization=localization,
        palette=PALETTES.resolve(),
        strict=strict,
        reservation=reservation,
    )


def truncate_display_text(content: str, limit: int) -> str:
    """Fit text into a Discord display budget with an explicit marker."""
    if len(content) <= limit:
        return content
    if limit <= 1:
        return "\u2026"[:limit]
    return content[: limit - 1].rstrip() + "\u2026"


async def _component_error_hook(interaction: discord.Interaction, error: Exception, source: str) -> None:
    # Imported lazily to keep error handling independent from the command UI catalogue.
    from squid.bot.errors import handle_interaction_error

    await handle_interaction_error(interaction, error, surface=f"component:{source}")


HOST_DEFAULTS = sd.MessageRootDefaults(chrome=CHROME, palette=PALETTES.resolve(), on_error=_component_error_hook)
"""What the bot installs with: the chrome and error handling every panel shares.

Only the half that can be written down as a value. The other half -- a challenge presenter,
which needs the session registry and the background runner -- is assembled by
`sd.install` and reached back through `ClientRuntime.of`, so a panel built from a click
gets the same wiring as one opened through `bot.mounts`.
"""


def _fields(fields: Sequence[CardField]) -> tuple[ui.semantic.Field, ...]:
    return tuple(ui.field(field.name, field.value) for field in fields)


def _groups(sections: Sequence[CardSection]) -> tuple[ui.semantic.Section, ...]:
    # A nested section per group: each field steps its own Condense ladder independently
    # rather than a whole group stepping in lockstep — finer-grained, not a regression.
    return tuple(ui.section(ui.heading(s.title), ui.fields(*_fields(s.fields))) for s in sections if s.fields)


def card_node(
    title: ui.TextLike,
    description: ui.TextLike | None = None,
    *,
    accent_colour: int = DISCORD_GREEN,
    fields: Sequence[CardField] = (),
    sections: Sequence[CardSection] = (),
    footer: ui.TextLike | None = None,
    media: Sequence[str] = (),
) -> ui.LayoutNode[ui.ComponentsV2Target]:
    """Build a semantic card that can be composed inside a component render."""
    extra_media = media[1:]
    return ui.section(
        ui.heading(title),
        # The body is the card's shock absorber: truncate lets it give up characters under
        # pressure before a field or the footer loses any.
        description and ui.truncate(ui.paragraph(description)),
        # `fields`/`extra_media` are tuples: an empty one is falsy but not `False`, and
        # `_children` only skips `None`/`False`, so the truthiness check must be explicit.
        bool(fields) and ui.fields(*_fields(fields)),
        *_groups(sections),
        bool(extra_media) and ui.media(*extra_media, key="media"),
        footer and ui.note(footer),
        accent=accent_colour,
        thumbnail=media[0] if media else None,
    )


def text_node(content: ui.TextLike, *, accent_colour: int | None = None) -> ui.LayoutNode[ui.ComponentsV2Target]:
    """Build a truncating text response for composition inside a component render."""
    # Truncate-wrapped rather than bare: a plain paragraph lowers to Never, which *raises*
    # on an overlong message. This is the bot's most-used reply path, so it clips.
    node: ui.LayoutNode[ui.ComponentsV2Target] = ui.truncate(ui.paragraph(content))
    if accent_colour is not None:
        node = ui.block(node, accent=accent_colour)
    return node


def _prefixed(prefix: str, value: ui.TextLike) -> ui.TextLike:
    if isinstance(value, ui.text.Message):
        plural = None if value.plural is None else prefix + value.plural
        return ui.text.Message(prefix + value.template, value.params, value.markup, plural)
    if isinstance(value, ui.text.ResolvedText):
        return ui.text.ResolvedText(prefix + value.content, value.markup)
    return prefix + value


def error_node(title: ui.TextLike, description: ui.TextLike | None) -> ui.LayoutNode[ui.ComponentsV2Target]:
    """Build an error card for composition inside a component render."""
    return card_node(
        title,
        _prefixed(":x: ", description or ""),
        accent_colour=DISCORD_RED,
    )


def info_node(title: ui.TextLike, description: ui.TextLike | None) -> ui.LayoutNode[ui.ComponentsV2Target]:
    """Build an informational card for composition inside a component render."""
    return card_node(title, description, accent_colour=DISCORD_GREEN)


def link_node(
    title: ui.TextLike,
    url: str,
    *,
    description: ui.TextLike | None = None,
    label: ui.TextLike = _OPEN_LINK,
) -> ui.LayoutNode[ui.ComponentsV2Target]:
    """Build a link card for composition inside a component render."""
    return ui.section(
        ui.heading(title),
        description and ui.truncate(ui.paragraph(description)),
        ui.action_controls(ui.link(label, url, key="open-link"), key="link"),
        accent=DISCORD_GREEN,
    )
