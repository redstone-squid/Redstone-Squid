"""JSON-only messages between the API process and a range worker.

Taxonomy filtering finishes in the parent transaction. Workers receive only
candidate arithmetic facts and cannot query the database or publish results.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from squid.records.application.expansion_regions import DoorRangeAnalysis, DoorRangeQuery
from squid.records.application.range_snapshots import RangeSource
from squid.records.domain.regions import DEFAULT_REGION_LIMITS, RegionAnalysisStatus, SmallestRegionAnalysis


class RangeWorkerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: DoorRangeQuery
    sources: Annotated[tuple[RangeSource, ...], Field(max_length=DEFAULT_REGION_LIMITS.candidates)]


RANGE_RESULT = TypeAdapter(DoorRangeAnalysis)


def failed_analysis(status: RegionAnalysisStatus, diagnostic: str) -> DoorRangeAnalysis:
    return DoorRangeAnalysis(SmallestRegionAnalysis(status, diagnostic=diagnostic), ())
