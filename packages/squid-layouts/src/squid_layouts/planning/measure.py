"""Measure one concrete IR tree against Discord's message budgets.

`measure()` evaluates a single already-decided layout. It costs every node's chrome
(markdown prefixes, code fences, join characters) exactly, grants the shared display-text
budget in priority order, and applies each node's overflow policy only when its content does
not fit. Higher priority is allocated first; ties fall back to document order. Dropped nodes
refund their grant and the allocation reruns, so a dropped footnote genuinely returns its
characters to the body.

Nothing here chooses between alternatives. `Variants` and semantic fallbacks are the
planner's search decisions, and a layout reaching this module has already made them.
"""

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum, StrEnum
from heapq import heappop, heappush

from squid_layouts.chrome import DEFAULT_CHROME, Chrome, localize_chrome
from squid_layouts.errors import LayoutInvariantError
from squid_layouts.planning.breaking import BreakItem, balanced_breaks
from squid_layouts.planning.degradation import DegradationProfile, DegradationRecorder
from squid_layouts.planning.limits import (
    COMPONENTS,
    CONTENT_TEXT,
    CONTROLS,
    DISPLAY_TEXT,
    ELLIPSIS,
    EMBED_TEXT,
    EMBEDS,
    LIMITS,
    ROWS,
    TEXT_AXES,
    DiscordLimits,
)
from squid_layouts.planning.navigation import (
    NavNode,
    PlannedNav,
    materialized_navigation_state,
)
from squid_layouts.planning.target import EMPTY_COST, ResourceCost
from squid_layouts.primitives.constraints import Alts, Condense, Drop, Never, Overflow, Paginate, Spill, Truncate
from squid_layouts.primitives.nodes import (
    ActionGroup,
    Boundary,
    Break,
    Budget,
    Button,
    EntitySelect,
    Card,
    CardMedia,
    CardText,
    Code,
    Content,
    File,
    Footer,
    Gallery,
    Heading,
    Lines,
    LinkButton,
    MediaCollection,
    Node,
    Option,
    Panel,
    RawItem,
    RoutedButton,
    RoutedSelect,
    Row,
    Section,
    SelectMenu,
    Sep,
    Text,
    Thumbnail,
    Time,
    Variants,
    ZonedTime,
    card_text,
)
from squid_layouts.primitives.styles import Color
from squid_layouts.sources import Position
from squid_layouts.temporal import ZonedDateTime
from squid_layouts.text import NEUTRAL, Localization

type TextBearing = Text | Heading | Footer | Code | Lines


class SolveNoteCode(StrEnum):
    """Stable identities for diagnostics emitted by the measured solver."""

    CLAMP_BUTTON_LABEL = "clamp.button_label"
    CLAMP_SELECT_OPTIONS = "clamp.select_options"
    CLAMP_SELECT_OPTION_TEXT = "clamp.select_option_text"
    CLAMP_SELECT_PLACEHOLDER = "clamp.select_placeholder"
    CLAMP_SECTION_TEXTS = "clamp.section_texts"
    CLAMP_GALLERY_ITEMS = "clamp.gallery_items"
    CLAMP_EMBED_TEXT = "clamp.embed_text"
    CLAMP_EMBED_FIELDS = "clamp.embed_fields"
    NODE_DROPPED = "degradation.node_dropped"
    ALTERNATE = "degradation.alternate"
    ALTERNATE_EXHAUSTED = "degradation.alternate_exhausted"
    TRUNCATED = "degradation.truncated"
    NEVER_CLAMPED = "degradation.never_clamped"
    CHROME_DROP = "degradation.chrome_drop"
    CONDENSED = "degradation.condensed"
    CONDENSE_TRUNCATED = "degradation.condense_truncated"
    SPILL_ALTERNATES = "degradation.spill_alternates"
    SPILLED = "degradation.spilled"
    SPILL_DROPPED = "degradation.spill_dropped"
    NEVER_BUDGET = "failure.never_budget"
    BUDGET_FLOOR = "failure.budget_floor"
    BEST_EFFORT_FLOOR = "degradation.best_effort_floor"
    VARIANT_STEP = "adaptation.variant_step"
    SEMANTIC_FALLBACK = "degradation.semantic_fallback"
    VARIANT_REFORMATTED = "degradation.variant_reformatted"
    VARIANT_LOSSY = "degradation.variant_lossy"
    PAGINATE_PER_FALLBACK = "degradation.paginate_per_fallback"
    COMPONENT_BUDGET = "degradation.component_budget"
    TEXT_BUDGET = "degradation.text_budget"


class SolveNoteSeverity(Enum):
    """How a solver note affects feasibility and reporting."""

    ADAPTATION = "adaptation"
    """The layout took another shape and lost nothing; `strict=True` accepts this.

    Stepping a ladder to an exact rung is the case that matters: paginating a long region
    or splitting it across cards shows every word the author wrote, so a caller who asked
    for no degradation has not been given any.
    """

    CLAMP = "clamp"
    DEGRADATION = "degradation"
    FAILURE = "failure"


@dataclass(frozen=True, slots=True)
class SolveNote:
    """A stable solver diagnostic whose meaning does not depend on message wording."""

    code: SolveNoteCode
    message: str
    severity: SolveNoteSeverity = SolveNoteSeverity.DEGRADATION

    def __str__(self) -> str:
        return self.message


def lossy_notes(notes: Sequence[SolveNote]) -> list[SolveNote]:
    """The notes `strict=True` refuses: everything that is not lossless adaptation."""
    return [note for note in notes if note.severity is not SolveNoteSeverity.ADAPTATION]


def _note(
    code: SolveNoteCode,
    message: str,
    severity: SolveNoteSeverity = SolveNoteSeverity.DEGRADATION,
) -> SolveNote:
    return SolveNote(code, message, severity)


class LayoutOverflowError(Exception):
    """The document cannot fit its hard constraints into Discord's budgets."""

    def __init__(self, notes: list[SolveNote]) -> None:
        super().__init__("; ".join(note.message for note in notes))
        self.notes = notes


# --- Realized tree: the same shapes with final strings, consumed by scene conversion -------


@dataclass(slots=True)
class RText:
    content: str = ""
    dropped: bool = False


@dataclass(frozen=True, slots=True)
class RTime:
    instant: datetime
    style: str
    prefix: str | None = None


@dataclass(frozen=True, slots=True)
class RZonedTime:
    value: ZonedDateTime
    prefix: str | None = None


@dataclass(frozen=True, slots=True)
class RSection:
    texts: list[RText]
    accessory: Thumbnail | LinkButton | RawItem


@dataclass(frozen=True, slots=True)
class RPanel:
    children: list[Realized]
    accent: Color | None


@dataclass(frozen=True, slots=True)
class RGroup:
    """A transparent realized group removed before scene conversion."""

    children: list[Realized]


@dataclass(frozen=True, slots=True)
class RContent:
    """The realized `content` field, whose text was allocated from its own pool."""

    slot: RText


@dataclass(frozen=True, slots=True)
class RCardField:
    name: RText
    value: RText
    inline: bool


