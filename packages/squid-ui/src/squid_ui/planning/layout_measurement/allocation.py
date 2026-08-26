"""Allocate text budgets and apply authored overflow policies."""

from collections.abc import Sequence
from dataclasses import dataclass
from heapq import heappop, heappush

from squid_ui.chrome import Chrome
from squid_ui.planning.degradation import DegradationRecorder
from squid_ui.planning.layout_measurement.diagnostics import (
    SolveNote,
    SolveNoteCode,
    SolveNoteSeverity,
    note,
)
from squid_ui.planning.layout_measurement.text import BudgetRegion, TextUnit, split_pages, trim_keep
from squid_ui.primitives.constraints import Alts, Condense, Drop, Never, Paginate, Spill, Truncate


def _apply(unit: TextUnit, chrome: Chrome, notes: list[SolveNote], degradation: DegradationRecorder) -> bool:
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
            notes.append(note(SolveNoteCode.NODE_DROPPED, f"dropped node {unit.index} ({unit.need} chars over budget)"))
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
                        note(
                            SolveNoteCode.ALTERNATE,
                            f"node {unit.index} degraded to a {len(alternate)}-char alternate",
                        )
                    )
                    degradation.record(priority=unit.priority, path=f"$.text.{unit.index}", semantic_steps=step)
                    unit.slot.content = unit.prefix + alternate + unit.suffix
                    return True
            fallback = ladder[-1] if ladder else unit.content
            notes.append(
                note(
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
            unit.slot.content = unit.prefix + trim_keep(fallback, usable, "head") + unit.suffix
            return True
        case Paginate(boundary=boundary, min_fill=min_fill, widows=widows) if usable >= 1:
            unit.fragments = split_pages(unit.content, usable, boundary, min_fill=min_fill, widows=widows)
            unit.slot.content = unit.prefix + unit.fragments[0] + unit.suffix
            return True
        case Truncate(keep=keep) if usable >= 1:
            notes.append(
                note(SolveNoteCode.TRUNCATED, f"trimmed node {unit.index} from {len(unit.content)} to {usable}")
            )
            degradation.record(
                priority=unit.priority,
                path=f"$.text.{unit.index}",
                truncated_chars=max(0, len(unit.content) - usable),
            )
            unit.slot.content = unit.prefix + trim_keep(unit.content, usable, keep) + unit.suffix
            return True
        case Never() if usable >= 1:
            notes.append(
                note(
                    SolveNoteCode.NEVER_CLAMPED,
                    f"clamped Never node {unit.index}: needed {unit.need}, granted {unit.grant}",
                )
            )
            unit.slot.content = unit.prefix + trim_keep(unit.content, usable, "head") + unit.suffix
            return True
        case _:
            notes.append(
                note(
                    SolveNoteCode.CHROME_DROP,
                    f"dropped node {unit.index}: grant {unit.grant} cannot cover chrome {unit.chrome_len}",
                )
            )
            degradation.record(priority=unit.priority, path=f"$.text.{unit.index}", dropped_nodes=1)
            return False


def _apply_count_pages(unit: TextUnit) -> bool:
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
    """Step the largest entries down their ladders until the block fits."""
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
    unit: TextUnit,
    usable: int,
    notes: list[SolveNote],
    degradation: DegradationRecorder,
) -> bool:
    ladders = unit.ladders or ((unit.content,),)
    levels = _step_ladders(ladders, unit.join, usable)
    body = unit.join.join(ladder[level] for ladder, level in zip(ladders, levels, strict=True))
    stepped = sum(1 for level in levels if level)
    if stepped:
        notes.append(
            note(
                SolveNoteCode.CONDENSED,
                f"node {unit.index} condensed {stepped} of {len(ladders)} entries down their ladders",
            )
        )
        degradation.record(priority=unit.priority, path=f"$.text.{unit.index}", semantic_steps=sum(levels))
    if len(body) > usable:
        notes.append(
            note(
                SolveNoteCode.CONDENSE_TRUNCATED,
                f"condensed node {unit.index} exhausted its ladders; trimming from {len(body)} to {usable}",
            )
        )
        degradation.record(
            priority=unit.priority,
            path=f"$.text.{unit.index}",
            truncated_chars=len(body) - usable,
        )
        body = trim_keep(body, usable, "head")
    unit.slot.content = unit.prefix + body + unit.suffix
    return True


def _apply_spill(
    unit: TextUnit,
    usable: int,
    chrome: Chrome,
    notes: list[SolveNote],
    degradation: DegradationRecorder,
) -> bool:
    ladders = unit.ladders or ()
    total = len(ladders)
    levels = _step_ladders(ladders, unit.join, usable)
    degraded = any(levels)

    def entry(index: int) -> str:
        return ladders[index][levels[index]]

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
                    note(
                        SolveNoteCode.SPILL_ALTERNATES,
                        f"node {unit.index} degraded {sum(1 for level in levels if level)} entries down their ladders",
                    )
                )
                degradation.record(priority=unit.priority, path=f"$.text.{unit.index}", semantic_steps=sum(levels))
            if dropped:
                notes.append(
                    note(
                        SolveNoteCode.SPILLED,
                        f"spilled node {unit.index}: showing {total - dropped} of {total} lines",
                    )
                )
                degradation.record(priority=unit.priority, path=f"$.text.{unit.index}", spilled_items=dropped)
            unit.slot.content = unit.prefix + body + unit.suffix
            return True
        if dropped < total:
            remaining_chars -= entry_lengths[drop_order[dropped]]
    notes.append(note(SolveNoteCode.SPILL_DROPPED, f"dropped node {unit.index}: no line fits in {usable}"))
    degradation.record(priority=unit.priority, path=f"$.text.{unit.index}", dropped_nodes=1)
    return False


