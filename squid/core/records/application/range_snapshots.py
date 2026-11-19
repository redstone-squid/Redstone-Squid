"""Immutable arithmetic inputs and publication metadata for registered range queries."""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from squid.core.builds.domain.expansion import DoorExpansion
from squid.core.records.application.expansion_regions import DoorRangeAnalysis, DoorRangeQuery
from squid.core.records.application.models import CandidateBuild
from squid.core.records.domain.models import BuildKind, Candidate

RANGE_CALCULATOR_VERSION = "door-ranges-1"
RANGE_ARITHMETIC_VERSION = "rational-polynomial-1"


class RangeSource(BaseModel):
    """Arithmetic facts after taxonomy filtering, independent of later build edits."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    build_id: int
    completion_at: datetime | None
    volume: int | None
    opening: tuple[int, int, int] | None
    expansion: DoorExpansion | None

    @classmethod
    def from_candidate(cls, source: CandidateBuild) -> RangeSource:
        return cls(
            build_id=source.candidate.build_id,
            completion_at=source.candidate.completion_at,
            volume=source.candidate.fixed_volume,
            opening=source.door_opening,
            expansion=source.expansion,
        )

    def to_candidate(self) -> CandidateBuild:
        return CandidateBuild(
            BuildKind.DOOR,
            Candidate(self.build_id, self.completion_at, self.volume),
            frozenset(),
            (),
            (),
            expansion=self.expansion,
            door_opening=self.opening,
        )


def canonical_range_query(query: DoorRangeQuery) -> str:
    value = query.model_dump(mode="json")
    # Preserve existing fixed/all-time registration keys across this addition.
    if not query.follow_current_version:
        value.pop("follow_current_version")
    for key in ("type_ids", "restriction_ids", "restriction_values"):
        value[key] = sorted(value[key])
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class RangeSnapshotDocument(BaseModel):
    """Versioned inputs used by one attempt; source claims remain unverified."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    calculator_version: str = RANGE_CALCULATOR_VERSION
    arithmetic_version: str = RANGE_ARITHMETIC_VERSION
    query: DoorRangeQuery
    sources: tuple[RangeSource, ...]
    captured_at: datetime
    resolved_version_id: int | None = None

    @property
    def effective_version_id(self) -> int | None:
        return self.resolved_version_id if self.query.follow_current_version else self.query.version_id

    def digest(self) -> str:
        payload = self.model_dump(mode="json")
        payload["query"] = json.loads(canonical_range_query(self.query))
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def semantic_digest(self) -> str:
        """Compare source evidence and resolved scope without the capture clock."""
        payload = self.model_dump(mode="json", exclude={"captured_at"})
        payload["query"] = json.loads(canonical_range_query(self.query))
        payload["resolved_version_id"] = self.effective_version_id
        payload["sources"].sort(key=lambda source: source["build_id"])
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class RangeAttempt:
    id: int
    query_id: int
    generation: int
    rules_revision_id: int
    started_at: datetime
    completed_at: datetime | None
    is_active: bool
    result: DoorRangeAnalysis | None
    error: str | None
    snapshot_hash: str | None
    captured_at: datetime | None
    resolved_version_id: int | None = field(default=None, kw_only=True)


@dataclass(frozen=True, slots=True)
class RangePublication:
    query_id: int
    query: DoorRangeQuery
    active: RangeAttempt | None
    latest: RangeAttempt | None
    last_checked_at: datetime | None = field(default=None, kw_only=True)
    refresh_pending: bool = field(default=False, kw_only=True)