@dataclass(frozen=True, slots=True)
class RCard:
    """One realized embed. Every slot already holds its final, allocated string."""

    title: RText | None
    url: str | None
    blocks: list[Realized]
    """Description blocks, joined by the dialect once their text is allocated."""
    fields: list[RCardField]
    footer: RText | None
    footer_icon: str | None
    author: RText | None
    author_url: str | None
    author_icon: str | None
    accent: Color | None
    image: CardMedia | None
    thumbnail: CardMedia | None
    timestamp: ZonedDateTime | datetime | None


type Realized = (
    RText
    | RTime
    | RZonedTime
    | RSection
    | RPanel
    | RGroup
    | RCard
    | RContent
    | File
    | Sep
    | Row
    | SelectMenu
    | EntitySelect
    | RoutedSelect
    | Thumbnail
    | Gallery
    | RawItem
)


PAGE_FOOTER_PREFIX = "-# "


@dataclass(slots=True)
class Pager:
    """Page state for one keyed Paginate node that overflowed."""

    key: str
    slot: RText
    prefix: str
    suffix: str
    fragments: list[str]
    footer_slot: RText
    footer: Callable[[int, int], str]
    axis: str = DISPLAY_TEXT
    """The text pool this pager's body and footer draw from."""
    initial: int = 0
    """The page to open on; a mount adopts this before its first render."""
    page: int = 0
    nav_host: list[Realized] | None = None
    """The realized list holding this pager's nav, so `repage` can replace it in place."""
    nav_at: int = 0
    nav_count: int = 0

    @property
    def pages(self) -> int:
        return len(self.fragments)

    def select(self, index: int) -> int:
        """Render page ``index`` (clamped) into the document; returns the page shown."""
        index = max(0, min(index, self.pages - 1))
        self.slot.content = self.prefix + self.fragments[index] + self.suffix
        self.footer_slot.content = PAGE_FOOTER_PREFIX + self.footer(index + 1, self.pages)
        self.page = index
        return index


@dataclass(frozen=True, slots=True)
class MeasuredLayout:
    """One concrete primitive layout, measured against one target's budgets."""

    children: list[Realized]
    notes: list[SolveNote]
    pagers: tuple[Pager, ...] = ()
    cost: ResourceCost = EMPTY_COST
    """Everything this layout spends, per named axis, including every pager's controls.

    One cost rather than a components scalar beside a text scalar: a target with two text
    pools has no single number to report, and a caller asking "does this fit?" has to ask
    it of every axis the target budgets or it is not asking the question at all.
    """
    overflowed: bool = False
    """Whether anything had to give to fit, as opposed to being clamped on the way in.

    Not every note is a defeat. Trimming a select's options to 25 or a section's texts to
    3 is Discord's shape being enforced and happens whatever the budget; degrading,
    spilling, dropping or stepping a ladder means the content did not fit. A caller
    deciding whether more will fit — the root packer — needs to tell those apart.
    """
    nav: PlannedNav | None = None
    chrome: Chrome = DEFAULT_CHROME
    limits: DiscordLimits = LIMITS
    degradation: DegradationProfile = field(default_factory=DegradationProfile)

    def fits(self, capacities: Mapping[str, int]) -> bool:
        """Whether every budgeted axis is within its cap."""
        return self.cost.within(capacities)

    @property
    def failures(self) -> tuple[SolveNote, ...]:
        """Constraint failures that make this solution unusable without another rung."""
        return tuple(note for note in self.notes if note.severity is SolveNoteSeverity.FAILURE)

    def reposition(self, positions: Mapping[str, Position]) -> None:
        """Show a different position in each named pager without re-fitting.

        Which page is showing is a display decision, not a layout one: every fragment
        already fits the grant its pager was allocated, the footer reservation was
        measured at its widest, and a nav factory may not vary its shape by page. So a
        caller that only learns where the reader belongs *after* fitting — which is
        anyone reconciling against a stored cursor, since the page count is an output —
        can move the page here instead of measuring again.
        """
        for pager in self.pagers:
            position = positions.get(pager.key)
            if position is None:
                continue
            shown = pager.select(position.offset)
            if self.nav is None or pager.nav_host is None:
                continue
            window = slice(pager.nav_at, pager.nav_at + pager.nav_count)
            previous = pager.nav_host[window]
            realized = _Builder(limits=self.limits).realize_children(
                _validated_nav(
                    self.nav(materialized_navigation_state(pager.key, Position(offset=shown), pager.pages, self.chrome))
                )
            )
            if len(realized) != pager.nav_count or _component_count(realized) != _component_count(previous):
                message = (
                    f"nav factory changed shape between pages of {pager.key!r}; "
                    "disable controls at the ends instead of hiding them"
                )
                raise LayoutInvariantError(message)
            pager.nav_host[window] = realized

    @property
    def pager(self) -> Pager | None:
        """The first pager, for single-pager callers."""
        return self.pagers[0] if self.pagers else None

    @property
    def page(self) -> int:
        return self.pager.page if self.pager is not None else 0

    @property
    def pages(self) -> int:
        return self.pager.pages if self.pager is not None else 1


# --- Text units -----------------------------------------------------------------------------


@dataclass(slots=True)
class _Unit:
    """One text-bearing node's mutable allocation state."""

    node: TextBearing
    slot: RText
    index: int
    axis: str
    """Which message-wide text pool this unit draws from.

    Set where the unit is realized, because that is the only place that knows whether it
    sits in a card, in message content, or in the message body itself. Pools never lend to
    each other, so this decides which allocation it takes part in.
    """
    prefix: str
    suffix: str
    content: str
    ladders: tuple[tuple[str, ...], ...] | None
    ranks: tuple[int, ...]
    """One drop priority per ladder; empty for nodes that are not entry lists."""
    join: str
    priority: int
    overflow: Overflow
    grant: int = 0
    fragments: list[str] | None = None
    count_pages: list[str] | None = None
    """Count-based pages, when the node paginates every N entries rather than on overflow."""

    @property
    def chrome_len(self) -> int:
        return len(self.prefix) + len(self.suffix)

    @property
    def need(self) -> int:
        # A count-paginated node only ever shows one page, so that is all it asks for.
        if self.count_pages is not None:
            return self.chrome_len + max(len(page) for page in self.count_pages)
        return self.chrome_len + len(self.content)


@dataclass(frozen=True, slots=True)
class _BudgetRegion:
    units: tuple[_Unit, ...]
    minimum: int
    preferred: int
    stretch: int
    best_effort: bool

    @property
    def axis(self) -> str | None:
        """The single text pool this region reserves from, or None if it holds no text.

        A `Budget` states one preferred size. Applying that same size independently to two
        pools would silently double the author's reservation, so a region spanning pools is
        rejected rather than guessed at.
        """
        axes = {unit.axis for unit in self.units}
        if len(axes) > 1:
            named = ", ".join(sorted(axes))
            message = f"a Budget region spans the text axes {named}; give each axis its own Budget"
            raise LayoutInvariantError(message)
        return next(iter(axes), None)


def _escape_fences(content: str) -> str:
    # A closing fence inside the content would end the block early; break it invisibly.
    return content.replace("```", "``\N{ZERO WIDTH SPACE}`")


