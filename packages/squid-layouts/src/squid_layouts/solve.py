"""Fit an IR tree to Discord's message budgets.

The solver measures every node's chrome (markdown prefixes, code fences, join characters)
exactly, grants the shared display-text budget in priority order, and applies each node's
overflow policy only when its content does not fit. Higher priority is allocated first; ties
fall back to document order. Dropped nodes refund their grant and the allocation reruns, so a
dropped footnote genuinely returns its characters to the body.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace

from squid_layouts.chrome import DEFAULT_CHROME, Chrome
from squid_layouts.conform import ELLIPSIS
from squid_layouts.constraints import Alts, Drop, Never, Overflow, Paginate, Spill, Truncate
from squid_layouts.ir import (
    Button,
    Code,
    Fold,
    Footer,
    Gallery,
    Heading,
    Lines,
    LinkButton,
    Node,
    Option,
    Panel,
    RawItem,
    Row,
    Section,
    SelectMenu,
    Sep,
    Text,
    Thumbnail,
)
from squid_layouts.limits import LIMITS, V2Limits
from squid_layouts.styles import Color

type TextBearing = Text | Heading | Footer | Code | Lines


class LayoutOverflowError(Exception):
    """The document cannot fit its hard constraints into Discord's budgets."""

    def __init__(self, notes: list[str]) -> None:
        super().__init__("; ".join(notes))
        self.notes = notes


# --- Realized tree: the same shapes with final strings, consumed by materialize ------------


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
    """Page state for a document whose Paginate node overflowed."""

    slot: RText
    prefix: str
    suffix: str
    fragments: list[str]
    footer_slot: RText
    footer: Callable[[int, int], str]
    initial: int = 0
    """The page to open on; a mount adopts this before its first render."""

    @property
    def pages(self) -> int:
        return len(self.fragments)

    def select(self, index: int) -> int:
        """Render page ``index`` (clamped) into the document; returns the page shown."""
        index = max(0, min(index, self.pages - 1))
        self.slot.content = self.prefix + self.fragments[index] + self.suffix
        self.footer_slot.content = PAGE_FOOTER_PREFIX + self.footer(index + 1, self.pages)
        return index


@dataclass(frozen=True, slots=True)
class SolvedLayout:
    children: list[Realized]
    notes: list[str]
    pager: Pager | None = None
    page: int = 0
    """The page realized into the children; 0 when the document does not paginate."""
    components: int = 0
    """Components the built view will hold, nav and page footer included."""

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

    def _clamp_button(self, button: Button | LinkButton) -> Button | LinkButton:
        if len(button.label) <= self.limits.button_label:
            return button
        self.notes.append(f"button label clamped from {len(button.label)}")
        trimmed = _trim_keep(button.label, self.limits.button_label, "head")
        if isinstance(button, LinkButton):
            return LinkButton(label=trimmed, url=button.url)
        return Button(
            label=trimmed,
            on_click=button.on_click,
            key=button.key,
            style=button.style,
            emoji=button.emoji,
            disabled=button.disabled,
            policy=button.policy,
        )

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
                    self._clamp_button(item) if isinstance(item, Button | LinkButton) else item for item in items
                )
                return Row(items=clamped)
            case SelectMenu():
                return self._clamp_select(node)
            case RawItem(text_cost=text_cost):
                self.raw_text_cost += text_cost
                return node
            case Fold(primary=primary):
                # Folds are resolved to a branch before realization; this is belt and braces.
                return self.realize(primary)
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


