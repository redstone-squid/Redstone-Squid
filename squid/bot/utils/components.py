"""Components V2 layout and message-boundary helpers."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import discord

DISCORD_RED = 0xF04747
DISCORD_YELLOW = 0xFAA61A
DISCORD_GREEN = 0x43B581
DISCORD_BLUE = 0x5865F2
DISCORD_GREY = 0x4F545C
MAX_DISPLAY_CHARACTERS = 4000


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


class StaticLayout(discord.ui.LayoutView):
    """A non-interactive Components V2 message layout."""

    def __init__(self, *items: discord.ui.Item[Any]) -> None:
        super().__init__(timeout=None)
        for item in items:
            self.add_item(item)


def truncate_display_text(content: str, limit: int) -> str:
    """Fit text into a Discord display budget with an explicit marker."""
    if len(content) <= limit:
        return content
    if limit <= 1:
        return "…"[:limit]
    return content[: limit - 1].rstrip() + "…"


def card_container(
    title: str,
    description: str | None = None,
    *,
    accent_colour: int = DISCORD_GREEN,
    fields: Sequence[CardField] = (),
    sections: Sequence[CardSection] = (),
    footer: str | None = None,
    media: Sequence[str] = (),
) -> discord.ui.Container[discord.ui.LayoutView]:
    """Create a purpose-built V2 card container."""
    footer_content = f"-# {footer}" if footer else ""
    field_content = "\n".join(f"**{field.name}**\n{field.value}" for field in fields)
    section_content = "\n\n".join(
        f"### {section.title}\n" + "\n".join(f"**{field.name}:** {field.value}" for field in section.fields)
        for section in sections
        if section.fields
    )
    fixed_length = len(title) + len(field_content) + len(section_content) + len(footer_content) + 8
    description_budget = max(0, MAX_DISPLAY_CHARACTERS - fixed_length)
    body = truncate_display_text(description or "", description_budget)

    heading = f"## {title}"
    if body:
        heading += f"\n{body}"

    children: list[discord.ui.Item[Any]]
    if media:
        children = [discord.ui.Section(heading, accessory=discord.ui.Thumbnail(media[0]))]
    else:
        children = [discord.ui.TextDisplay(heading)]
    if field_content:
        children.extend((discord.ui.Separator(), discord.ui.TextDisplay(field_content)))
    if section_content:
        children.extend((discord.ui.Separator(), discord.ui.TextDisplay(section_content)))
    if len(media) > 1:
        children.append(discord.ui.MediaGallery(*(discord.MediaGalleryItem(url) for url in media[1:10])))
    if footer_content:
        children.append(discord.ui.TextDisplay(footer_content))
    return discord.ui.Container(*children, accent_colour=accent_colour)


def card_layout(
    title: str,
    description: str | None = None,
    *,
    accent_colour: int = DISCORD_GREEN,
    fields: Sequence[CardField] = (),
    sections: Sequence[CardSection] = (),
    footer: str | None = None,
    media: Sequence[str] = (),
) -> StaticLayout:
    """Create a standalone V2 card."""
    return StaticLayout(
        card_container(
            title,
            description,
            accent_colour=accent_colour,
            fields=fields,
            sections=sections,
            footer=footer,
            media=media,
        )
    )


def text_layout(content: str, *, accent_colour: int | None = None) -> StaticLayout:
    """Create a simple V2 text response."""
    content = truncate_display_text(content, MAX_DISPLAY_CHARACTERS)
    if accent_colour is None:
        return StaticLayout(discord.ui.TextDisplay(content))
    return StaticLayout(discord.ui.Container(discord.ui.TextDisplay(content), accent_colour=accent_colour))


def error_layout(title: str, description: str | None) -> StaticLayout:
    return card_layout(title, f":x: {description or ''}", accent_colour=DISCORD_RED)


def warning_layout(title: str, description: str | None) -> StaticLayout:
    return card_layout(f":warning: {title}", description, accent_colour=DISCORD_YELLOW)


def info_layout(title: str, description: str | None) -> StaticLayout:
    return card_layout(title, description, accent_colour=DISCORD_GREEN)


def help_layout(
    title: str,
    description: str | None,
    *,
    sections: Sequence[CardSection] = (),
    footer: str | None = None,
) -> StaticLayout:
    return card_layout(title, description, accent_colour=DISCORD_BLUE, sections=sections, footer=footer)


def link_layout(title: str, url: str, *, description: str | None = None, label: str = "Open link") -> StaticLayout:
    """Create a card whose primary action opens a URL."""
    container = card_container(title, description)
    container.add_item(discord.ui.ActionRow(discord.ui.Button(label=label, url=url)))
    return StaticLayout(container)


def no_mentions() -> discord.AllowedMentions:
    """Return the default mention policy for rendered component text."""
    return discord.AllowedMentions.none()


def _message_uses_components_v2(message: discord.Message) -> bool:
    return bool(getattr(getattr(message, "flags", None), "components_v2", False))


async def edit_layout(
    message: discord.Message,
    layout: discord.ui.LayoutView,
    *,
    allowed_mentions: discord.AllowedMentions | None = None,
) -> discord.Message:
    """Edit a message with a V2 layout, clearing legacy fields on first conversion."""
    if not _message_uses_components_v2(message):
        return await message.edit(
            content=None,
            embed=None,
            view=layout,
            allowed_mentions=allowed_mentions,
        )
    return await message.edit(view=layout, allowed_mentions=allowed_mentions)


async def edit_interaction_layout(
    interaction: discord.Interaction[Any],
    layout: discord.ui.LayoutView,
    *,
    attachments: Sequence[discord.File | discord.Attachment] | None = None,
) -> None:
    """Edit the source interaction message, converting legacy payloads when needed.

    `attachments` replaces the message's files, so passing `[]` strips them and omitting it
    leaves them alone — a paging button should not re-upload what it is not changing.

    A callback that has already answered cannot edit through the response, so the edit goes
    through the interaction's original response instead. For a component callback that deferred
    its update — which is how a callback makes room for a consent prompt — that is the same
    message either way.
    """
    extra: dict[str, Any] = {} if attachments is None else {"attachments": list(attachments)}
    message = interaction.message
    if message is not None and not _message_uses_components_v2(message):
        extra |= {"content": None, "embed": None}
    if interaction.response.is_done():
        await interaction.edit_original_response(view=layout, **extra)
        return
    await interaction.response.edit_message(view=layout, **extra)


async def reply_layout(
    interaction: discord.Interaction[Any],
    layout: discord.ui.LayoutView,
    *,
    ephemeral: bool = True,
    wait: bool = False,
) -> discord.Message | None:
    """Answer an interaction with a layout, whether or not it has already been responded to.

    A component callback cannot know: a consent prompt or a deferral upstream may have spent
    the response, and the second send has to be a followup or Discord rejects it.

    `wait` costs a round trip to fetch the message back, so it is opt-in and only worth it
    for a view that needs to edit itself later — an expiring panel disabling its controls.
    """
    if interaction.response.is_done():
        message = await interaction.followup.send(
            view=layout, ephemeral=ephemeral, wait=wait, allowed_mentions=no_mentions()
        )
        return message if wait else None
    await interaction.response.send_message(  # pyrefly: ignore[no-matching-overload]
        view=layout, ephemeral=ephemeral, allowed_mentions=no_mentions()
    )
    return await interaction.original_response() if wait else None
