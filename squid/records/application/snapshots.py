"""Concrete computation inputs detached from database sessions."""

from dataclasses import dataclass
from datetime import datetime

from squid.records.application.models import CandidateBuild, CategoryIdentity
from squid.records.domain.models import BuildKind


@dataclass(frozen=True, slots=True, kw_only=True)
class RecordSnapshot:
    """In-memory inputs for one scope, retained only until publication.

    Capture time supplies evaluation time so computation does not read the clock.
    """

    rules_revision_id: int
    kind: BuildKind
    version_id: int | None
    sources: tuple[CandidateBuild, ...]
    requested: tuple[CategoryIdentity, ...]
    captured_at: datetime
    input_generation: int = 0
