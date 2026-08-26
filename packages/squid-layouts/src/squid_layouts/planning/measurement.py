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

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from squid_layouts.chrome import DEFAULT_CHROME, Chrome, localize_chrome
from squid_layouts.errors import LayoutInvariantError
from squid_layouts.planning.degradation import DegradationProfile, DegradationRecorder
from squid_layouts.planning.layout_measurement.allocation import (
    allocate as _allocate,
)
from squid_layouts.planning.layout_measurement.allocation import (
    allocate_budgeted as _allocate_budgeted,
)
from squid_layouts.planning.layout_measurement.costing import (
    component_count as _component_count,
)
from squid_layouts.planning.layout_measurement.costing import (
    prune as _prune,
)
from squid_layouts.planning.layout_measurement.costing import (
    structural_cost as _structural_cost,
)
from squid_layouts.planning.layout_measurement.costing import (
    validated_nav as _validated_nav,
)
from squid_layouts.planning.layout_measurement.diagnostics import (
    LayoutOverflowError,
    SolveNote,
    SolveNoteCode,
    SolveNoteSeverity,
    lossy_notes,
)
from squid_layouts.planning.layout_measurement.diagnostics import (
    note as _note,
)
from squid_layouts.planning.layout_measurement.model import (
    PAGE_FOOTER_PREFIX,
    Pager,
    Realized,
    RGroup,
    RPanel,
    RText,
)
from squid_layouts.planning.layout_measurement.realization import Builder as _Builder
from squid_layouts.planning.layout_measurement.text import (
    TextUnit as _Unit,
)
from squid_layouts.planning.limits import (
    COMPONENTS,
    LIMITS,
    DiscordLimits,
)
from squid_layouts.planning.navigation import (
    PlannedNav,
    materialized_navigation_state,
)
from squid_layouts.planning.target import EMPTY_COST, ResourceCost
from squid_layouts.primitives.constraints import Paginate
from squid_layouts.primitives.nodes import (
    Lines,
    Node,
)
from squid_layouts.sources import Position
from squid_layouts.text import NEUTRAL, Localization


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


# --- Solve ----------------------------------------------------------------------------------


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
