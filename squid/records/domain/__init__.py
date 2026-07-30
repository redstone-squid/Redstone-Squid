"""Rule-driven record domain primitives."""

from squid.records.domain.categories import CategorySemantics, generate_category_subsets
from squid.records.domain.formatting import (
    CategoryText,
    DoorCategory,
    ExtenderCategory,
    RulesTitleFormatter,
    TitleFormatter,
)
from squid.records.domain.models import (
    BuildKind,
    CandidateGap,
    RecordCandidate,
    RecordClass,
    RecordResolution,
    ResolutionStatus,
    TimingMethod,
    TimingVariant,
    VersionScope,
)
from squid.records.domain.resolution import (
    reduce_timing_variants,
    resolve_fastest,
    resolve_fastest_smallest,
    resolve_first,
    resolve_smallest,
    resolve_smallest_fastest,
)

__all__ = [
    "BuildKind",
    "CandidateGap",
    "CategorySemantics",
    "CategoryText",
    "DoorCategory",
    "ExtenderCategory",
    "RecordCandidate",
    "RecordClass",
    "RecordResolution",
    "ResolutionStatus",
    "RulesTitleFormatter",
    "TimingMethod",
    "TimingVariant",
    "TitleFormatter",
    "VersionScope",
    "generate_category_subsets",
    "reduce_timing_variants",
    "resolve_fastest",
    "resolve_fastest_smallest",
    "resolve_first",
    "resolve_smallest",
    "resolve_smallest_fastest",
]
