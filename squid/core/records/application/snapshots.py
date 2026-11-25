"""Concrete computation inputs detached from database sessions."""

from dataclasses import dataclass, field
from datetime import datetime

from squid.core.records.application.models import (
    CandidateBuild,
    CategoryIdentity,
    PriorPublication,
    ScopedCategory,
)
from squid.core.records.domain.models import BuildKind


@dataclass(frozen=True, slots=True, kw_only=True)
class RecordSnapshot:
    """In-memory inputs for one batch of one kind, retained only until publication.

    `sources` holds every approved candidate of the kind. A full batch recomputes
    every category in `version_ids` and retires live records in other versions.
    Otherwise the batch recomputes the categories that `changed_builds` can
    affect, plus `targets`. A backstop sets `expected_builds` to every build with
    pending work, so publication can tell expected differences from drift.

    Capture time supplies evaluation time so computation does not read the clock.
    """

    rules_revision_id: int
    kind: BuildKind
    version_ids: tuple[int | None, ...]
    sources: tuple[CandidateBuild, ...]
    requested: tuple[CategoryIdentity, ...]
    captured_at: datetime
    input_generation: int = 0
    full: bool = False
    changed_builds: frozenset[int] = frozenset()
    targets: frozenset[ScopedCategory] = frozenset()
    prior: PriorPublication = field(default_factory=PriorPublication)
    expected_builds: frozenset[int] | None = None
