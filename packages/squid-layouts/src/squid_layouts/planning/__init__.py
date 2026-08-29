"""Portable planning, adaptation, and measured solving APIs."""

from squid_layouts.planning.cache import PlanCache
from squid_layouts.planning.planner import plan
from squid_layouts.planning.search import DEFAULT_SEARCH_BUDGET, StrategyCandidate, choose_strategy
from squid_layouts.planning.solve import LayoutOverflowError, SolvedLayout, solve
from squid_layouts.planning.target import PreparedExtension, ResourceCost, TargetProfile

__all__ = [
    "DEFAULT_SEARCH_BUDGET",
    "LayoutOverflowError",
    "PlanCache",
    "PreparedExtension",
    "ResourceCost",
    "SolvedLayout",
    "StrategyCandidate",
    "TargetProfile",
    "choose_strategy",
    "plan",
    "solve",
]