def allocate(
    units: list[TextUnit],
    budget: int,
    notes: list[SolveNote],
    chrome: Chrome,
    degradation: DegradationRecorder,
) -> None:
    """Allocate one text pool by hard policy and authored priority."""
    active = list(units)
    for _ in range(len(units) + 1):
        remaining = budget
        overdraw = 0
        for unit in active:
            if isinstance(unit.overflow, Never):
                unit.grant = min(unit.need, max(0, remaining))
                overdraw += unit.need - unit.grant
                remaining -= unit.grant
        if overdraw:
            notes.append(
                note(
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
                share = max(0, remaining)
                for unit in group:
                    unit.grant = unit.need * share // total_need
                leftover = share - sum(unit.grant for unit in group)
                for unit in sorted(group, key=lambda candidate: candidate.index):
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


@dataclass(slots=True)
class _GrantGroup:
    units: tuple[TextUnit, ...]
    floor: int
    demand: int
    priority: int
    best_effort: bool = False
    grant: int = 0


def allocate_budgeted(
    regions: Sequence[BudgetRegion],
    units: Sequence[TextUnit],
    budget: int,
    notes: list[SolveNote],
    chrome: Chrome,
    degradation: DegradationRecorder,
) -> None:
    """Allocate transparent budget regions as siblings, then solve inside each grant."""
    claimed: set[int] = set()
    groups: list[_GrantGroup] = []
    candidate_units = units
    for region in reversed(regions):
        candidate_units = tuple(unit for unit in region.units if unit.index not in claimed)
        region_units = candidate_units
        if not region_units:
            continue
        claimed.update(unit.index for unit in region_units)
        need = sum(unit.need for unit in region_units)
        ceiling = region.preferred + region.stretch
        demand = need if need <= ceiling else min(need, region.preferred)
        if len(region_units) == 1 and need > ceiling and isinstance(region_units[0].overflow, Paginate):
            unit = region_units[0]
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
                region_units,
                min(region.minimum, demand),
                demand,
                max(unit.priority for unit in region_units),
                region.best_effort,
            )
        )
    for unit in candidate_units:
        if unit.index in claimed:
            continue
        fixed = isinstance(unit.overflow, Never | Condense)
        groups.append(_GrantGroup((unit,), unit.need if fixed else 0, unit.need, unit.priority))
    groups.sort(key=lambda group: min(unit.index for unit in group.units))

    remaining = max(0, budget)
    hard_floor = sum(group.floor for group in groups if not group.best_effort)
    if hard_floor > remaining:
        notes.append(
            note(
                SolveNoteCode.BUDGET_FLOOR,
                f"Budget floors need {hard_floor} of {remaining} available characters",
                SolveNoteSeverity.FAILURE,
            )
        )
    for group in groups:
        if not group.best_effort:
            group.grant = min(group.floor, remaining)
            remaining -= group.grant
    for group in groups:
        if group.best_effort:
            group.grant = min(group.floor, remaining)
            remaining -= group.grant
            if group.grant < group.floor:
                notes.append(
                    note(
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
        allocate(list(group.units), group.grant, notes, chrome, degradation)
