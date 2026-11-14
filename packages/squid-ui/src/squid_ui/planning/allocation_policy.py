"""Comparison and diagnostics for stable joint allocations.

Preferences are ordinal within a choice, then compared worst-first within a
region and between equally important siblings. Fidelity is a partial order:
unrelated losses remain incomparable even when one requires fewer page turns.
"""

from dataclasses import dataclass
from enum import StrEnum
from functools import cmp_to_key
from itertools import zip_longest

from squid_ui.planning.composition import CandidateViolation, OccurrenceId
from squid_ui.planning.resources import Axis
from squid_ui.presentations import FidelityEffect


@dataclass(frozen=True, slots=True)
class AllocationGoal:
    """A region's desired resource allocation; hard floors reserve unused space."""

    axis: Axis
    minimum: int
    hard: bool = False

    def __post_init__(self) -> None:
        if self.minimum < 0:
            message = "allocation minimum cannot be negative"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class RegionPolicy:
    """Higher priority is compared first. Stretch is deliberately a separate concept."""

    priority: int = 0
    goals: tuple[AllocationGoal, ...] = ()


@dataclass(frozen=True, slots=True)
class PreferenceTree:
    ranks: tuple[int, ...] = ()
    children: tuple[tuple[int, PreferenceTree], ...] = ()


def dense_ranks(preferences: tuple[int, ...]) -> tuple[int, ...]:
    ordered = {value: index for index, value in enumerate(sorted(set(preferences)))}
    return tuple(ordered[value] for value in preferences)


def compare_preferences(left: PreferenceTree, right: PreferenceTree) -> int:
    """Compare active choices, padding absent choices and regions with satisfaction.

    A region's own choices precede its nested sibling groups. Conditional branches
    do not pay for choices that are absent. Extra satisfied choices cannot improve
    or worsen a result; extra dissatisfied choices can worsen it.
    """
    for a, b in zip_longest(sorted(left.ranks, reverse=True), sorted(right.ranks, reverse=True), fillvalue=0):
        if a != b:
            return (a > b) - (a < b)
    priorities = sorted({priority for priority, _ in (*left.children, *right.children)}, reverse=True)
    for priority in priorities:
        a_children = sorted(
            (child for tier, child in left.children if tier == priority),
            key=cmp_to_key(compare_preferences),
            reverse=True,
        )
        b_children = sorted(
            (child for tier, child in right.children if tier == priority),
            key=cmp_to_key(compare_preferences),
            reverse=True,
        )
        for a, b in zip_longest(a_children, b_children, fillvalue=PreferenceTree()):
            if result := compare_preferences(a, b):
                return result
    return 0


@dataclass(frozen=True, slots=True)
class AllocationQuality:
    minimum_shortfalls: tuple[int, ...]
    preferences: PreferenceTree
    effects: frozenset[FidelityEffect]
    break_shortfalls: tuple[int, ...]
    turns: int
    tie: tuple[tuple[tuple[str, ...], str, tuple[int, ...]], ...]


def compare_quality(left: AllocationQuality, right: AllocationQuality) -> int | None:
    """Return -1 for better, 1 for worse, 0 for equivalent, None for incomparable."""
    for a, b in zip_longest(left.minimum_shortfalls, right.minimum_shortfalls, fillvalue=0):
        if a != b:
            return (a > b) - (a < b)
    if preferences := compare_preferences(left.preferences, right.preferences):
        return preferences
    if left.effects != right.effects:
        if left.effects < right.effects:
            return -1
        if right.effects < left.effects:
            return 1
        return None
    for a, b in zip_longest(left.break_shortfalls, right.break_shortfalls, fillvalue=0):
        if a != b:
            return (a > b) - (a < b)
    return (left.turns > right.turns) - (left.turns < right.turns)


class SearchCompletion(StrEnum):
    COMPLETE = "complete"
    EXHAUSTED = "exhausted"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class SearchDiagnostics:
    completion: SearchCompletion
    optimal: bool
    work_used: int
    work_limit: int
    generated_states: int = 0
    target_evaluations: int = 0
    certified_combinations: int = 0
    violations: tuple[CandidateViolation, ...] = ()
    unsupported: tuple[OccurrenceId, ...] = ()