def _make_unit(node: TextBearing, slot: RText, index: int, axis: str = DISPLAY_TEXT) -> _Unit | None:
    prefix, suffix, ladders, join = "", "", None, "\n"
    ranks: tuple[int, ...] = ()
    match node:
        case Text(content=content):
            pass
        case Heading(content=content, level=level):
            prefix = "#" * level + " "
        case Footer(content=content):
            prefix = "-# "
        case Code(content=content, lang=lang):
            prefix = f"```{lang}\n"
            suffix = "\n```"
            content = _escape_fences(content)
        case Lines(lines=raw_lines, join=join):
            entries = [
                ((entry,), 0) if isinstance(entry, str) else (entry.steps, entry.priority) for entry in raw_lines
            ]
            kept = [(ladder, rank) for ladder, rank in entries if ladder[0]]
            ladders = tuple(ladder for ladder, _ in kept)
            ranks = tuple(rank for _, rank in kept)
            content = join.join(ladder[0] for ladder in ladders)
    if not content:
        slot.dropped = True
        return None
    return _Unit(
        node=node,
        slot=slot,
        index=index,
        axis=axis,
        prefix=prefix,
        suffix=suffix,
        content=content,
        ladders=ladders,
        ranks=ranks,
        join=join,
        priority=node.priority,
        overflow=node.overflow,
    )


def _hard_split(segment: str, limit: int) -> list[str]:
    return [segment[start : start + limit] for start in range(0, len(segment), limit)]


def split_pages(
    text: str,
    limit: int,
    boundary: str = "\n",
    *,
    min_fill: int = 0,
    widows: int = 1,
) -> list[str]:
    """Balance ``text`` across the fewest bounded pages, preferring semantic cuts."""
    if limit < 1:
        message = "page limit must be positive"
        raise ValueError(message)
    chunks: list[tuple[str, str]] = []
    for segment_index, segment in enumerate(text.split(boundary)):
        separator = boundary if segment_index else ""
        pieces = _hard_split(segment, limit) if len(segment) > limit else [segment]
        for piece_index, piece in enumerate(pieces):
            chunks.append((separator if piece_index == 0 else "", piece))
    if not chunks:
        return [""]

    def page_text(start: int, end: int) -> str:
        return chunks[start][1] + "".join(separator + content for separator, content in chunks[start + 1 : end])

    cuts = balanced_breaks(
        [BreakItem(len(content), leading_chars=len(separator)) for separator, content in chunks],
        max_chars=limit,
        min_fill=min_fill,
        widows=widows,
        ideal_total=len(text),
    )
    result: list[str] = []
    start = 0
    for end in cuts:
        result.append(page_text(start, end))
        start = end
    return result or [""]


def _trim_keep(text: str, limit: int, keep: str) -> str:
    if len(text) <= limit:
        return text
    if limit <= 1:
        return ELLIPSIS if limit == 1 else ""
    if keep == "tail":
        return ELLIPSIS + text[-(limit - 1) :].lstrip()
    return text[: limit - 1].rstrip() + ELLIPSIS


def text_total(cost: ResourceCost) -> int:
    """Every text axis a cost spends on, added up.

    Safe to add only because a single region lives in a single pool: a card's text is all
    `embed_text`, a content node's is all `content_text`. Callers reasoning about one region
    want its size; callers reasoning about a whole message must ask per axis instead.
    """
    return sum(value for axis, value in cost.values.items() if axis in TEXT_AXES)


def measure_nodes(nodes: Sequence[Node], *, limits: DiscordLimits = LIMITS) -> ResourceCost:
    """Measure preferred cost per named axis, without applying any budget pressure."""

    def lower_shape(node: Node) -> list[Node]:
        match node:
            case ActionGroup(items=items):
                return [
                    Row(tuple(items[start : start + limits.row_buttons]))
                    for start in range(0, len(items), limits.row_buttons)
                ]
            case MediaCollection(urls=urls):
                return [
                    Gallery(tuple(urls[start : start + limits.gallery_items]))
                    for start in range(0, len(urls), limits.gallery_items)
                ]
            case (
                Panel(children=children)
                | Budget(children=children)
                | Break(children=children)
                | Card(children=children)
            ):
                return [replace(node, children=tuple(child for item in children for child in lower_shape(item)))]
            case Variants(variants=variants):
                # Preferred cost, so every ladder is priced on the rung it opens at.
                return [child for item in variants[0].nodes for child in lower_shape(item)]
            case _:
                return [node]

    lowered = [child for node in nodes for child in lower_shape(node)]
    builder = _Builder(limits=limits)
    children = builder.realize_children(lowered)
    text = dict(builder.raw_text_cost)
    for unit in builder.units:
        text[unit.axis] = text.get(unit.axis, 0) + unit.need
    return ResourceCost({**text, **_structural_cost(children)})


def split_text_node(
    node: Node,
    limit: int,
    *,
    min_fill: int = 0,
    widows: int = 1,
) -> tuple[Node, ...] | None:
    """Losslessly split one text primitive into independently renderable fragments."""
    if not isinstance(node, Text | Heading | Footer | Code | Lines):
        return None
    slot = RText()
    unit = _make_unit(node, slot, 0)
    if unit is None:
        return (node,)
    usable = limit - unit.chrome_len
    if usable < 1:
        return None
    boundary = node.join if isinstance(node, Lines) else "\n"
    fragments = split_pages(unit.content, usable, boundary, min_fill=min_fill, widows=widows)
    if len(fragments) <= 1:
        return (node,)
    if isinstance(node, Lines):
        return tuple(Text(fragment, overflow=Never(), priority=node.priority) for fragment in fragments)
    return tuple(replace(node, content=fragment, overflow=Never()) for fragment in fragments)


# --- Solve ----------------------------------------------------------------------------------


