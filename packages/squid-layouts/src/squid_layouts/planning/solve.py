"""Fit an IR tree to Discord's message budgets.

The solver measures every node's chrome (markdown prefixes, code fences, join characters)
exactly, grants the shared display-text budget in priority order, and applies each node's
overflow policy only when its content does not fit. Higher priority is allocated first; ties
fall back to document order. Dropped nodes refund their grant and the allocation reruns, so a
dropped footnote genuinely returns its characters to the body.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from squid_layouts.chrome import DEFAULT_CHROME, Chrome, localize_chrome
from squid_layouts.errors import LayoutInvariantError
from squid_layouts.planning.limits import ELLIPSIS, LIMITS, V2Limits
from squid_layouts.planning.pagination import NavNode, PageNav
from squid_layouts.primitives.constraints import Alts, Condense, Drop, Never, Overflow, Paginate, Spill, Truncate
from squid_layouts.primitives.nodes import (
    Button,
    Code,
    Embed,
    Footer,
    Gallery,
    Heading,
    Lines,
    LinkButton,
    Node,
    Option,
    Panel,
    RawItem,
    RoutedButton,
    Row,
    Section,
    SelectMenu,
    Sep,
    Text,
    Thumbnail,
    Variants,
)
from squid_layouts.primitives.styles import Color
from squid_layouts.text import NEUTRAL, Localization

type TextBearing = Text | Heading | Footer | Code | Lines


class LayoutOverflowError(Exception):
    """The document cannot fit its hard constraints into Discord's budgets."""

    def __init__(self, notes: list[str]) -> None:
        super().__init__("; ".join(notes))
        self.notes = notes


# --- Realized tree: the same shapes with final strings, consumed by scene conversion -------


@dataclass(slots=True)
class RText:
    content: str = ""
    dropped: bool = False


@dataclass(frozen=True, slots=True)
class RSection:
    texts: list[RText]
    accessory: Thumbnail | LinkButton | RawItem


@dataclass(frozen=True, slots=True)
class RPanel:
    children: list[Realized]
    accent: Color | None


