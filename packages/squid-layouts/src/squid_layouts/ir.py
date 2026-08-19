"""Widget-level IR: Discord-shaped nodes carrying overflow policies.

The semantic layer compiles to these; views needing exact control write them directly. Nodes
are immutable descriptions. `solve` fits them to the message budgets and `materialize` turns
the result into discord.py items — authors never do budget arithmetic.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

import discord

from squid_layouts.constraints import Overflow, Spill, Truncate


@dataclass(frozen=True, slots=True)
class Text:
    content: str
    overflow: Overflow = field(default_factory=Truncate)
    priority: int = 0


@dataclass(frozen=True, slots=True)
class Heading:
    content: str
    level: int = 2
    overflow: Overflow = field(default_factory=Truncate)
    priority: int = 10


@dataclass(frozen=True, slots=True)
class Footer:
    """Small (`-#`) text at the card's foot; first to shrink by default."""

    content: str
    overflow: Overflow = field(default_factory=Truncate)
    priority: int = -10


@dataclass(frozen=True, slots=True)
class Code:
    """Fenced code block; embedded fences are escaped so content cannot break out."""

    content: str
    lang: str = ""
    overflow: Overflow = field(default_factory=Truncate)
    priority: int = 0


@dataclass(frozen=True, slots=True)
class Lines:
    """A list of entries joined by ``join``; spills to "…and N more" by default.

    Entries may span multiple lines themselves — Spill keeps or drops whole entries.
    """

    lines: tuple[str, ...]
    join: str = "\n"
    overflow: Overflow = field(default_factory=Spill)
    priority: int = 0


@dataclass(frozen=True, slots=True)
class Sep:
    large: bool = False
    visible: bool = True


@dataclass(frozen=True, slots=True)
class LinkButton:
    label: str
    url: str


@dataclass(frozen=True, slots=True)
class RawItem:
    """Escape hatch: a caller-built discord.py item added verbatim.

    This is how persistent DynamicItems enter a layout. The factory is called once per
    materialization; ``text_cost`` charges any display text the item contributes.
    """

    factory: Callable[[], discord.ui.Item]
    text_cost: int = 0


@dataclass(frozen=True, slots=True)
class Row:
    items: tuple[LinkButton | RawItem, ...]


@dataclass(frozen=True, slots=True)
class Thumbnail:
    url: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class Gallery:
    urls: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Section:
    """Up to three text nodes beside an accessory; extra texts are dropped with a note."""

    texts: tuple[Text | Heading | Footer, ...]
    accessory: Thumbnail | LinkButton | RawItem


@dataclass(frozen=True, slots=True)
class Panel:
    """A Container: children grouped under an optional accent colour."""

    children: tuple[Node, ...]
    accent: discord.Colour | int | None = None


type Node = Text | Heading | Footer | Code | Lines | Sep | Row | Thumbnail | Gallery | Section | Panel | RawItem