@dataclass(slots=True)
class _Builder:
    limits: DiscordLimits = LIMITS
    notes: list[SolveNote] = field(default_factory=list)
    units: list[_Unit] = field(default_factory=list)
    raw_text_cost: dict[str, int] = field(default_factory=dict)
    """Text no overflow policy can shrink, per axis: timestamps and prepared native items."""
    budgets: list[_BudgetRegion] = field(default_factory=list)
    axis: str = DISPLAY_TEXT
    """The pool text realized right now draws from; target shape moves it, nothing else."""

    def charge(self, characters: int) -> None:
        self.raw_text_cost[self.axis] = self.raw_text_cost.get(self.axis, 0) + characters

    def unit(self, node: TextBearing, slot: RText) -> None:
        made = _make_unit(node, slot, len(self.units), self.axis)
        if made is not None:
            self.units.append(made)

    @contextmanager
    def pool(self, axis: str) -> Iterator[None]:
        """Realize everything inside this block against another text pool."""
        previous = self.axis
        self.axis = axis
        try:
            yield
        finally:
            self.axis = previous

    def slot(self, value: CardText | None, cap: int, what: str) -> RText | None:
        """Realize one card text slot, clamping it to its own local cap first.

        Local caps are the target's shape, not its remaining room: an embed title is 256
        characters whatever else the message holds. A slot that cannot shrink and does not
        fit is rejected by the dialect's validation before this ever runs, so anything
        trimmed here asked to be.
        """
        if value is None:
            return None
        node = card_text(value)
        content = node.content.strip()
        if not content:
            return None
        if len(content) > cap:
            self.notes.append(
                _note(
                    SolveNoteCode.CLAMP_EMBED_TEXT,
                    f"{what} clamped from {len(content)} to {cap}",
                    SolveNoteSeverity.CLAMP,
                )
            )
            content = _trim_keep(content, cap, "head")
        slot = RText()
        self.unit(replace(node, content=content), slot)
        return slot

    def card(self, node: Card) -> RCard:
        limits = self.limits
        fields = node.fields[: getattr(limits, "embed_fields", len(node.fields))]
        if len(fields) != len(node.fields):
            self.notes.append(
                _note(
                    SolveNoteCode.CLAMP_EMBED_FIELDS,
                    f"card holds {len(node.fields)} fields; keeping {len(fields)}",
                    SolveNoteSeverity.CLAMP,
                )
            )
        realized_fields: list[RCardField] = []
        for field_node in fields:
            name = self.slot(field_node.name, getattr(limits, "field_name", 256), "field name")
            value = self.slot(field_node.value, getattr(limits, "field_value", 1024), "field value")
            if name is None or value is None:
                # Discord rejects an empty field name or value outright, and a field with
                # neither is not a smaller field, it is a different document.
                message = "a CardField needs a non-empty name and value after trimming"
                raise LayoutInvariantError(message)
            realized_fields.append(RCardField(name, value, field_node.inline))
        return RCard(
            title=self.slot(node.title, getattr(limits, "embed_title", 256), "embed title"),
            url=node.url,
            blocks=self.realize_children(node.children),
            fields=realized_fields,
            footer=None
            if node.footer is None
            else self.slot(node.footer.text, getattr(limits, "embed_footer", 2048), "embed footer"),
            footer_icon=None if node.footer is None else node.footer.icon_url,
            author=None
            if node.author is None
            else self.slot(node.author.name, getattr(limits, "embed_author", 256), "embed author"),
            author_url=None if node.author is None else node.author.url,
            author_icon=None if node.author is None else node.author.icon_url,
            accent=node.accent,
            image=node.image,
            thumbnail=node.thumbnail,
            timestamp=node.timestamp,
        )

    def _clamp_button[ButtonT: Button | LinkButton | RoutedButton](self, button: ButtonT) -> ButtonT:
        if len(button.label) <= self.limits.button_label:
            return button
        self.notes.append(
            _note(
                SolveNoteCode.CLAMP_BUTTON_LABEL,
                f"button label clamped from {len(button.label)}",
                SolveNoteSeverity.CLAMP,
            )
        )
        trimmed = _trim_keep(button.label, self.limits.button_label, "head")
        return replace(button, label=trimmed)

    def _clamp_select[SelectT: SelectMenu | RoutedSelect](self, select: SelectT) -> SelectT:
        limits = self.limits
        options = select.options
        if len(options) > limits.select_options:
            self.notes.append(
                _note(
                    SolveNoteCode.CLAMP_SELECT_OPTIONS,
                    f"{len(options)} select options clamped to {limits.select_options}",
                    SolveNoteSeverity.CLAMP,
                )
            )
            options = options[: limits.select_options]
        clamped_options = []
        for option in options:
            label = _trim_keep(option.label, limits.option_label, "head")
            value = option.value[: limits.option_value]
            description = option.description
            if description is not None and len(description) > limits.option_description:
                description = _trim_keep(description, limits.option_description, "head")
            if (label, value, description) != (option.label, option.value, option.description):
                self.notes.append(
                    _note(SolveNoteCode.CLAMP_SELECT_OPTION_TEXT, "select option text clamped", SolveNoteSeverity.CLAMP)
                )
                option = Option(label=label, value=value, description=description, default=option.default)
            clamped_options.append(option)
        placeholder = select.placeholder
        if placeholder is not None and len(placeholder) > limits.select_placeholder:
            self.notes.append(
                _note(
                    SolveNoteCode.CLAMP_SELECT_PLACEHOLDER,
                    f"select placeholder clamped from {len(placeholder)}",
                    SolveNoteSeverity.CLAMP,
                )
            )
            placeholder = _trim_keep(placeholder, limits.select_placeholder, "head")
        return replace(
            select,
            options=tuple(clamped_options),
            placeholder=placeholder,
            max_values=min(select.max_values, len(clamped_options) or 1),
        )

    def _clamp_entity_select(self, select: EntitySelect) -> EntitySelect:
        placeholder = select.placeholder
        if placeholder is not None and len(placeholder) > self.limits.select_placeholder:
            self.notes.append(
                _note(
                    SolveNoteCode.CLAMP_SELECT_PLACEHOLDER,
                    f"select placeholder clamped from {len(placeholder)}",
                    SolveNoteSeverity.CLAMP,
                )
            )
            placeholder = _trim_keep(placeholder, self.limits.select_placeholder, "head")
        return replace(select, placeholder=placeholder)

    def realize_children(self, nodes: Sequence[Node]) -> list[Realized]:
        return [self.realize(node) for node in nodes]

    def realize(self, node: Node) -> Realized:
        match node:
            case Text() | Heading() | Footer() | Code() | Lines():
                slot = RText()
                self.unit(node, slot)
                return slot
            case Time(instant=instant, style=style, prefix=prefix):
                unix = int(instant.timestamp())
                self.charge(len(prefix or "") + len(f"<t:{unix}:{style}>"))
                return RTime(instant, style, prefix)
            case ZonedTime(value=value, prefix=prefix):
                self.charge(len(prefix or "") + len(value.isoformat()))
                return RZonedTime(value, prefix)
            case Section(texts=texts, accessory=accessory):
                if len(texts) > 3:
                    self.notes.append(
                        _note(
                            SolveNoteCode.CLAMP_SECTION_TEXTS,
                            f"section holds {len(texts)} texts; keeping 3",
                            SolveNoteSeverity.CLAMP,
                        )
                    )
                    texts = texts[:3]
                slots: list[RText] = []
                for text_node in texts:
                    slot = RText()
                    self.unit(text_node, slot)
                    slots.append(slot)
                if isinstance(accessory, RawItem):
                    self.charge(accessory.text_cost)
                return RSection(texts=slots, accessory=accessory)
            case Content(content=text, overflow=overflow, priority=priority):
                slot = RText()
                # Message content is its own pool. Nothing in an embed can borrow from it and
                # nothing here can borrow from them, which is the whole point of the split.
                with self.pool(CONTENT_TEXT):
                    self.unit(Text(text, overflow=overflow, priority=priority), slot)
                return RContent(slot)
            case Card():
                with self.pool(EMBED_TEXT):
                    return self.card(node)
            case Panel(children=children, accent=accent):
                return RPanel(children=self.realize_children(children), accent=accent)
            case Budget(
                children=children,
                minimum=minimum,
                preferred=preferred,
                stretch=stretch,
                best_effort=best_effort,
            ):
                first = len(self.units)
                realized = self.realize_children(children)
                self.budgets.append(_BudgetRegion(tuple(self.units[first:]), minimum, preferred, stretch, best_effort))
                return RGroup(realized)
            case Break(children=children):
                return RGroup(self.realize_children(children))
            case Gallery(urls=urls):
                if len(urls) > 10:
                    self.notes.append(
                        _note(
                            SolveNoteCode.CLAMP_GALLERY_ITEMS,
                            f"gallery holds {len(urls)} items; keeping 10",
                            SolveNoteSeverity.CLAMP,
                        )
                    )
                    node = Gallery(urls=urls[:10])
                return node
            case Row(items=items):
                self.charge(sum(item.text_cost for item in items if isinstance(item, RawItem)))
                clamped = tuple(
                    self._clamp_button(item) if isinstance(item, Button | LinkButton | RoutedButton) else item
                    for item in items
                )
                return Row(items=clamped)
            case SelectMenu() | RoutedSelect():
                return self._clamp_select(node)
            case EntitySelect():
                return self._clamp_entity_select(node)
            case RawItem(text_cost=text_cost):
                self.charge(text_cost)
                return node
            case Boundary():
                message = "Boundary must be expanded before solving"
                raise ValueError(message)
            case Variants():
                message = "Variants must be resolved before measuring; plan() owns that choice"
                raise LayoutInvariantError(message)
            case _:
                return node


