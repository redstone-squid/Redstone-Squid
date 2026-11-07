"""Rule-driven record domain primitives."""

from squid.records.domain.categories import CategorySemantics, generate_category_subsets
from squid.records.domain.formatting import (
    DoorCategory,
    ExtenderCategory,
    FormattedTitle,
    RulesTitleFormatter,
    TitleFormatter,
)
from squid.records.domain.models import (
    BaseTitle,
    BuildKind,
    Candidate,
    CandidateGap,
    ResolutionStatus,
    TimingMethod,
    TimingVariant,
    TitleResolution,
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
    "BaseTitle",
    "BuildKind",
    "Candidate",
    "CandidateGap",
    "CategorySemantics",
    "DoorCategory",
    "ExtenderCategory",
    "FormattedTitle",
    "ResolutionStatus",
    "RulesTitleFormatter",
    "TimingMethod",
    "TimingVariant",
    "TitleFormatter",
    "TitleResolution",
    "VersionScope",
    "generate_category_subsets",
    "reduce_timing_variants",
    "resolve_fastest",
    "resolve_fastest_smallest",
    "resolve_first",
    "resolve_smallest",
    "resolve_smallest_fastest",
]
