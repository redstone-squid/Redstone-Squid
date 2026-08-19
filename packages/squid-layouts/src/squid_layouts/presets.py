"""Named document shapes built from IR nodes.

Policy-free and string-in/string-out: callers pass pre-translated text and pick colours. Each
preset returns IR, so callers can post-process (append rows, wrap further) before rendering.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import discord

from squid_layouts.ir import (
    Code,
    Footer,
    Gallery,
    Heading,
    Lines,
    Node,
    Panel,
    Row,
    Section,
    Sep,
    Text,
    Thumbnail,
)


@dataclass(frozen=True, slots=True)
class Field:
    """A labelled value rendered inside a card."""

    name: str
    value: str


@dataclass(frozen=True, slots=True)
class FieldGroup:
    """A titled group of related values rendered inside a card."""

    title: str
    fields: Sequence[Field]


def card(
    title: str,
    description: str | None = None,
    *,
    accent: discord.Colour | int | None = None,
    fields: Sequence[Field] = (),
    groups: Sequence[FieldGroup] = (),
    footer: str | None = None,
    media: Sequence[str] = (),
    rows: Sequence[Row] = (),
) -> Panel:
    """A titled card: heading, body, labelled fields, grouped fields, media, footer.

    The body yields budget first (Truncate), field lists spill to "…and N more", and the
    heading and footer carry their default priorities — a long body can no longer starve
    the rest of the card.
    """
    heading = Heading(title)
    # The body is the card's shock absorber: everything else outranks it, so a long
    # description trims before a field, group, or the footer loses a character.
    body = Text(description, priority=-5) if description else None
    children: list[Node] = []
    if media:
        texts = (heading, body) if body is not None else (heading,)
        children.append(Section(texts=texts, accessory=Thumbnail(media[0])))
    else:
        children.append(heading)
        if body is not None:
            children.append(body)
    if fields:
        children.append(Sep())
        children.append(Lines(tuple(f"**{field.name}**\n{field.value}" for field in fields), priority=5))
    group_blocks = tuple(
        f"### {group.title}\n" + "\n".join(f"**{field.name}:** {field.value}" for field in group.fields)
        for group in groups
        if group.fields
    )
    if group_blocks:
        children.append(Sep())
        children.append(Lines(group_blocks, join="\n\n", priority=4))
    if len(media) > 1:
        children.append(Gallery(tuple(media[1:])))
    if footer:
        children.append(Footer(footer, priority=3))
    children.extend(rows)
    return Panel(children=tuple(children), accent=accent)


def banner(content: str, *, accent: discord.Colour | int | None = None) -> Node:
    """A plain text message, optionally wrapped in an accent-coloured container."""
    text = Text(content)
    if accent is None:
        return text
    return Panel(children=(text,), accent=accent)


def listing(
    title: str,
    entries: Sequence[str],
    *,
    footer: str | None = None,
    accent: discord.Colour | int | None = None,
    rows: Sequence[Row] = (),
) -> Panel:
    """A titled list of entries; overflow spills to "…and N more"."""
    children: list[Node] = [Heading(title), Lines(tuple(entries))]
    if footer:
        children.append(Footer(footer))
    children.extend(rows)
    return Panel(children=tuple(children), accent=accent)


def report(
    title: str,
    body: str,
    *,
    lang: str = "",
    fields: Sequence[Field] = (),
    footer: str | None = None,
    accent: discord.Colour | int | None = None,
) -> Panel:
    """A titled code-fenced report with optional labelled fields."""
    children: list[Node] = [Heading(title), Code(body, lang=lang)]
    if fields:
        children.append(Lines(tuple(f"**{field.name}**\n{field.value}" for field in fields)))
    if footer:
        children.append(Footer(footer))
    return Panel(children=tuple(children), accent=accent)
