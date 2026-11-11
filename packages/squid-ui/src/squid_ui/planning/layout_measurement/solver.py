"""Measure an already-selected primitive tree against Discord message budgets.

Each text pool is allocated independently after charging markdown chrome and
reserving pager footers. Never and Condense policies precede flexible text;
flexible grants follow author priority. Dropped nodes release their grants.

Structural variants and semantic fallbacks must be selected before measurement.
Text overflow policies can still choose shorter alternatives during allocation.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from squid_ui.chrome import DEFAULT_CHROME, Chrome, localize_chrome
from squid_ui.errors import LayoutInvariantError
from squid_ui.ownership import Controlled
from squid_ui.planning.adapter import EMPTY_COST, ResourceCost
from squid_ui.planning.degradation import DegradationProfile, DegradationRecorder
from squid_ui.planning.layout_measurement.allocation import (
    allocate,
    allocate_budgeted,
)
from squid_ui.planning.layout_measurement.costing import (
    component_count,
    prune,
    structural_cost,
    validated_nav,
)
from squid_ui.planning.layout_measurement.diagnostics import (
    LayoutOverflowError,
    SolveNote,
    SolveNoteCode,
    SolveNoteSeverity,
    lossy_notes,
)
from squid_ui.planning.layout_measurement.model import (
    PAGE_FOOTER_PREFIX,
    MeasuredCard,
    MeasuredGroup,
    MeasuredPanel,
    MeasuredText,
    Pager,
    Realized,
)
from squid_ui.planning.layout_measurement.realization import Builder
from squid_ui.planning.layout_measurement.text import TextUnit
from squid_ui.planning.limits import LIMITS, Axis, MessageLimits
from squid_ui.planning.navigation import PagerNav
from squid_ui.planning.resolved import text as resolved_text
from squid_ui.primitives.constraints import Paginate, RoutedPage, resolve_page
from squid_ui.primitives.nodes import (
    Lines,
    Node,
)
from squid_ui.sources import Position
from squid_ui.text import NEUTRAL, Localization


@dataclass(frozen=True, slots=True)
class MeasuredLayout:
    """One concrete primitive layout, measured against one target's budgets."""

    children: list[Realized]
    notes: list[SolveNote]
    pagers: tuple[Pager, ...] = ()
    cost: ResourceCost = EMPTY_COST
    """Measured consumption by resource axis, including pager footers and controls."""
    overflowed: bool = False
    """Whether fitting added diagnostics beyond the initial shape checks.

    Local clamps alone do not set this flag. Root pagination uses it to avoid
    packing another node onto a page that already required overflow handling.
    """
    nav: PagerNav | None = None
    chrome: Chrome = DEFAULT_CHROME
    limits: MessageLimits = LIMITS
    degradation: DegradationProfile = field(default_factory=DegradationProfile)

    def fits(self, capacities: Mapping[Axis, int]) -> bool:
        """Whether every budgeted axis is within its cap."""
        return self.cost.within(capacities)

    @property
    def failures(self) -> tuple[SolveNote, ...]:
        """Constraint failures that make this solution unusable without another rung."""
        return tuple(note for note in self.notes if note.severity is SolveNoteSeverity.FAILURE)

    def reposition(self, positions: Mapping[str, Position]) -> None:
        """Rewrite visible pager pages without reallocating text.

        Page counts become known during fitting, so stored positions can only be
        reconciled afterward. Each fragment already fits its text allowance;
        changing the visible page can reuse that allocation and footer reservation.

        Navigation must preserve its realized node count and component cost; a
        change raises `LayoutInvariantError`. This mutates pager slots and child
        lists even though the MeasuredLayout wrapper is frozen.
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
            realized = Builder(limits=self.limits).realize_children(
                validated_nav(self.nav(pager.key, shown, pager.pages, self.chrome, pager.owner))
            )
            if len(realized) != pager.nav_count or component_count(realized) != component_count(previous):
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


def _count_pages(unit: TextUnit, per: int) -> list[str]:
    """Group a Lines node's entries into pages of ``per`` entries."""
    entries = [ladder[0] for ladder in unit.ladders or ()]
    pages = [unit.join.join(entries[start : start + per]) for start in range(0, len(entries), per)]
    return pages or [unit.content]


def _footer_cost(footer: Callable[[int, int], str], content_len: int) -> int:
    """Reserve a footer using the widest page number implied by content length.

    This assumes footer length grows with the digit counts of its arguments. A
    custom footer whose longest text occurs for smaller numbers can exceed this
    estimate. The `-# ` prefix is included.
    """
    widest = 10 ** len(str(max(content_len, 1))) - 1
    return len(PAGE_FOOTER_PREFIX) + len(footer(widest, widest))


type PositionState = Mapping[str, Position] | Position | None


