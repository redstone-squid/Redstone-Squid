"""Structured, lexicographic accounting for author-granted layout loss."""

from dataclasses import dataclass
from functools import total_ordering


@dataclass(frozen=True, slots=True)
class DegradationEffect:
    priority: int
    path: str
    semantic_steps: int = 0
    truncated_chars: int = 0
    spilled_items: int = 0
    dropped_nodes: int = 0
    reformatted_nodes: int = 0
    """Regions kept whole but redrawn in another shape, by an explicitly non-exact variant."""
    lossy_nodes: int = 0
    """Regions an explicitly lossy variant does not show at all."""


# One ordering of the loss axes, shared by every accumulator here. Adding an axis in one
# place and forgetting another is how a profile silently stops comparing what it claims to.
_AXIS_NAMES = (
    "dropped_nodes",
    "spilled_items",
    "truncated_chars",
    "semantic_steps",
    "reformatted_nodes",
    "lossy_nodes",
)
_AXES = len(_AXIS_NAMES)

type Tie = tuple[int | str, ...]
"""One effect's priority, path, and every axis, for the final deterministic comparison."""


def _axes(effect: DegradationEffect) -> tuple[int, ...]:
    return tuple(getattr(effect, name) for name in _AXIS_NAMES)


def _tie(effect: DegradationEffect) -> Tie:
    return (effect.priority, effect.path, *_axes(effect))


def _accumulate(totals: list[int], effect: DegradationEffect) -> None:
    for index, value in enumerate(_axes(effect)):
        totals[index] += value


@dataclass(frozen=True, slots=True)
class DegradationLevel:
    priority: int
    dropped_nodes: int = 0
    spilled_items: int = 0
    truncated_chars: int = 0
    semantic_steps: int = 0
    reformatted_nodes: int = 0
    lossy_nodes: int = 0

    @property
    def rank(self) -> tuple[int, int, int, int, int, int]:
        """Order severe losses before semantic substitutions when minimizing.

        A lossy variant hides whole authored regions, so it leads. Reformatting sits below
        truncation because it keeps every character the author wrote, only in another shape.
        """
        return (
            self.lossy_nodes,
            self.dropped_nodes,
            self.spilled_items,
            self.truncated_chars,
            self.reformatted_nodes,
            self.semantic_steps,
        )


def _level_values(level: DegradationLevel) -> list[int]:
    return [getattr(level, name) for name in _AXIS_NAMES]


@total_ordering
@dataclass(frozen=True, slots=True)
class DegradationProfile:
    """Loss grouped by author priority, with deterministic paths as the final tie."""

    levels: tuple[DegradationLevel, ...] = ()
    ties: tuple[Tie, ...] = ()

    @classmethod
    def from_effects(cls, effects: list[DegradationEffect]) -> DegradationProfile:
        totals: dict[int, list[int]] = {}
        for effect in effects:
            totals.setdefault(effect.priority, [0] * _AXES)
            _accumulate(totals[effect.priority], effect)
        levels = tuple(
            DegradationLevel(priority, *values)
            for priority, values in sorted(totals.items(), reverse=True)
            if any(values)
        )
        ties = tuple(sorted(_tie(effect) for effect in effects if any(_axes(effect))))
        return cls(levels, ties)

    @property
    def lossless(self) -> bool:
        return not self.levels

    def with_effect(self, effect: DegradationEffect) -> DegradationProfile:
        totals = {level.priority: _level_values(level) for level in self.levels}
        totals.setdefault(effect.priority, [0] * _AXES)
        _accumulate(totals[effect.priority], effect)
        levels = tuple(
            DegradationLevel(priority, *values)
            for priority, values in sorted(totals.items(), reverse=True)
            if any(values)
        )
        return DegradationProfile(levels, tuple(sorted((*self.ties, _tie(effect)))))

    def merged(self, other: DegradationProfile) -> DegradationProfile:
        totals: dict[int, list[int]] = {}
        for level in (*self.levels, *other.levels):
            aggregate = totals.setdefault(level.priority, [0] * _AXES)
            for index, value in enumerate(_level_values(level)):
                aggregate[index] += value
        levels = tuple(
            DegradationLevel(priority, *values)
            for priority, values in sorted(totals.items(), reverse=True)
            if any(values)
        )
        return DegradationProfile(levels, tuple(sorted((*self.ties, *other.ties))))

    def _rank(self, priorities: tuple[int, ...]) -> tuple[tuple[int, ...], ...]:
        by_priority = {level.priority: level.rank for level in self.levels}
        return tuple(by_priority.get(priority, (0,) * _AXES) for priority in priorities)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, DegradationProfile):
            return NotImplemented
        priorities = tuple(sorted({level.priority for level in (*self.levels, *other.levels)}, reverse=True))
        return (self._rank(priorities), self.ties) < (other._rank(priorities), other.ties)


@dataclass(slots=True)
class DegradationRecorder:
    effects: list[DegradationEffect]

    @classmethod
    def create(cls) -> DegradationRecorder:
        return cls([])

    def record(
        self,
        *,
        priority: int,
        path: str,
        semantic_steps: int = 0,
        truncated_chars: int = 0,
        spilled_items: int = 0,
        dropped_nodes: int = 0,
        reformatted_nodes: int = 0,
        lossy_nodes: int = 0,
    ) -> None:
        effect = DegradationEffect(
            priority,
            path,
            semantic_steps,
            truncated_chars,
            spilled_items,
            dropped_nodes,
            reformatted_nodes,
            lossy_nodes,
        )
        if any(_axes(effect)):
            self.effects.append(effect)

    def freeze(self) -> DegradationProfile:
        return DegradationProfile.from_effects(self.effects)
