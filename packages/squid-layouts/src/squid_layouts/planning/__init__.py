"""Portable planning, adaptation, and measured solving APIs."""

from squid_layouts.planning.cache import PlanCache
from squid_layouts.planning.planner import plan
from squid_layouts.planning.search import (
    DEFAULT_SEARCH_BUDGET,
    StrategyAssignment,
    StrategyAxis,
    StrategyCandidate,
    choose_strategy,
    iter_assignments,
)
from squid_layouts.planning.solve import LayoutOverflowError, SolvedLayout, solve
from squid_layouts.planning.target import PreparedExtension, ResourceCost, TargetProfile
from squid_layouts.sources import DEFAULT_POSITION_POLICY, Position, PositionDirection, PositionPolicy

__all__ = [
    "DEFAULT_POSITION_POLICY",
    "DEFAULT_SEARCH_BUDGET",
    "LayoutOverflowError",
    "PlanCache",
    "Position",
    "PositionDirection",
    "PositionPolicy",
    "PreparedExtension",
    "ResourceCost",
    "SolvedLayout",
    "StrategyAssignment",
    "StrategyAxis",
    "StrategyCandidate",
    "TargetProfile",
    "choose_strategy",
    "iter_assignments",
    "plan",
    "solve",
]