def measure(
    nodes: Sequence[Node],
    *,
    limits: MessageLimits = LIMITS,
    chrome: Chrome = DEFAULT_CHROME,
    localization: Localization = NEUTRAL,
    strict: bool = False,
    reserved: ResourceCost = EMPTY_COST,
    position: PositionState = None,
    nav: PagerNav | None = None,
) -> MeasuredLayout:
    """Fit a normalized primitive tree and return its realized nodes, costs, and diagnostics.

    Structural alternatives must already be resolved. Allocation may repeat as
    nodes drop or pagers activate. With `strict=True`, any diagnostic other than
    lossless adaptation raises `LayoutOverflowError`; otherwise failures remain
    in the returned layout for the caller to inspect.
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
    builder: Builder,
    chrome: Chrome,
) -> tuple[list[TextUnit], dict[int, str], dict[int, Callable[[int, int], str]]]:
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
        footer = policy.footer if policy.footer is not None else chrome.page_footer
        footers[unit.index] = lambda page, pages, footer=footer: resolved_text(footer(page, pages))
        if policy.per is not None:
            if isinstance(unit.node, Lines):
                unit.count_pages = _count_pages(unit, policy.per)
            else:
                builder.notes.append(
                    SolveNote(
                        SolveNoteCode.PAGINATE_PER_FALLBACK,
                        f"node {unit.index} is not a Lines node; paging on overflow instead of per entry",
                    )
                )
                unit.overflow = replace(policy, per=None)
    return units, keys, footers


def _insert_after(
    children: list[Realized], target: MeasuredText, additions: list[Realized]
) -> tuple[list[Realized], int] | None:
    """Splice `additions` in after `target`, reporting the list and offset they landed at."""
    for index, child in enumerate(children):
        if child is target:
            children[index + 1 : index + 1] = additions
            return children, index + 1
        if (
            isinstance(child, MeasuredPanel | MeasuredGroup)
            and (found := _insert_after(child.children, target, additions)) is not None
        ):
            return found
    return None


def _insert_pager(
    children: list[Realized], target: MeasuredText, footer: MeasuredText, navigation: list[Realized]
) -> tuple[list[Realized], int] | None:
    """Keep an embed's page footer in its description and its controls outside the card."""
    for index, child in enumerate(children):
        if isinstance(child, MeasuredCard) and _insert_after(child.blocks, target, [footer]) is not None:
            # Each pager owns its navigation list, so another pager in the same card
            # cannot shift the slice that reposition() will later replace. Classic
            # drawing collects rows separately from embeds, preserving their order.
            children.insert(index, MeasuredGroup(navigation))
            return navigation, 0
        if (
            isinstance(child, MeasuredPanel | MeasuredGroup)
            and (placement := _insert_pager(child.children, target, footer, navigation)) is not None
        ):
            return placement
        if child is target:
            children[index + 1 : index + 1] = [footer, *navigation]
            return children, index + 2
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

    builder: Builder
    children: list[Realized]
    paginate_units: list[TextUnit]
    keys: dict[int, str]
    footers: dict[int, Callable[[int, int], str]]
    clamps: int
    degradation: DegradationProfile
    text_used: dict[Axis, int]


def _measure_once(
    nodes: Sequence[Node],
    *,
    limits: MessageLimits,
    chrome: Chrome,
    reserved: ResourceCost,
    position: PositionState,
    nav: PagerNav | None,
    notes: list[SolveNote],
) -> MeasuredLayout:
    """One measuring pass, including a fixed point for all measured pager footers."""
    resolved = list(nodes)
    active: frozenset[int] = frozenset()
    seen: set[frozenset[int]] = {active}
    final: _Pass | None = None

    # Keep footer reservations once a pager has activated. This monotone set is
    # bounded by the number of paginators, so repeated allocation terminates.
    while True:
        pass_notes = list(notes)
        degradation = DegradationRecorder.create()
        builder = Builder(limits=limits, notes=pass_notes)
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
                _footer_cost(footers[unit.index], len(unit.content)) + (2 if unit.axis is Axis.EMBED_TEXT else 0)
                for unit in paginate_units
                if unit.index in active and unit.axis == axis
            )
            budget = capacity - builder.raw_text_cost.get(axis, 0) - reserved.get(axis) - footer_reservation
            if axis_regions:
                allocate_budgeted(axis_regions, axis_units, budget, pass_notes, chrome, degradation)
            else:
                allocate(axis_units, budget, pass_notes, chrome, degradation)
        children = prune(children)
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
        footer_slot = MeasuredText()
        pager = Pager(
            key=key,
            slot=unit.slot,
            prefix=unit.prefix,
            suffix=unit.suffix,
            fragments=unit.fragments,
            footer_slot=footer_slot,
            footer=final.footers[unit.index],
            owner=policy.page,
            axis=unit.axis,
        )
        # A controlled page comes from the application, so a stored position must not override it.
        owner = policy.page
        requested = (
            None if isinstance(owner, Controlled | RoutedPage) else _requested_position(position, key, first=not pagers)
        )
        opening = owner.value if isinstance(owner, Controlled | RoutedPage) else owner.initial
        shown = pager.select(resolve_page(opening, pager.pages) if requested is None else requested.offset)
        # Embed description blocks are separated by two newlines.
        separator = 2 if unit.axis is Axis.EMBED_TEXT else 0
        text_used[unit.axis] = text_used.get(unit.axis, 0) + len(footer_slot.content) + separator
        navigation = (
            [] if nav is None else builder.realize_children(validated_nav(nav(key, shown, pager.pages, chrome, owner)))
        )
        placement = _insert_pager(children, unit.slot, footer_slot, navigation)
        if placement is None:
            children.append(footer_slot)
            placement = children, len(children)
            children.extend(navigation)
        pager.nav_host, pager.nav_at, pager.nav_count = placement[0], placement[1], len(navigation)
        pagers.append(pager)

    cost = ResourceCost({**text_used, **structural_cost(children)})
    for axis, spent, capacity in cost.over({**limits.text_axes, **limits.capacities}):
        builder.notes.append(
            SolveNote(
                SolveNoteCode.COMPONENT_BUDGET if axis == Axis.COMPONENTS else SolveNoteCode.TEXT_BUDGET,
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
