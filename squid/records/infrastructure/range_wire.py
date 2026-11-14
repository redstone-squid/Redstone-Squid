"""JSON-only messages between the API process and a range worker.

Taxonomy filtering finishes in the parent transaction. Workers receive only
candidate arithmetic facts and cannot query the database or publish results.
"""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from squid.builds.domain.expansion import DoorExpansion
from squid.records.application.expansion_regions import DoorRangeAnalysis, DoorRangeQuery
from squid.records.application.models import CandidateBuild
from squid.records.domain.models import BuildKind, Candidate
from squid.records.domain.regions import DEFAULT_REGION_LIMITS, RegionAnalysisStatus, SmallestRegionAnalysis


class RangeSource(BaseModel):
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


class RangeWorkerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: DoorRangeQuery
    sources: Annotated[tuple[RangeSource, ...], Field(max_length=DEFAULT_REGION_LIMITS.candidates)]


RANGE_RESULT = TypeAdapter(DoorRangeAnalysis)


def failed_analysis(status: RegionAnalysisStatus, diagnostic: str) -> DoorRangeAnalysis:
    return DoorRangeAnalysis(SmallestRegionAnalysis(status, diagnostic=diagnostic), ())
