"""Exact sequence fragmentation under character and component limits."""

from dataclasses import dataclass
from math import inf


@dataclass(frozen=True, slots=True)
class BreakItem:
    """One atomic fragment and the cost of placing it after its leading separator."""

    chars: int
    components: int = 0
    leading_chars: int = 0
    break_after: bool = True

    def __post_init__(self) -> None:
        if self.chars < 0 or self.components < 0 or self.leading_chars < 0:
            message = "fragment costs must not be negative"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class _State:
    violations: int
    badness: float
    tie: int
    previous: int


def balanced_breaks(
    items: list[BreakItem],
    *,
    max_chars: int,
    max_components: int | None = None,
    min_fill: int = 0,
    widows: int = 1,
    ideal_total: int | None = None,
) -> tuple[int, ...]:
    """Return exact page-end offsets in fragmentation objective order.

    Preference violations are minimized first, followed by page count, squared distance
    from the mean fill, and stable fuller-first cuts. The first pass finds the first two
    tiers on the page-boundary DAG. A second, fixed-depth pass optimizes balance without
    carrying whole cut tuples through every state.
    """
    if max_chars < 1:
        message = "page character limit must be positive"
        raise ValueError(message)
    if max_components is not None and max_components < 1:
        message = "page component limit must be positive"
        raise ValueError(message)
    if min_fill < 0:
        message = "minimum fill must not be negative"
        raise ValueError(message)
    if widows < 1:
        message = "widows must be at least one"
        raise ValueError(message)
    if not items:
        return ()

    char_prefix = [0]
    component_prefix = [0]
    for item in items:
        char_prefix.append(char_prefix[-1] + item.leading_chars + item.chars)
        component_prefix.append(component_prefix[-1] + item.components)

    def costs(start: int, end: int) -> tuple[int, int]:
        chars = char_prefix[end] - char_prefix[start] - items[start].leading_chars
        components = component_prefix[end] - component_prefix[start]
        return chars, components

    count = len(items)
    predecessors: list[range] = [range(0)]
    earliest = 0
    for end in range(1, count + 1):
        while earliest < end:
            chars, components = costs(earliest, end)
            if chars <= max_chars and (max_components is None or components <= max_components):
                break
            earliest += 1
        predecessors.append(range(earliest, end))

    # Prefix states end at a real, non-final page boundary. The final edge is considered
    # separately because widows replace min-fill on the last page.
    ranks: list[tuple[int, int] | None] = [None] * (count + 1)
    ranks[0] = (0, 0)
    for end in range(1, count):
        if not items[end - 1].break_after:
            continue
        best: tuple[int, int] | None = None
        for start in predecessors[end]:
            previous = ranks[start]
            if previous is None:
                continue
            chars = char_prefix[end] - char_prefix[start] - items[start].leading_chars
            candidate = (previous[0] + int(chars < min_fill), previous[1] + 1)
            if best is None or candidate < best:
                best = candidate
        ranks[end] = best

    target: tuple[int, int] | None = None
    for start in predecessors[count]:
        previous = ranks[start]
        if previous is None:
            continue
        candidate = (previous[0] + int(count - start < widows), previous[1] + 1)
        if target is None or candidate < target:
            target = candidate
    if target is None:
        message = "sequence has no feasible break set"
        raise ValueError(message)

    target_violations, page_count = target
    total = ideal_total if ideal_total is not None else sum(item.chars for item in items)
    ideal = total / page_count
    base = count + 1
    initial: list[_State | None] = [None] * (count + 1)
    initial[0] = _State(0, 0.0, 0, -1)
    layers: list[list[_State | None]] = [initial]
    for used in range(1, page_count + 1):
        final_page = used == page_count
        previous_layer = layers[used - 1]
        layer: list[_State | None] = [None] * (count + 1)
        minimum_end = used
        maximum_end = count if final_page else count - (page_count - used)
        for end in range(minimum_end, maximum_end + 1):
            if not final_page and not items[end - 1].break_after:
                continue
            best_violations = count + 1
            best_badness = inf
            best_tie = -1
            best_start = -1
            for start in predecessors[end]:
                previous = previous_layer[start]
                if previous is None:
                    continue
                chars = char_prefix[end] - char_prefix[start] - items[start].leading_chars
                violation = int(end - start < widows) if final_page else int(chars < min_fill)
                violations = previous.violations + violation
                if violations > target_violations:
                    continue
                badness = previous.badness + (chars - ideal) ** 2
                tie = previous.tie * base + end
                if (violations, badness, -tie) < (best_violations, best_badness, -best_tie):
                    best_violations = violations
                    best_badness = badness
                    best_tie = tie
                    best_start = start
            if best_start >= 0:
                layer[end] = _State(best_violations, best_badness, best_tie, best_start)
        layers.append(layer)

    final = layers[page_count][count]
    if final is None or final.violations != target_violations or final.badness == inf:
        message = "sequence has no feasible balanced break set"
        raise ValueError(message)
    cuts = [count]
    end = count
    for used in range(page_count, 1, -1):
        state = layers[used][end]
        assert state is not None
        end = state.previous
        cuts.append(end)
    cuts.reverse()
    return tuple(cuts)
