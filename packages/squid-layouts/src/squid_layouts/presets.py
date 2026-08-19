"""Named document shapes built from IR nodes.

Policy-free and string-in/string-out: callers pass pre-translated text and pick colours. Each
preset returns IR, so callers can post-process (append rows, wrap further) before rendering.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import discord

from squid_layouts.constraints import Alt
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


def _normalized_alt(primary: str, fallbacks: Sequence[str]) -> Alt:
    """Build a valid Alt from caller-supplied fallbacks, dropping rungs that cannot help.

    Presets accept loosely-shaped user data (values assembled from formatting and escaping),
    so a rung that came out empty or longer than what precedes it is skipped rather than
    rejected — direct `Alt` construction stays strict.
    """
    kept: list[str] = []
    ceiling = len(primary)
    for rung in fallbacks:
        if rung and len(rung) <= ceiling:
            kept.append(rung)
            ceiling = len(rung)
    return Alt(primary=primary, fallbacks=tuple(kept))


@dataclass(frozen=True, slots=True)
class Field:
    """A labelled value rendered inside a card.

    ``alts`` is the value's degradation ladder: shorter alternates tried in order when the
    card is under budget pressure (e.g. all links → a count and the first link → a count).
    """

    name: str
    value: str
    alts: tuple[str, ...] = ()


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
        entries = tuple(
            _normalized_alt(
                f"**{field.name}**\n{field.value}",
                tuple(f"**{field.name}**\n{alt}" for alt in field.alts),
            )
            for field in fields
        )
        children.append(Lines(entries, priority=5))
    group_blocks = tuple(_group_ladder(group) for group in groups if group.fields)
    if group_blocks:
        children.append(Sep())
        children.append(Lines(group_blocks, join="\n\n", priority=4))
    if len(media) > 1:
        children.append(Gallery(tuple(media[1:])))
    if footer:
        children.append(Footer(footer, priority=3))
    children.extend(rows)
    return Panel(children=tuple(children), accent=accent)


def _group_ladder(group: FieldGroup) -> Alt:
    """A group block's degradation ladder: every field steps down its own ladder together."""
    depth = max((1 + len(field.alts) for field in group.fields), default=1)

    def block(level: int) -> str:
        rendered = []
        for field in group.fields:
            values = (field.value, *field.alts)
            rendered.append(f"**{field.name}:** {values[min(level, len(values) - 1)]}")
        return f"### {group.title}\n" + "\n".join(rendered)

    return _normalized_alt(block(0), tuple(block(level) for level in range(1, depth)))


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