def _apply(unit: _Unit, chrome: Chrome, notes: list[SolveNote], degradation: DegradationRecorder) -> bool:
    """Render the unit into its slot within its grant. Returns False when the node drops."""
    if unit.count_pages is not None:
        return _apply_count_pages(unit)
    if unit.fragments is not None and unit.grant >= unit.chrome_len + max(map(len, unit.fragments)):
        unit.slot.content = unit.prefix + unit.fragments[0] + unit.suffix
        return True
    if unit.grant >= unit.need:
        unit.slot.content = unit.prefix + unit.content + unit.suffix
        return True

    usable = unit.grant - unit.chrome_len
    match unit.overflow:
        case Drop():
            notes.append(
                _note(SolveNoteCode.NODE_DROPPED, f"dropped node {unit.index} ({unit.need} chars over budget)")
            )
            degradation.record(priority=unit.priority, path=f"$.text.{unit.index}", dropped_nodes=1)
            return False
        case Spill() if unit.ladders is not None:
            return _apply_spill(unit, usable, chrome, notes, degradation)
        case Condense() if usable >= 1:
            return _apply_condense(unit, usable, notes, degradation)
        case Alts(ladder=ladder) if usable >= 1:
            for step, alternate in enumerate(ladder, 1):
                if alternate and len(alternate) <= usable:
                    notes.append(
                        _note(
                            SolveNoteCode.ALTERNATE,
                            f"node {unit.index} degraded to a {len(alternate)}-char alternate",
                        )
                    )
                    degradation.record(
                        priority=unit.priority,
                        path=f"$.text.{unit.index}",
                        semantic_steps=step,
                    )
                    unit.slot.content = unit.prefix + alternate + unit.suffix
                    return True
            fallback = ladder[-1] if ladder else unit.content
            notes.append(
                _note(
                    SolveNoteCode.ALTERNATE_EXHAUSTED,
                    f"node {unit.index} exhausted its ladder; trimming the last alternate",
                )
            )
            degradation.record(
                priority=unit.priority,
                path=f"$.text.{unit.index}",
                semantic_steps=len(ladder),
                truncated_chars=max(0, len(fallback) - usable),
            )
            unit.slot.content = unit.prefix + _trim_keep(fallback, usable, "head") + unit.suffix
            return True
        case Paginate(boundary=boundary, min_fill=min_fill, widows=widows) if usable >= 1:
            # Pagination is the policy working as intended, not a degradation: no note.
            unit.fragments = split_pages(unit.content, usable, boundary, min_fill=min_fill, widows=widows)
            unit.slot.content = unit.prefix + unit.fragments[0] + unit.suffix
            return True
        case Truncate(keep=keep) if usable >= 1:
            notes.append(
                _note(
                    SolveNoteCode.TRUNCATED,
                    f"trimmed node {unit.index} from {len(unit.content)} to {usable}",
                )
            )
            degradation.record(
                priority=unit.priority,
                path=f"$.text.{unit.index}",
                truncated_chars=max(0, len(unit.content) - usable),
            )
            unit.slot.content = unit.prefix + _trim_keep(unit.content, usable, keep) + unit.suffix
            return True
        case Never() if usable >= 1:
            notes.append(
                _note(
                    SolveNoteCode.NEVER_CLAMPED,
                    f"clamped Never node {unit.index}: needed {unit.need}, granted {unit.grant}",
                )
            )
            unit.slot.content = unit.prefix + _trim_keep(unit.content, usable, "head") + unit.suffix
            return True
        case _:
            notes.append(
                _note(
                    SolveNoteCode.CHROME_DROP,
                    f"dropped node {unit.index}: grant {unit.grant} cannot cover chrome {unit.chrome_len}",
                )
            )
            degradation.record(priority=unit.priority, path=f"$.text.{unit.index}", dropped_nodes=1)
            return False


def _apply_count_pages(unit: _Unit) -> bool:
    """Realize a count-paginated node: N entries per page, budget-split if a page is too big.

    Count pages are the author's UX pin rather than a degradation, so this fires whether or
    not the budget is tight and adds no note.
    """
    pages = unit.count_pages or [""]
    usable = max(1, unit.grant - unit.chrome_len)
    fragments: list[str] = []
    for page in pages:
        policy = unit.overflow
        assert isinstance(policy, Paginate)
        fragments.extend(
            [page]
            if len(page) <= usable
            else split_pages(page, usable, unit.join, min_fill=policy.min_fill, widows=policy.widows)
        )
    unit.fragments = fragments
    unit.slot.content = unit.prefix + fragments[0] + unit.suffix
    return True


def _step_ladders(ladders: tuple[tuple[str, ...], ...], join: str, usable: int) -> list[int]:
    """Pick each entry's ladder rung, stepping the largest entry down until the block fits.

    Shared by `Spill` and `Condense`: both shrink entries the same way and differ only in
    what they do once every ladder is exhausted. Returns one rung index per entry, which is
    0 for an entry that never had to move.
    """
    levels = [0] * len(ladders)
    total = sum(len(ladder[0]) for ladder in ladders) + max(0, len(ladders) - 1) * len(join)
    candidates: list[tuple[int, int]] = []
    for index, ladder in enumerate(ladders):
        if len(ladder) > 1:
            heappush(candidates, (-len(ladder[0]), index))
    while total > usable and candidates:
        _negative_length, largest = heappop(candidates)
        level = levels[largest]
        before = len(ladders[largest][level])
        level += 1
        levels[largest] = level
        after = len(ladders[largest][level])
        total -= before - after
        if level + 1 < len(ladders[largest]):
            heappush(candidates, (-after, largest))
    return levels


def _apply_condense(
    unit: _Unit,
    usable: int,
    notes: list[SolveNote],
    degradation: DegradationRecorder,
) -> bool:
    """Shorten entries as far as their ladders go, then trim; never lose a whole entry."""
    ladders = unit.ladders or ((unit.content,),)
    levels = _step_ladders(ladders, unit.join, usable)
    body = unit.join.join(ladder[level] for ladder, level in zip(ladders, levels, strict=True))
    stepped = sum(1 for level in levels if level)
    if stepped:
        notes.append(
            _note(
                SolveNoteCode.CONDENSED,
                f"node {unit.index} condensed {stepped} of {len(ladders)} entries down their ladders",
            )
        )
        degradation.record(
            priority=unit.priority,
            path=f"$.text.{unit.index}",
            semantic_steps=sum(levels),
        )
    if len(body) > usable:
        notes.append(
            _note(
                SolveNoteCode.CONDENSE_TRUNCATED,
                f"condensed node {unit.index} exhausted its ladders; trimming from {len(body)} to {usable}",
            )
        )
        degradation.record(
            priority=unit.priority,
            path=f"$.text.{unit.index}",
            truncated_chars=len(body) - usable,
        )
        body = _trim_keep(body, usable, "head")
    unit.slot.content = unit.prefix + body + unit.suffix
    return True


