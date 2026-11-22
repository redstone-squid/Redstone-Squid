"""Rule-driven record domain primitives."""

from squid.core.records.domain.categories import CategorySemantics, generate_category_subsets
from squid.core.records.domain.formatting import (
    DoorCategory,
    ExtenderCategory,
    FormattedTitle,
    RulesTitleFormatter,
    TitleFormatter,
)
from squid.core.records.domain.models import (
    BuildKind,
    Candidate,
    CandidateGap,
    RecordCriterion,
    RecordResolution,
    ResolutionStatus,
    TimingMethod,
    TimingVariant,
    VersionScope,
)
from squid.core.records.domain.resolution import (
    reduce_timing_variants,
    resolve_fastest,
    resolve_fastest_smallest,
    resolve_first,
    resolve_smallest,
    resolve_smallest_fastest,
)

__all__ = [
    "BuildKind",
    "Candidate",
    "CandidateGap",
    "CategorySemantics",
    "DoorCategory",
    "ExtenderCategory",
    "FormattedTitle",
    "RecordCriterion",
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
