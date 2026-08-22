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


@dataclass(frozen=True, slots=True)
class DegradationLevel:
    priority: int
    dropped_nodes: int = 0
    spilled_items: int = 0
    truncated_chars: int = 0
    semantic_steps: int = 0

    @property
    def rank(self) -> tuple[int, int, int, int]:
        """Order severe losses before semantic substitutions when minimizing."""
        return self.dropped_nodes, self.spilled_items, self.truncated_chars, self.semantic_steps


@total_ordering
@dataclass(frozen=True, slots=True)
class DegradationProfile:
    """Loss grouped by author priority, with deterministic paths as the final tie."""

    levels: tuple[DegradationLevel, ...] = ()
    ties: tuple[tuple[int, str, int, int, int, int], ...] = ()

    @classmethod
    def from_effects(cls, effects: list[DegradationEffect]) -> DegradationProfile:
        totals: dict[int, list[int]] = {}
        for effect in effects:
            level = totals.setdefault(effect.priority, [0, 0, 0, 0])
            level[0] += effect.dropped_nodes
            level[1] += effect.spilled_items
            level[2] += effect.truncated_chars
            level[3] += effect.semantic_steps
        levels = tuple(
            DegradationLevel(priority, *values)
            for priority, values in sorted(totals.items(), reverse=True)
            if any(values)
        )
        ties = tuple(
            sorted(
                (
                    effect.priority,
                    effect.path,
                    effect.dropped_nodes,
                    effect.spilled_items,
                    effect.truncated_chars,
                    effect.semantic_steps,
                )
                for effect in effects
                if any(
                    (
                        effect.dropped_nodes,
                        effect.spilled_items,
                        effect.truncated_chars,
                        effect.semantic_steps,
                    )
                )
            )
        )
        return cls(levels, ties)

    @property
    def lossless(self) -> bool:
        return not self.levels

    def with_effect(self, effect: DegradationEffect) -> DegradationProfile:
        totals = {
            level.priority: [
                level.dropped_nodes,
                level.spilled_items,
                level.truncated_chars,
                level.semantic_steps,
            ]
            for level in self.levels
        }
        level = totals.setdefault(effect.priority, [0, 0, 0, 0])
        level[0] += effect.dropped_nodes
        level[1] += effect.spilled_items
        level[2] += effect.truncated_chars
        level[3] += effect.semantic_steps
        levels = tuple(
            DegradationLevel(priority, *values)
            for priority, values in sorted(totals.items(), reverse=True)
            if any(values)
        )
        tie = (
            effect.priority,
            effect.path,
            effect.dropped_nodes,
            effect.spilled_items,
            effect.truncated_chars,
            effect.semantic_steps,
        )
        return DegradationProfile(levels, tuple(sorted((*self.ties, tie))))

    def merged(self, other: DegradationProfile) -> DegradationProfile:
        totals: dict[int, list[int]] = {}
        for level in (*self.levels, *other.levels):
            aggregate = totals.setdefault(level.priority, [0, 0, 0, 0])
            aggregate[0] += level.dropped_nodes
            aggregate[1] += level.spilled_items
            aggregate[2] += level.truncated_chars
            aggregate[3] += level.semantic_steps
        levels = tuple(
            DegradationLevel(priority, *values)
            for priority, values in sorted(totals.items(), reverse=True)
            if any(values)
        )
        return DegradationProfile(levels, tuple(sorted((*self.ties, *other.ties))))

    def _rank(self, priorities: tuple[int, ...]) -> tuple[tuple[int, int, int, int], ...]:
        by_priority = {level.priority: level.rank for level in self.levels}
        return tuple(by_priority.get(priority, (0, 0, 0, 0)) for priority in priorities)

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
    ) -> None:
        if any((semantic_steps, truncated_chars, spilled_items, dropped_nodes)):
            self.effects.append(
                DegradationEffect(
                    priority,
                    path,
                    semantic_steps,
                    truncated_chars,
                    spilled_items,
                    dropped_nodes,
                )
            )

    def freeze(self) -> DegradationProfile:
        return DegradationProfile.from_effects(self.effects)