def _apply_spill(
    unit: _Unit,
    usable: int,
    chrome: Chrome,
    notes: list[SolveNote],
    degradation: DegradationRecorder,
) -> bool:
    ladders = unit.ladders or ()
    total = len(ladders)
    # First degrade the largest entries down their ladders; spill whole entries only after
    # every ladder is exhausted.
    levels = _step_ladders(ladders, unit.join, usable)
    degraded = any(levels)

    def entry(index: int) -> str:
        return ladders[index][levels[index]]

    # Stepping is about fitting, so it takes the largest entries first; dropping is about
    # what the reader can afford to lose, so it takes the lowest priority first (ties from
    # the tail, which is where a list's least important entries conventionally sit).
    drop_order = sorted(range(total), key=lambda i: (unit.ranks[i] if unit.ranks else 0, -i))
    entry_lengths = [len(entry(index)) for index in range(total)]
    remaining_chars = sum(entry_lengths)
    for dropped in range(total + 1):
        marker = chrome.and_n_more(dropped) if dropped else ""
        shown_entries = total - dropped
        output_items = shown_entries + int(bool(marker))
        body_length = remaining_chars + len(marker) + max(0, output_items - 1) * len(unit.join)
        if body_length and body_length <= usable:
            omitted = set(drop_order[:dropped])
            shown = [entry(index) for index in range(total) if index not in omitted]
            if marker:
                shown.append(marker)
            body = unit.join.join(shown)
            if degraded:
                notes.append(
                    _note(
                        SolveNoteCode.SPILL_ALTERNATES,
                        f"node {unit.index} degraded {sum(1 for lvl in levels if lvl)} entries down their ladders",
                    )
                )
                degradation.record(
                    priority=unit.priority,
                    path=f"$.text.{unit.index}",
                    semantic_steps=sum(levels),
                )
            if dropped:
                notes.append(
                    _note(
                        SolveNoteCode.SPILLED,
                        f"spilled node {unit.index}: showing {total - dropped} of {total} lines",
                    )
                )
                degradation.record(
                    priority=unit.priority,
                    path=f"$.text.{unit.index}",
                    spilled_items=dropped,
                )
            unit.slot.content = unit.prefix + body + unit.suffix
            return True
        if dropped < total:
            remaining_chars -= entry_lengths[drop_order[dropped]]
    notes.append(_note(SolveNoteCode.SPILL_DROPPED, f"dropped node {unit.index}: no line fits in {usable}"))
    degradation.record(priority=unit.priority, path=f"$.text.{unit.index}", dropped_nodes=1)
    return False


def _allocate(
    units: list[_Unit],
    budget: int,
    notes: list[SolveNote],
    chrome: Chrome,
    degradation: DegradationRecorder,
) -> None:
    active = list(units)
    for _ in range(len(units) + 1):
        remaining = budget
        # Never and Condense nodes are fixed costs: both promise to keep every entry, so
        # they are charged before any flexible node sees the budget. Never goes first —
        # a heading or a paragraph outranks a field block that can still condense — and
        # only Never's shortfall is reported, because only Never's shortfall is a defeat.
        overdraw = 0
        for unit in active:
            if isinstance(unit.overflow, Never):
                unit.grant = min(unit.need, max(0, remaining))
                overdraw += unit.need - unit.grant
                remaining -= unit.grant
        if overdraw:
            notes.append(
                _note(
                    SolveNoteCode.NEVER_BUDGET,
                    f"Never nodes need {budget + overdraw} of {budget} available characters",
                    SolveNoteSeverity.FAILURE,
                )
            )
        for unit in active:
            if isinstance(unit.overflow, Condense):
                unit.grant = min(unit.need, max(0, remaining))
                remaining -= unit.grant
        flexible = [unit for unit in active if not isinstance(unit.overflow, Never | Condense)]
        for priority in sorted({unit.priority for unit in flexible}, reverse=True):
            group = [unit for unit in flexible if unit.priority == priority]
            total_need = sum(unit.need for unit in group)
            if total_need <= remaining:
                for unit in group:
                    unit.grant = unit.need
            else:
                # Share the shortfall proportionally to need instead of first-come-take-all,
                # so document order does not decide which same-priority node starves.
                share = max(0, remaining)
                for unit in group:
                    unit.grant = unit.need * share // total_need
                leftover = share - sum(unit.grant for unit in group)
                for unit in sorted(group, key=lambda u: u.index):
                    if leftover <= 0:
                        break
                    top_up = min(leftover, unit.need - unit.grant)
                    unit.grant += top_up
                    leftover -= top_up
            remaining -= sum(unit.grant for unit in group)
        iteration = DegradationRecorder.create()
        dropped = [unit for unit in active if not _apply(unit, chrome, notes, iteration)]
        if not dropped:
            degradation.effects.extend(iteration.effects)
            return
        dropped_paths = {f"$.text.{unit.index}" for unit in dropped}
        degradation.effects.extend(effect for effect in iteration.effects if effect.path in dropped_paths)
        for unit in dropped:
            unit.slot.dropped = True
            active.remove(unit)
    # The loop always terminates by emptying `active`; each pass removes at least one unit.


@dataclass(slots=True)
class _GrantGroup:
    units: tuple[_Unit, ...]
    floor: int
    demand: int
    priority: int
    best_effort: bool = False
    grant: int = 0


def _allocate_budgeted(
    regions: Sequence[_BudgetRegion],
    units: Sequence[_Unit],
    budget: int,
    notes: list[SolveNote],
    chrome: Chrome,
    degradation: DegradationRecorder,
) -> None:
    """Allocate transparent budget regions as siblings, then solve inside each grant."""
    claimed: set[int] = set()
    groups: list[_GrantGroup] = []
    # Outer regions are recorded after their descendants. Claiming them first gives a
    # nested declaration one owner instead of charging the same unit twice.
    for region in reversed(regions):
        units = tuple(unit for unit in region.units if unit.index not in claimed)
        if not units:
            continue
        claimed.update(unit.index for unit in units)
        need = sum(unit.need for unit in units)
        ceiling = region.preferred + region.stretch
        demand = need if need <= ceiling else min(need, region.preferred)
        if len(units) == 1 and need > ceiling and isinstance(units[0].overflow, Paginate):
            unit = units[0]
            usable = ceiling - unit.chrome_len
            if usable >= 1:
                policy = unit.overflow
                unit.fragments = split_pages(
                    unit.content,
                    usable,
                    policy.boundary,
                    min_fill=policy.min_fill,
                    widows=policy.widows,
                )
                demand = unit.chrome_len + max(map(len, unit.fragments))
        groups.append(
            _GrantGroup(
                units,
                min(region.minimum, demand),
                demand,
                max(unit.priority for unit in units),
                region.best_effort,
            )
        )
    for unit in units:
        if unit.index in claimed:
            continue
        fixed = isinstance(unit.overflow, Never | Condense)
        groups.append(_GrantGroup((unit,), unit.need if fixed else 0, unit.need, unit.priority))
    groups.sort(key=lambda group: min(unit.index for unit in group.units))

    remaining = max(0, budget)
    hard_floor = sum(group.floor for group in groups if not group.best_effort)
    if hard_floor > remaining:
        notes.append(
            _note(
                SolveNoteCode.BUDGET_FLOOR,
                f"Budget floors need {hard_floor} of {remaining} available characters",
                SolveNoteSeverity.FAILURE,
            )
        )

    for group in groups:
        if group.best_effort:
            continue
        group.grant = min(group.floor, remaining)
        remaining -= group.grant
    for group in groups:
        if not group.best_effort:
            continue
        group.grant = min(group.floor, remaining)
        remaining -= group.grant
        if group.grant < group.floor:
            notes.append(
                _note(
                    SolveNoteCode.BEST_EFFORT_FLOOR,
                    f"breached best-effort budget floor {group.floor} with a {group.grant}-character grant",
                )
            )

    for priority in sorted({group.priority for group in groups}, reverse=True):
        peers = [group for group in groups if group.priority == priority and group.grant < group.demand]
        wanted = sum(group.demand - group.grant for group in peers)
        share = min(remaining, wanted)
        if wanted:
            distributed = 0
            for group in peers:
                extra = (group.demand - group.grant) * share // wanted
                group.grant += extra
                distributed += extra
            leftover = share - distributed
            for group in peers:
                if leftover <= 0:
                    break
                top_up = min(leftover, group.demand - group.grant)
                group.grant += top_up
                leftover -= top_up
            remaining = max(0, budget - sum(group.grant for group in groups))

    for group in groups:
        _allocate(list(group.units), group.grant, notes, chrome, degradation)