def _apply_spill(unit: _Unit, usable: int, chrome: Chrome, notes: list[str]) -> bool:
    ladders = unit.ladders or ()
    total = len(ladders)
    # First degrade the largest entries down their ladders; spill whole entries only after
    # every ladder is exhausted.
    levels = [0] * total
    degraded = False

    def entry(index: int) -> str:
        return ladders[index][levels[index]]

    while sum(len(entry(i)) for i in range(total)) + (total - 1) * len(unit.join) > usable:
        candidates = [i for i in range(total) if levels[i] + 1 < len(ladders[i])]
        if not candidates:
            break
        largest = max(candidates, key=lambda i: len(entry(i)))
        levels[largest] += 1
        degraded = True

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
        # Never nodes are fixed costs: charge them before any flexible node sees the budget.
        overdraw = 0
        for unit in active:
            if isinstance(unit.overflow, Never):
                unit.grant = min(unit.need, max(0, remaining))
                overdraw += unit.need - unit.grant
                remaining -= unit.grant
        if overdraw:
            notes.append(f"Never nodes need {budget + overdraw} of {budget} available characters")
        flexible = [unit for unit in active if not isinstance(unit.overflow, Never)]
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


def _validated_nav(nodes: Sequence[Node]) -> list[Node]:
    """Check a nav factory's output against the contract that makes late realization exact.

    Nav lands after the display budget is allocated, so it may only carry nodes that cost no
    display text: rows, selects, separators, media, and zero-cost raw items.
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


def _component_count(children: list[Realized]) -> int:
    count = 0
    for child in children:
        match child:
            case RPanel(children=inner):
                count += 1 + _component_count(inner)
            case RSection(texts=texts):
                count += 1 + len(texts) + 1
            case Row(items=items):
                count += 1 + len(items)
            case SelectMenu():
                count += 2  # the implicit ActionRow plus the select itself
            case _:
                count += 1
    return count


type _FoldPath = tuple[int | str, ...]


def _folds(nodes: Sequence[Node], collapsed: set[_FoldPath]) -> list[tuple[_FoldPath, Fold]]:
    """Every available Fold occurrence on the selected branches, in document order."""
    found: list[tuple[_FoldPath, Fold]] = []

    def walk(node: Node, path: _FoldPath) -> None:
        match node:
            case Fold(primary=primary, fallback=fallback):
                if path in collapsed:
                    walk(fallback, (*path, "fallback"))
                else:
                    found.append((path, node))
                    walk(primary, (*path, "primary"))
            case Panel(children=children):
                for index, child in enumerate(children):
                    walk(child, (*path, "panel", index))
            case _:
                return

    for index, node in enumerate(nodes):
        walk(node, (index,))
    return found


def _resolve_folds(nodes: Sequence[Node], collapsed: set[_FoldPath]) -> list[Node]:
    """Resolve Fold wrappers to the branches selected for this measuring pass."""

    def rewrite(node: Node, path: _FoldPath) -> Node:
        match node:
            case Fold(primary=primary, fallback=fallback):
                if path in collapsed:
                    return rewrite(fallback, (*path, "fallback"))
                return rewrite(primary, (*path, "primary"))
            case Panel(children=children, accent=accent):
                rewritten = tuple(rewrite(child, (*path, "panel", index)) for index, child in enumerate(children))
                return Panel(children=rewritten, accent=accent)
            case _:
                return node

    return [rewrite(node, (index,)) for index, node in enumerate(nodes)]


def solve(
    nodes: Sequence[Node],
    *,
    limits: V2Limits = LIMITS,
    chrome: Chrome = DEFAULT_CHROME,
    strict: bool = False,
    reserved_text: int = 0,
    page: int | None = None,
    nav: Callable[[int, int], Sequence[Node]] | None = None,
) -> SolvedLayout:
    """Fit ``nodes`` into the message budgets, applying overflow policies where needed.

    Args:
        nodes: Top-level IR nodes in document order.
        limits: The limit table supplying the text and component budgets.
        chrome: Pre-translated framework strings.
        strict: Raise :class:`LayoutOverflowError` instead of degrading.
        reserved_text: Characters held back from the budget (e.g. for pagination chrome).
        page: The page to realize when the document paginates, clamped to the page count;
            ``None`` takes the pager's initial page.
        nav: Called with ``(page, pages)`` when the document paginates; its nodes are
            realized as the document's last children. It must return component-bearing
            nodes only — see :func:`_validated_nav`.

    Returns:
        The realized tree plus a note per degradation applied.
    """
    # Structural degradation wraps the measuring pass: solve with every Fold on its primary
    # branch, and while the document is over the component limit, collapse the lowest-priority
    # fold and solve again. Each round removes one fold, so the loop is bounded by their count.
    tree = list(nodes)
    collapsed: set[_FoldPath] = set()
    fold_notes: list[str] = []
    solved = _solve_once(
        tree,
        collapsed=collapsed,
        limits=limits,
        chrome=chrome,
        reserved_text=reserved_text,
        page=page,
        nav=nav,
        notes=[],
    )
    while solved.components > limits.total_components:
        remaining = _folds(tree, collapsed)
        if not remaining:
            break
        target_path, target = min(remaining, key=lambda candidate: candidate[1].priority)
        fold_notes.append(f"folded a priority {target.priority} alternate under component pressure")
        collapsed.add(target_path)
        solved = _solve_once(
            tree,
            collapsed=collapsed,
            limits=limits,
            chrome=chrome,
            reserved_text=reserved_text,
            page=page,
            nav=nav,
            notes=list(fold_notes),
        )

    if strict and solved.notes:
        raise LayoutOverflowError(solved.notes)
    return solved


def _solve_once(
    nodes: Sequence[Node],
    *,
    collapsed: set[_FoldPath],
    limits: V2Limits,
    chrome: Chrome,
    reserved_text: int,
    page: int | None,
    nav: Callable[[int, int], Sequence[Node]] | None,
    notes: list[str],
) -> SolvedLayout:
    """One measuring pass over a document whose Folds are resolved to a single branch."""
    builder = _Builder(limits=limits, notes=notes)
    children = builder.realize_children(_resolve_folds(nodes, collapsed))

    paginate_units = [unit for unit in builder.units if isinstance(unit.overflow, Paginate)]
    for extra in paginate_units[1:]:
        extra.overflow = Truncate()
        notes.append(f"extra Paginate node {extra.index} degraded to Truncate")
    paginator = paginate_units[0] if paginate_units else None

    page_footer = chrome.page_footer
    if paginator is not None:
        policy = paginator.overflow
        assert isinstance(policy, Paginate)
        page_footer = policy.footer if policy.footer is not None else chrome.page_footer
        if policy.per is not None:
            if isinstance(paginator.node, Lines):
                paginator.count_pages = _count_pages(paginator, policy.per)
            else:
                notes.append(f"node {paginator.index} is not a Lines node; paging on overflow instead of per entry")
                paginator.overflow = replace(policy, per=None)

    budget = limits.total_text - builder.raw_text_cost - reserved_text
    # When pagination can happen, its footer is charged before allocation — this is what
    # replaces hand-tuned constants like the old PAGE_CHARS.
    count_paginated = paginator is not None and paginator.count_pages is not None and len(paginator.count_pages) > 1
    if paginator is not None and (count_paginated or sum(unit.need for unit in builder.units) > budget):
        budget -= _footer_cost(page_footer, len(paginator.content))
    _allocate(builder.units, budget, notes, chrome)
    children = _prune(children)

    pager = None
    shown = 0
    if paginator is not None and paginator.fragments is not None and len(paginator.fragments) > 1:
        footer_slot = RText()
        children.append(footer_slot)
        assert isinstance(paginator.overflow, Paginate)
        initial = len(paginator.fragments) - 1 if paginator.overflow.initial == "end" else 0
        pager = Pager(
            slot=paginator.slot,
            prefix=paginator.prefix,
            suffix=paginator.suffix,
            fragments=paginator.fragments,
            footer_slot=footer_slot,
            footer=page_footer,
            initial=initial,
        )
        shown = pager.select(initial if page is None else page)
        if nav is not None:
            children.extend(builder.realize_children(_validated_nav(nav(shown, pager.pages))))

    count = _component_count(children)
    if count > limits.total_components:
        notes.append(f"{count} components exceed {limits.total_components}; the document needs restructuring")

    return SolvedLayout(children=children, notes=notes, pager=pager, page=shown, components=count)
