"""Widget-level IR: Discord-shaped nodes carrying overflow policies.

The semantic layer compiles to these; views needing exact control write them directly. Nodes
are immutable descriptions. The planner fits them to target budgets and renderers draw the
resulting scene — authors never do budget arithmetic.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from squid_layouts.actions import ActionBinding, ActionPolicy, PressHandler, SelectionHandler
from squid_layouts.primitives.constraints import Alt, Overflow, Spill, Truncate
from squid_layouts.primitives.styles import ActionStyle, Color


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
    may also be an :class:`~squid_layouts.primitives.constraints.Alt` carrying a degradation ladder:
    under pressure the solver steps the largest entries down their fallbacks before it spills
    any entry. Each :class:`~squid_layouts.primitives.constraints.Alt` may carry a drop priority; lower
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
    on_click: PressHandler
    key: str
    style: ActionStyle = ActionStyle.SECONDARY
    emoji: str | None = None
    disabled: bool = False
    policy: ActionPolicy = ActionPolicy.EXCLUSIVE


@dataclass(frozen=True, slots=True)
class RoutedButton:
    """A button whose custom id *is* its state, dispatched by a router rather than a mount.

    Carries no handler, so it needs no binding and survives the process that drew it: a
    sessionless document may hold one, and a mount's policies (author lock, generation
    checks) do not reach it even when it sits inside a mounted message. Build the id with
    a `squid_layouts.routing.Route` rather than by hand.
    """

    label: str
    custom_id: str
    style: ActionStyle = ActionStyle.SECONDARY
    emoji: str | None = None
    disabled: bool = False


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
    on_select: SelectionHandler
    key: str
    placeholder: str | None = None
    min_values: int = 1
    max_values: int = 1
    disabled: bool = False
    policy: ActionPolicy = ActionPolicy.EXCLUSIVE
    routes: Mapping[str, ActionBinding] = field(default_factory=dict)


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

    items: tuple[LinkButton | Button | RoutedButton | RawItem, ...]


@dataclass(frozen=True, slots=True)
class ActionGroup:
    """Buttons automatically arranged into as many valid target rows as needed."""

    items: tuple[LinkButton | Button | RoutedButton | RawItem, ...]


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
    accessory: Thumbnail | LinkButton | RoutedButton | RawItem


@dataclass(frozen=True, slots=True)
class Panel:
    """A Container: children grouped under an optional accent colour."""

    children: tuple[Node, ...]
    accent: Color | None = None


@dataclass(frozen=True, slots=True)
class Variant:
    """One structural representation of a region and the capabilities it requires.

    ``nodes`` is a tuple because a variant may lower to several nodes — an ActionGroup becomes
    one Row per five buttons — and splicing them into the parent is exact where wrapping them
    in a Panel would invent the very container component the ladder exists to save.
    """

    nodes: tuple[Node, ...]
    requires: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.nodes:
            message = "Variant needs at least one node"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class Variants:
    """An ordered ladder of structural representations for one region.

    Overflow policies shrink *text*; nothing they do returns a component, so a document with
    too many components would otherwise only be reportable. A ladder gives the solver
    something to give up: a button panel stepping to one select, a gallery to a link row.

    Rungs unsupported by the target are dropped at planning time; the survivors form a budget
    ladder. The solver opens every ladder at rung 0 and, under component pressure, steps the
    lowest-priority one down a single rung, re-solving after each step — the decision is made
    before anything is measured, so it stays out of the text-policy matrix.

    Two rules follow from stepping being a whole-tree decision. ``priority`` compares
    **globally**, not among siblings: the lowest-priority ladder anywhere in the document
    steps first, and equal priorities step breadth-first, each reaching rung 1 before any
    reaches rung 2. And a nested ladder only becomes steppable once its ancestor's *selected*
    rung exposes it; stepping the ancestor abandons it and opens whatever the new rung holds
    at rung 0.
    """

    variants: tuple[Variant, ...]
    priority: int = 0

    def __post_init__(self) -> None:
        if not self.variants:
            message = "Variants needs at least one variant"
            raise ValueError(message)

    @classmethod
    def of(cls, *rungs: Node | Variant, priority: int = 0) -> Variants:
        """Build a ladder from bare nodes, wrapping each in a capability-free Variant."""
        return cls(tuple(rung if isinstance(rung, Variant) else Variant((rung,)) for rung in rungs), priority)


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
    | RoutedButton
    | Thumbnail
    | Gallery
    | MediaCollection
    | Section
    | Panel
    | RawItem
    | Embed
    | Extension
    | Variants
)


def as_nodes(rendered: Node | Sequence[Node]) -> list[Node]:
    """Normalize a render result — one node or a sequence of them — to a list."""
    return list(rendered) if isinstance(rendered, Sequence) else [rendered]