def _prune(children: list[Realized]) -> list[Realized]:
    pruned: list[Realized] = []
    for child in children:
        match child:
            case RText(dropped=True):
                continue
            case RCard(blocks=blocks):
                pruned.append(replace(child, blocks=_prune(blocks)))
            case RContent(slot=slot) if slot.dropped:
                continue
            case RPanel(children=inner, accent=accent):
                kept = _prune(inner)
                if kept:
                    pruned.append(RPanel(children=kept, accent=accent))
            case RGroup(children=inner):
                pruned.extend(_prune(inner))
            case RSection(texts=texts, accessory=accessory):
                kept_texts = [slot for slot in texts if not slot.dropped]
                # A Section needs at least one text child, and its accessory cannot stand
                # alone as a top-level component, so an emptied section drops whole.
                if kept_texts:
                    pruned.append(RSection(texts=kept_texts, accessory=accessory))
            case Gallery(urls=()) | Row(items=()):
                continue
            case _:
                pruned.append(child)
    return pruned


def _count_pages(unit: _Unit, per: int) -> list[str]:
    """Group a Lines node's entries into pages of ``per`` entries."""
    entries = [ladder[0] for ladder in unit.ladders or ()]
    pages = [unit.join.join(entries[start : start + per]) for start in range(0, len(entries), per)]
    return pages or [unit.content]


def _footer_cost(footer: Callable[[int, int], str], content_len: int) -> int:
    """Characters to hold back for the page footer before anything is allocated.

    Pages never outnumber the characters they hold, so the widest number the footer can be
    asked to render has at most as many digits as the content length. Measuring the footer
    there bounds its cost exactly, without a hand-picked sentinel page number.
    """
    widest = 10 ** len(str(max(content_len, 1))) - 1
    return len(PAGE_FOOTER_PREFIX) + len(footer(widest, widest))


def _validated_nav(nodes: Sequence[NavNode]) -> list[Node]:
    """Check the one part of the nav contract a type cannot state.

    `NavNode` already excludes text-bearing nodes, which is what lets nav land after the
    display budget is allocated. A raw item smuggles its own text cost past that, so it is
    still worth a look at runtime.
    """
    for node in nodes:
        match node:
            case Row(items=items) if not any(isinstance(item, RawItem) and item.text_cost for item in items):
                continue
            case SelectMenu() | RoutedSelect() | EntitySelect() | Sep() | Thumbnail() | Gallery() | RawItem(text_cost=0):
                continue
            case _:
                message = f"nav factories may only return component-bearing nodes, got {type(node).__name__}"
                raise ValueError(message)
    return list(nodes)


def _item_component_cost(item: object) -> int:
    return item.component_cost if isinstance(item, RawItem) else 1


def _structural_cost(children: Sequence[Realized]) -> dict[str, int]:
    """Count every structural axis at once, whichever target ends up budgeting them.

    A Components V2 target budgets components; a classic target budgets embeds, rows, and
    view children. Counting all four always is cheaper than asking the dialect, and costs
    nothing: a target simply does not budget the axes it has no field for, and an axis no
    target budgets is unconstrained rather than zero.
    """
    totals = {COMPONENTS: 0, EMBEDS: 0, ROWS: 0, CONTROLS: 0}

    def walk(nodes: Sequence[Realized]) -> None:
        for child in nodes:
            match child:
                case RPanel(children=inner):
                    totals[COMPONENTS] += 1
                    walk(inner)
                case RGroup(children=inner):
                    walk(inner)
                case RCard(blocks=blocks):
                    totals[EMBEDS] += 1
                    totals[COMPONENTS] += 1
                    walk(blocks)
                case RContent():
                    # Content is a message field, not a component. It costs no structure.
                    continue
                case RSection(texts=texts, accessory=accessory):
                    totals[COMPONENTS] += 1 + len(texts) + _item_component_cost(accessory)
                case Row(items=items):
                    totals[COMPONENTS] += 1 + sum(_item_component_cost(item) for item in items)
                    totals[ROWS] += 1
                    totals[CONTROLS] += len(items)
                case SelectMenu() | RoutedSelect() | EntitySelect():
                    totals[COMPONENTS] += 2  # the implicit ActionRow plus the select itself
                    totals[ROWS] += 1  # a select always occupies a whole row
                    totals[CONTROLS] += 1
                case RawItem(component_cost=component_cost):
                    totals[COMPONENTS] += component_cost
                case _:
                    totals[COMPONENTS] += 1

    walk(children)
    return totals


def _component_count(children: Sequence[Realized]) -> int:
    """Components alone, for callers comparing one V2 subtree against another."""
    return _structural_cost(children)[COMPONENTS]


type PositionState = Mapping[str, Position] | Position | None


def measure(
    nodes: Sequence[Node],
    *,
    limits: DiscordLimits = LIMITS,
    chrome: Chrome = DEFAULT_CHROME,
    localization: Localization = NEUTRAL,
    strict: bool = False,
    reserved: ResourceCost = EMPTY_COST,
    position: PositionState = None,
    nav: PlannedNav | None = None,
) -> MeasuredLayout:
    """Fit one concrete primitive layout into its target budgets.

    Exactly one deterministic pass over one already-decided tree: no alternatives are
    weighed here. Choosing between them — semantic strategies, semantic fallbacks, and
    primitive `Variants` — belongs to the planner's search, which calls this per candidate.
    An unresolved `Variants` reaching here is a planner bug, not a layout to fit.
    """
    chrome = localize_chrome(chrome, localization)
    measured = _measure_once(
        nodes,
        limits=limits,
        chrome=chrome,
        reserved=reserved,
        position=position,
        nav=nav,
        notes=[],
    )
    if strict and (lossy := lossy_notes(measured.notes)):
        raise LayoutOverflowError(lossy)
    return measured