type Realized = RText | RSection | RPanel | Sep | Row | SelectMenu | Thumbnail | Gallery | RawItem


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
class SolvedLayout:
    children: list[Realized]
    notes: list[str]
    pagers: tuple[Pager, ...] = ()
    components: int = 0
    """Components the built view will hold, including every pager's controls."""
    overflowed: bool = False
    """Whether anything had to give to fit, as opposed to being clamped on the way in.

    Not every note is a defeat. Trimming a select's options to 25 or a section's texts to
    3 is Discord's shape being enforced and happens whatever the budget; degrading,
    spilling, dropping or stepping a ladder means the content did not fit. A caller
    deciding whether more will fit — the root packer — needs to tell those apart.
    """
    nav: PageNav | None = None
    limits: V2Limits = LIMITS

    def repage(self, indices: Mapping[str, int]) -> None:
        """Show a different page of each named pager without re-fitting the document.

        Which page is showing is a display decision, not a layout one: every fragment
        already fits the grant its pager was allocated, the footer reservation was
        measured at its widest, and a nav factory may not vary its shape by page. So a
        caller that only learns where the reader belongs *after* fitting — which is
        anyone reconciling against a stored cursor, since the page count is an output —
        can move the page here instead of solving again.
        """
        for pager in self.pagers:
            index = indices.get(pager.key)
            if index is None:
                continue
            shown = pager.select(index)
            if self.nav is None or pager.nav_host is None:
                continue
            window = slice(pager.nav_at, pager.nav_at + pager.nav_count)
            previous = pager.nav_host[window]
            realized = _Builder(limits=self.limits).realize_children(
                _validated_nav(self.nav(pager.key, shown, pager.pages))
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


def _escape_fences(content: str) -> str:
    # A closing fence inside the content would end the block early; break it invisibly.
    return content.replace("```", "``\N{ZERO WIDTH SPACE}`")


def _make_unit(node: TextBearing, slot: RText, index: int) -> _Unit | None:
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


def split_pages(text: str, limit: int, boundary: str = "\n") -> list[str]:
    """Split ``text`` into chunks of at most ``limit``, preferring ``boundary`` cuts.

    Ported from the diagnostics view's `_paginate`/`_hard_split`: segments are kept whole
    where they fit; a single segment longer than a page is hard-split without inserting
    boundary characters that were never in the text.
    """
    pages: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current:
            pages.append(current)
            current = ""

    for segment in text.split(boundary):
        if len(segment) > limit:
            flush()
            chunks = _hard_split(segment, limit)
            pages.extend(chunks[:-1])
            current = chunks[-1]
            continue
        candidate = segment if not current else current + boundary + segment
        if len(candidate) <= limit:
            current = candidate
        else:
            flush()
            current = segment
    flush()
    return pages or [""]


def _trim_keep(text: str, limit: int, keep: str) -> str:
    if len(text) <= limit:
        return text
    if limit <= 1:
        return ELLIPSIS if limit == 1 else ""
    if keep == "tail":
        return ELLIPSIS + text[-(limit - 1) :].lstrip()
    return text[: limit - 1].rstrip() + ELLIPSIS


# --- Solve ----------------------------------------------------------------------------------


@dataclass(slots=True)
class _Builder:
    limits: V2Limits = LIMITS
    notes: list[str] = field(default_factory=list)
    units: list[_Unit] = field(default_factory=list)
    raw_text_cost: int = 0

    def _clamp_button[ButtonT: Button | LinkButton | RoutedButton](self, button: ButtonT) -> ButtonT:
        if len(button.label) <= self.limits.button_label:
            return button
        self.notes.append(f"button label clamped from {len(button.label)}")
        trimmed = _trim_keep(button.label, self.limits.button_label, "head")
        return replace(button, label=trimmed)

    def _clamp_select(self, select: SelectMenu) -> SelectMenu:
        limits = self.limits
        options = select.options
        if len(options) > limits.select_options:
            self.notes.append(f"{len(options)} select options clamped to {limits.select_options}")
            options = options[: limits.select_options]
        clamped_options = []
        for option in options:
            label = _trim_keep(option.label, limits.option_label, "head")
            value = option.value[: limits.option_value]
            description = option.description
            if description is not None and len(description) > limits.option_description:
                description = _trim_keep(description, limits.option_description, "head")
            if (label, value, description) != (option.label, option.value, option.description):
                self.notes.append("select option text clamped")
                option = Option(label=label, value=value, description=description, default=option.default)
            clamped_options.append(option)
        placeholder = select.placeholder
        if placeholder is not None and len(placeholder) > limits.select_placeholder:
            self.notes.append(f"select placeholder clamped from {len(placeholder)}")
            placeholder = _trim_keep(placeholder, limits.select_placeholder, "head")
        return SelectMenu(
            options=tuple(clamped_options),
            on_select=select.on_select,
            key=select.key,
            placeholder=placeholder,
            min_values=select.min_values,
            max_values=min(select.max_values, len(clamped_options) or 1),
            disabled=select.disabled,
            policy=select.policy,
            routes=select.routes,
        )

    def realize_children(self, nodes: Sequence[Node]) -> list[Realized]:
        return [self.realize(node) for node in nodes]

    def realize(self, node: Node) -> Realized:
        match node:
            case Text() | Heading() | Footer() | Code() | Lines():
                slot = RText()
                unit = _make_unit(node, slot, len(self.units))
                if unit is not None:
                    self.units.append(unit)
                return slot
            case Section(texts=texts, accessory=accessory):
                if len(texts) > 3:
                    self.notes.append(f"section holds {len(texts)} texts; keeping 3")
                    texts = texts[:3]
                slots: list[RText] = []
                for text_node in texts:
                    slot = RText()
                    unit = _make_unit(text_node, slot, len(self.units))
                    if unit is not None:
                        self.units.append(unit)
                    slots.append(slot)
                if isinstance(accessory, RawItem):
                    self.raw_text_cost += accessory.text_cost
                return RSection(texts=slots, accessory=accessory)
            case Panel(children=children, accent=accent):
                return RPanel(children=self.realize_children(children), accent=accent)
            case Gallery(urls=urls):
                if len(urls) > 10:
                    self.notes.append(f"gallery holds {len(urls)} items; keeping 10")
                    node = Gallery(urls=urls[:10])
                return node
            case Row(items=items):
                self.raw_text_cost += sum(item.text_cost for item in items if isinstance(item, RawItem))
                clamped = tuple(
                    self._clamp_button(item) if isinstance(item, Button | LinkButton | RoutedButton) else item
                    for item in items
                )
                return Row(items=clamped)
            case SelectMenu():
                return self._clamp_select(node)
            case RawItem(text_cost=text_cost):
                self.raw_text_cost += text_cost
                return node
            case Embed():
                message = "Embed must be expanded before solving"
                raise ValueError(message)
            case Variants():
                message = "Variants must be resolved before solving"
                raise ValueError(message)
            case _:
                return node


def _apply(unit: _Unit, chrome: Chrome, notes: list[str]) -> bool:
    """Render the unit into its slot within its grant. Returns False when the node drops."""
    if unit.count_pages is not None:
        return _apply_count_pages(unit)
    if unit.grant >= unit.need:
        unit.slot.content = unit.prefix + unit.content + unit.suffix
        return True

    usable = unit.grant - unit.chrome_len
    match unit.overflow:
        case Drop():
            notes.append(f"dropped node {unit.index} ({unit.need} chars over budget)")
            return False
        case Spill() if unit.ladders is not None:
            return _apply_spill(unit, usable, chrome, notes)
        case Condense() if usable >= 1:
            return _apply_condense(unit, usable, notes)
        case Alts(ladder=ladder) if usable >= 1:
            for alternate in ladder:
                if alternate and len(alternate) <= usable:
                    notes.append(f"node {unit.index} degraded to a {len(alternate)}-char alternate")
                    unit.slot.content = unit.prefix + alternate + unit.suffix
                    return True
            fallback = ladder[-1] if ladder else unit.content
            notes.append(f"node {unit.index} exhausted its ladder; trimming the last alternate")
            unit.slot.content = unit.prefix + _trim_keep(fallback, usable, "head") + unit.suffix
            return True
        case Paginate(boundary=boundary) if usable >= 1:
            # Pagination is the policy working as intended, not a degradation: no note.
            unit.fragments = split_pages(unit.content, usable, boundary)
            unit.slot.content = unit.prefix + unit.fragments[0] + unit.suffix
            return True
        case Truncate(keep=keep) if usable >= 1:
            notes.append(f"trimmed node {unit.index} from {len(unit.content)} to {usable}")
            unit.slot.content = unit.prefix + _trim_keep(unit.content, usable, keep) + unit.suffix
            return True
        case Never() if usable >= 1:
            notes.append(f"clamped Never node {unit.index}: needed {unit.need}, granted {unit.grant}")
            unit.slot.content = unit.prefix + _trim_keep(unit.content, usable, "head") + unit.suffix
            return True
        case _:
            notes.append(f"dropped node {unit.index}: grant {unit.grant} cannot cover chrome {unit.chrome_len}")
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
        fragments.extend([page] if len(page) <= usable else split_pages(page, usable, unit.join))
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

    def entry(index: int) -> str:
        return ladders[index][levels[index]]

    while sum(len(entry(i)) for i in range(len(ladders))) + (len(ladders) - 1) * len(join) > usable:
        candidates = [i for i in range(len(ladders)) if levels[i] + 1 < len(ladders[i])]
        if not candidates:
            break
        largest = max(candidates, key=lambda i: len(entry(i)))
        levels[largest] += 1
    return levels


def _apply_condense(unit: _Unit, usable: int, notes: list[str]) -> bool:
    """Shorten entries as far as their ladders go, then trim; never lose a whole entry."""
    ladders = unit.ladders or ((unit.content,),)
    levels = _step_ladders(ladders, unit.join, usable)
    body = unit.join.join(ladder[level] for ladder, level in zip(ladders, levels, strict=True))
    stepped = sum(1 for level in levels if level)
    if stepped:
        notes.append(f"node {unit.index} condensed {stepped} of {len(ladders)} entries down their ladders")
    if len(body) > usable:
        notes.append(f"condensed node {unit.index} exhausted its ladders; trimming from {len(body)} to {usable}")
        body = _trim_keep(body, usable, "head")
    unit.slot.content = unit.prefix + body + unit.suffix
    return True


def _apply_spill(unit: _Unit, usable: int, chrome: Chrome, notes: list[str]) -> bool:
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
    for dropped in range(total + 1):
        omitted = set(drop_order[:dropped])
        shown = [entry(i) for i in range(total) if i not in omitted]
        if dropped:
            shown.append(chrome.and_n_more(dropped))
        body = unit.join.join(shown)
        if body and len(body) <= usable:
            if degraded:
                notes.append(f"node {unit.index} degraded {sum(1 for lvl in levels if lvl)} entries down their ladders")
            if dropped:
                notes.append(f"spilled node {unit.index}: showing {total - dropped} of {total} lines")
            unit.slot.content = unit.prefix + body + unit.suffix
            return True
    notes.append(f"dropped node {unit.index}: no line fits in {usable}")
    return False


def _allocate(units: list[_Unit], budget: int, notes: list[str], chrome: Chrome) -> None:
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
            notes.append(f"Never nodes need {budget + overdraw} of {budget} available characters")
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
        dropped = [unit for unit in active if not _apply(unit, chrome, notes)]
        if not dropped:
            return
        for unit in dropped:
            unit.slot.dropped = True
            active.remove(unit)
    # The loop always terminates by emptying `active`; each pass removes at least one unit.


def _prune(children: list[Realized]) -> list[Realized]:
    pruned: list[Realized] = []
    for child in children:
        match child:
            case RText(dropped=True):
                continue
            case RPanel(children=inner, accent=accent):
                kept = _prune(inner)
                if kept:
                    pruned.append(RPanel(children=kept, accent=accent))
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
            case SelectMenu() | Sep() | Thumbnail() | Gallery() | RawItem(text_cost=0):
                continue
            case _:
                message = f"nav factories may only return component-bearing nodes, got {type(node).__name__}"
                raise ValueError(message)
    return list(nodes)


def _item_component_cost(item: object) -> int:
    return item.component_cost if isinstance(item, RawItem) else 1


def _component_count(children: list[Realized]) -> int:
    count = 0
    for child in children:
        match child:
            case RPanel(children=inner):
                count += 1 + _component_count(inner)
            case RSection(texts=texts, accessory=accessory):
                count += 1 + len(texts) + _item_component_cost(accessory)
            case Row(items=items):
                count += 1 + sum(_item_component_cost(item) for item in items)
            case SelectMenu():
                count += 2  # the implicit ActionRow plus the select itself
            case RawItem(component_cost=component_cost):
                count += component_cost
            case _:
                count += 1
    return count


type _VariantPath = tuple[int | str, ...]
type _Positions = Mapping[_VariantPath, int]
"""Which rung each ladder occurrence currently sits on; absent means rung 0."""


def _format_path(path: _VariantPath) -> str:
    """Render a ladder's path for a note. A reader's landmark, not an addressing scheme."""
    return "$." + ".".join(str(part) for part in path if part != "panel")


def _walk_ladders(nodes: Sequence[Node], positions: _Positions, visit) -> None:
    """Visit every node reachable through the currently selected rungs, in document order.

    Ladders only occur at the top level, inside a Panel, or inside another ladder's rung:
    `Section.texts`, `Row.items` and `ActionGroup.items` are typed to exclude them, so these
    two recursive arms are exhaustive.
    """

    def walk(node: Node, path: _VariantPath) -> None:
        match node:
            case Variants(variants=variants):
                rung = min(positions.get(path, 0), len(variants) - 1)
                visit(path, node, rung)
                # The rung is part of the descendants' path, so stepping this ladder abandons
                # their positions rather than reinterpreting them against a different subtree.
                for index, child in enumerate(variants[rung].nodes):
                    walk(child, (*path, rung, index))
            case Panel(children=children):
                for index, child in enumerate(children):
                    walk(child, (*path, "panel", index))
            case _:
                return

    for index, node in enumerate(nodes):
        walk(node, (index,))


def _steppable(nodes: Sequence[Node], positions: _Positions) -> list[tuple[_VariantPath, Variants, int]]:
    """Every reachable ladder that still has a rung left, in document order."""
    found: list[tuple[_VariantPath, Variants, int]] = []

    def visit(path: _VariantPath, node: Variants, rung: int) -> None:
        if rung + 1 < len(node.variants):
            found.append((path, node, rung))

    _walk_ladders(nodes, positions, visit)
    return found


def _resolve_variants(nodes: Sequence[Node], positions: _Positions) -> list[Node]:
    """Splice each ladder's selected rung into its parent for this measuring pass."""

    def rewrite(node: Node, path: _VariantPath) -> list[Node]:
        match node:
            case Variants(variants=variants):
                rung = min(positions.get(path, 0), len(variants) - 1)
                resolved: list[Node] = []
                for index, child in enumerate(variants[rung].nodes):
                    resolved.extend(rewrite(child, (*path, rung, index)))
                return resolved
            case Panel(children=children, accent=accent):
                inner: list[Node] = []
                for index, child in enumerate(children):
                    inner.extend(rewrite(child, (*path, "panel", index)))
                return [Panel(children=tuple(inner), accent=accent)]
            case _:
                return [node]

    resolved: list[Node] = []
    for index, node in enumerate(nodes):
        resolved.extend(rewrite(node, (index,)))
    return resolved


type PageState = Mapping[str, int] | int | None


def solve(
    nodes: Sequence[Node],
    *,
    limits: V2Limits = LIMITS,
    chrome: Chrome = DEFAULT_CHROME,
    localization: Localization = NEUTRAL,
    strict: bool = False,
    reserved_text: int = 0,
    page: PageState = None,
    nav: PageNav | None = None,
) -> SolvedLayout:
    """Fit nodes into target budgets with independently keyed pagination.

    `Variant.requires` is not consulted here: capability filtering belongs to the planner,
    which is the only layer that knows the target. A ladder reaching the solver is a pure
    budget ladder whose rungs are all available.
    """
    chrome = localize_chrome(chrome, localization)
    tree = list(nodes)
    positions: dict[_VariantPath, int] = {}
    step_notes: list[str] = []
    solved = _solve_once(
        tree,
        positions=positions,
        limits=limits,
        chrome=chrome,
        reserved_text=reserved_text,
        page=page,
        nav=nav,
        notes=[],
    )
    while solved.components > limits.total_components:
        remaining = _steppable(tree, positions)
        if not remaining:
            break
        # Priority decides which ladder gives way; the rung it already sits on decides which
        # of several equals gives way next, so equal-priority ladders step breadth-first
        # rather than one collapsing to nothing while its twin stays whole. `min` is stable,
        # so a full tie still falls to document order.
        path, ladder, rung = min(remaining, key=lambda candidate: (candidate[1].priority, candidate[2]))
        step_notes.append(
            f"{_format_path(path)} stepped to variant {rung + 2} of {len(ladder.variants)} "
            f"(priority {ladder.priority}) under component pressure"
        )
        positions[path] = rung + 1
        solved = _solve_once(
            tree,
            positions=positions,
            limits=limits,
            chrome=chrome,
            reserved_text=reserved_text,
            page=page,
            nav=nav,
            notes=list(step_notes),
        )

    if strict and solved.notes:
        raise LayoutOverflowError(solved.notes)
    return solved


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
                builder.notes.append(f"node {unit.index} is not a Lines node; paging on overflow instead of per entry")
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
        if isinstance(child, RPanel) and (found := _insert_after(child.children, target, additions)) is not None:
            return found
    return None


def _requested_page(state: PageState, key: str, *, first: bool) -> int | None:
    if isinstance(state, Mapping):
        return state.get(key)
    if isinstance(state, int) and first:
        return state
    return None


def _solve_once(
    nodes: Sequence[Node],
    *,
    positions: _Positions,
    limits: V2Limits,
    chrome: Chrome,
    reserved_text: int,
    page: PageState,
    nav: PageNav | None,
    notes: list[str],
) -> SolvedLayout:
    """One measuring pass, including a fixed point for all measured pager footers."""
    resolved = _resolve_variants(nodes, positions)
    active: set[int] = set()
    final: (
        tuple[
            _Builder,
            list[Realized],
            list[_Unit],
            dict[int, str],
            dict[int, Callable[[int, int], str]],
            int,
        ]
        | None
    ) = None

    # At least one previously unseen paginator joins active on every non-terminal pass.
    # The component ceiling is a safe bound even when many paginators are nested in one Panel.
    for _ in range(limits.total_components + 1):
        pass_notes = list(notes)
        builder = _Builder(limits=limits, notes=pass_notes)
        children = builder.realize_children(resolved)
        paginate_units, keys, footers = _configure_paginators(builder, chrome)
        # Everything noted so far is a clamp to Discord's own shape; fitting starts here.
        clamps = len(pass_notes)
        footer_reservation = sum(
            _footer_cost(footers[unit.index], len(unit.content)) for unit in paginate_units if unit.index in active
        )
        budget = limits.total_text - builder.raw_text_cost - reserved_text - footer_reservation
        _allocate(builder.units, budget, pass_notes, chrome)
        children = _prune(children)
        detected = {unit.index for unit in paginate_units if unit.fragments is not None and len(unit.fragments) > 1}
        final = (builder, children, paginate_units, keys, footers, clamps)
        expanded = active | detected
        if expanded == active:
            break
        active = expanded

    assert final is not None
    builder, children, paginate_units, keys, footers, clamps = final
    pagers: list[Pager] = []
    for unit in paginate_units:
        if unit.fragments is None or len(unit.fragments) <= 1:
            continue
        policy = unit.overflow
        assert isinstance(policy, Paginate)
        key = keys[unit.index]
        footer_slot = RText()
        initial = len(unit.fragments) - 1 if policy.initial == "end" else 0
        pager = Pager(
            key=key,
            slot=unit.slot,
            prefix=unit.prefix,
            suffix=unit.suffix,
            fragments=unit.fragments,
            footer_slot=footer_slot,
            footer=footers[unit.index],
            initial=initial,
        )
        requested = _requested_page(page, key, first=not pagers)
        shown = pager.select(initial if requested is None else requested)
        additions: list[Realized] = [footer_slot]
        if nav is not None:
            additions.extend(builder.realize_children(_validated_nav(nav(key, shown, pager.pages))))
        placement = _insert_after(children, unit.slot, additions)
        if placement is None:
            placement = (children, len(children))
            children.extend(additions)
        # The nav follows the footer slot, and `repage` replaces exactly that span.
        pager.nav_host, pager.nav_at, pager.nav_count = placement[0], placement[1] + 1, len(additions) - 1
        pagers.append(pager)

    count = _component_count(children)
    if count > limits.total_components:
        builder.notes.append(f"{count} components exceed {limits.total_components}; the document needs restructuring")
    return SolvedLayout(
        children=children,
        notes=builder.notes,
        pagers=tuple(pagers),
        components=count,
        # Incoming notes are the ladder steps this pass was asked to measure, which are
        # themselves a response to overflow.
        overflowed=bool(notes) or len(builder.notes) > clamps,
        nav=nav,
        limits=limits,
    )
