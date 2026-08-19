"""Widget-level IR: Discord-shaped nodes carrying overflow policies.

The semantic layer compiles to these; views needing exact control write them directly. Nodes
are immutable descriptions. `solve` fits them to the message budgets and `materialize` turns
the result into discord.py items — authors never do budget arithmetic.
"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field

from squid_layouts.actions import ActionPolicy
from squid_layouts.constraints import Alt, Overflow, Spill, Truncate
from squid_layouts.styles import ActionStyle, Color


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

    Entries may span multiple lines themselves — Spill keeps or drops whole entries. An entry
    may also be an :class:`~squid_layouts.constraints.Alt` carrying a degradation ladder:
    under pressure the solver steps the largest entries down their fallbacks before it spills
    any entry. Each :class:`~squid_layouts.constraints.Alt` may carry a drop priority; lower
    priorities disappear first, while surviving entries keep document order.
    """

    lines: tuple[str | Alt, ...]
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
class Button:
    """An interactive button whose handler runs through the mount's dispatch funnel."""

    label: str
    on_click: Callable[..., Awaitable[None]]
    key: str
    style: ActionStyle = ActionStyle.SECONDARY
    emoji: str | None = None
    disabled: bool = False
    policy: ActionPolicy = ActionPolicy.EXCLUSIVE


@dataclass(frozen=True, slots=True)
class Option:
    label: str
    value: str
    description: str | None = None
    default: bool = False


@dataclass(frozen=True, slots=True)
class SelectMenu:
    """A string select; occupies its own row when materialized."""

    options: tuple[Option, ...]
    on_select: Callable[..., Awaitable[None]]
    key: str
    placeholder: str | None = None
    min_values: int = 1
    max_values: int = 1
    disabled: bool = False
    policy: ActionPolicy = ActionPolicy.EXCLUSIVE


@dataclass(frozen=True, slots=True)
class RawItem:
    """Internal prepared target item retained until scene drawing."""

    factory: Callable[[], object]
    text_cost: int = 0
    component_cost: int = 1
    kind: str = "discord.raw"
    version: int = 0
    payload: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Embed:
    """A keyed component boundary expanded before portable planning."""

    component: object
    key: str

    def __post_init__(self) -> None:
        if not self.key:
            message = "Embed key must not be empty"
            raise ValueError(message)
        if "." in self.key:
            message = "Embed key must not contain '.'"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class Extension:
    """Target extension with a mandatory portable fallback."""

    kind: str
    version: int
    payload: object
    fallback: Node


@dataclass(frozen=True, slots=True)
class Row:
    """An exact target row; invalid local structure is a planning error."""

    items: tuple[LinkButton | Button | RawItem, ...]


@dataclass(frozen=True, slots=True)
class ActionGroup:
    """Buttons automatically arranged into as many valid target rows as needed."""

    items: tuple[LinkButton | Button | RawItem, ...]


@dataclass(frozen=True, slots=True)
class Thumbnail:
    url: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class Gallery:
    """One exact target gallery."""

    urls: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MediaCollection:
    """Media automatically arranged into valid target galleries."""

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
    accent: Color | None = None


@dataclass(frozen=True, slots=True)
class Variant:
    """One structural representation and the capabilities it requires."""

    node: Node
    requires: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class Choice:
    """Ordered structural and capability fallback ladder."""

    variants: tuple[Variant, ...]
    priority: int = 0

    def __post_init__(self) -> None:
        if not self.variants:
            message = "Choice needs at least one variant"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class Fold:
    """A structural alternate: ``primary``, or ``fallback`` when components run short.

    Overflow policies shrink *text*; nothing they do returns a component, so a document with
    too many components was previously only reportable. A Fold gives the solver something to
    give up: a button panel folding to one select, a gallery folding to a link row. The
    lowest-priority folds collapse first, and the choice is made before anything is measured,
    so it stays out of the text-policy matrix.
    """

    primary: Node
    fallback: Node
    priority: int = 0


type Node = (
    Text
    | Heading
    | Footer
    | Code
    | Lines
    | Sep
    | Row
    | ActionGroup
    | SelectMenu
    | Thumbnail
    | Gallery
    | MediaCollection
    | Section
    | Panel
    | RawItem
    | Embed
    | Extension
    | Fold
    | Choice
)


def as_nodes(rendered: Node | Sequence[Node]) -> list[Node]:
    """Normalize a render result — one node or a sequence of them — to a list."""
    return list(rendered) if isinstance(rendered, Sequence) else [rendered]