def _configure_paginators(
    builder: _Builder,
    chrome: Chrome,
) -> tuple[list[_Unit], dict[int, str], dict[int, Callable[[int, int], str]]]:
    units = [unit for unit in builder.units if isinstance(unit.overflow, Paginate)]
    keys: dict[int, str] = {}
    footers: dict[int, Callable[[int, int], str]] = {}
    used: set[str] = set()
    for unit in units:
        policy = unit.overflow
        assert isinstance(policy, Paginate)
        key = policy.key or f"page{unit.index}"
        if key in used:
            message = f"duplicate pager key {key!r}"
            raise ValueError(message)
        used.add(key)
        keys[unit.index] = key
        footers[unit.index] = policy.footer if policy.footer is not None else chrome.page_footer
        if policy.per is not None:
            if isinstance(unit.node, Lines):
                unit.count_pages = _count_pages(unit, policy.per)
            else:
                builder.notes.append(
                    _note(
                        SolveNoteCode.PAGINATE_PER_FALLBACK,
                        f"node {unit.index} is not a Lines node; paging on overflow instead of per entry",
                    )
                )
                unit.overflow = replace(policy, per=None)
    return units, keys, footers


def _insert_after(
    children: list[Realized], target: RText, additions: list[Realized]
) -> tuple[list[Realized], int] | None:
    """Splice `additions` in after `target`, reporting the list and offset they landed at."""
    for index, child in enumerate(children):
        if child is target:
            children[index + 1 : index + 1] = additions
            return children, index + 1
        if (
            isinstance(child, RPanel | RGroup)
            and (found := _insert_after(child.children, target, additions)) is not None
        ):
            return found
    return None


def _requested_position(state: PositionState, key: str, *, first: bool) -> Position | None:
    if isinstance(state, Mapping):
        return state.get(key)
    if isinstance(state, Position) and first:
        return state
    return None


@dataclass(slots=True)
class _Pass:
    """One complete measuring pass, kept so the last one can be used after the loop ends."""

    builder: _Builder
    children: list[Realized]
    paginate_units: list[_Unit]
    keys: dict[int, str]
    footers: dict[int, Callable[[int, int], str]]
    clamps: int
    degradation: DegradationProfile
    text_used: dict[str, int]


def _measure_once(
    nodes: Sequence[Node],
    *,
    limits: DiscordLimits,
    chrome: Chrome,
    reserved: ResourceCost,
    position: PositionState,
    nav: PlannedNav | None,
    notes: list[SolveNote],
) -> MeasuredLayout:
    """One measuring pass, including a fixed point for all measured pager footers."""
    resolved = list(nodes)
    active: frozenset[int] = frozenset()
    seen: set[frozenset[int]] = {active}
    final: _Pass | None = None

    # `active` only ever grows and is bounded by the number of paginators in the tree, so
    # this terminates. The bound is stated rather than borrowed from an unrelated limit,
    # and a repeated active set means a paginator toggled itself off, which is a bug in
    # this function rather than a document the caller can fix.
    while True:
        pass_notes = list(notes)
        degradation = DegradationRecorder.create()
        builder = _Builder(limits=limits, notes=pass_notes)
        children = builder.realize_children(resolved)
        paginate_units, keys, footers = _configure_paginators(builder, chrome)
        # Everything noted so far is a clamp to Discord's own shape; fitting starts here.
        clamps = len(pass_notes)
        # Every pool is solved on its own. A document that exhausts one must not be able to
        # shrink, spill, or drop anything drawn from another: they are separate fields on
        # the outgoing message and Discord charges them separately.
        for axis, capacity in limits.text_axes.items():
            axis_units = [unit for unit in builder.units if unit.axis == axis]
            axis_regions = [region for region in builder.budgets if region.axis == axis]
            footer_reservation = sum(
                _footer_cost(footers[unit.index], len(unit.content))
                for unit in paginate_units
                if unit.index in active and unit.axis == axis
            )
            budget = capacity - builder.raw_text_cost.get(axis, 0) - reserved.get(axis) - footer_reservation
            if axis_regions:
                _allocate_budgeted(axis_regions, axis_units, budget, pass_notes, chrome, degradation)
            else:
                _allocate(axis_units, budget, pass_notes, chrome, degradation)
        children = _prune(children)
        text_used = dict(builder.raw_text_cost)
        for unit in builder.units:
            if not unit.slot.dropped:
                text_used[unit.axis] = text_used.get(unit.axis, 0) + len(unit.slot.content)
        detected = frozenset(
            unit.index for unit in paginate_units if unit.fragments is not None and len(unit.fragments) > 1
        )
        final = _Pass(builder, children, paginate_units, keys, footers, clamps, degradation.freeze(), text_used)
        expanded = active | detected
        if expanded == active:
            break
        if expanded in seen or len(expanded) > len(paginate_units):
            message = "paginator activation did not reach a fixed point; a pager toggled itself off"
            raise LayoutInvariantError(message)
        seen.add(expanded)
        active = expanded

    builder, children = final.builder, final.children
    text_used = final.text_used
    pagers: list[Pager] = []
    for unit in final.paginate_units:
        if unit.fragments is None or len(unit.fragments) <= 1:
            continue
        policy = unit.overflow
        assert isinstance(policy, Paginate)
        key = final.keys[unit.index]
        footer_slot = RText()
        initial = len(unit.fragments) - 1 if policy.initial == "end" else 0
        pager = Pager(
            key=key,
            slot=unit.slot,
            prefix=unit.prefix,
            suffix=unit.suffix,
            fragments=unit.fragments,
            footer_slot=footer_slot,
            footer=final.footers[unit.index],
            initial=initial,
            axis=unit.axis,
        )
        requested = _requested_position(position, key, first=not pagers)
        shown = pager.select(initial if requested is None else requested.offset)
        text_used[unit.axis] = text_used.get(unit.axis, 0) + len(footer_slot.content)
        additions: list[Realized] = [footer_slot]
        if nav is not None:
            additions.extend(
                builder.realize_children(
                    _validated_nav(nav(materialized_navigation_state(key, Position(offset=shown), pager.pages, chrome)))
                )
            )
        placement = _insert_after(children, unit.slot, additions)
        if placement is None:
            placement = (children, len(children))
            children.extend(additions)
        # The nav follows the footer slot, and `repage` replaces exactly that span.
        pager.nav_host, pager.nav_at, pager.nav_count = placement[0], placement[1] + 1, len(additions) - 1
        pagers.append(pager)

    cost = ResourceCost({**text_used, **_structural_cost(children)})
    capacities = {name: getattr(limits, attribute) for name, attribute in limits.budgets.items()}
    for axis, spent, capacity in cost.over({**limits.text_axes, **capacities}):
        builder.notes.append(
            _note(
                SolveNoteCode.COMPONENT_BUDGET if axis == COMPONENTS else SolveNoteCode.TEXT_BUDGET,
                f"{spent} {axis} exceed {capacity}; the document needs restructuring",
            )
        )
    return MeasuredLayout(
        children=children,
        notes=builder.notes,
        pagers=tuple(pagers),
        cost=cost,
        # Incoming notes are the ladder steps this pass was asked to measure, which are
        # themselves a response to overflow.
        overflowed=bool(notes) or len(builder.notes) > final.clamps,
        nav=nav,
        chrome=chrome,
        limits=limits,
        degradation=final.degradation,
    )
