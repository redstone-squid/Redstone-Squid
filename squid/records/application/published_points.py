"""Select one opening from saved regions without re-evaluating live source formulas."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from squid.core.errors import DataIntegrityError, ValidationError
from squid.core.i18n import tr
from squid.records.application.expansion_regions import DoorRangeQuery, DoorRegionConfiguration
from squid.records.application.range_snapshots import RangeAttempt, RangePublication
from squid.records.domain.regions import RegionAnalysisStatus, SmallestRecordRegion


class PublishedPointStatus(StrEnum):
    PUBLISHED = "published"
    NOT_PUBLISHED = "not_published"
    OUTSIDE_QUERY = "outside_query"


@dataclass(frozen=True, slots=True)
class RangeAttemptSummary:
    """Attempt provenance without the full partition of opening sizes."""

    id: int
    generation: int
    rules_revision_id: int
    started_at: datetime
    completed_at: datetime | None
    captured_at: datetime | None
    snapshot_hash: str | None
    analysis_status: RegionAnalysisStatus | None
    error: str | None

    @classmethod
    def from_attempt(cls, attempt: RangeAttempt) -> RangeAttemptSummary:
        return cls(
            attempt.id,
            attempt.generation,
            attempt.rules_revision_id,
            attempt.started_at,
            attempt.completed_at,
            attempt.captured_at,
            attempt.snapshot_hash,
            None if attempt.result is None else attempt.result.analysis.status,
            attempt.error,
        )


@dataclass(frozen=True, slots=True)
class PublishedDoorPoint:
    """A saved record outcome, or an explicit reason no published answer applies.

    A published region can itself be unresolved or have no candidate. Those
    outcomes differ from an unpublished query or an opening outside its bounds.
    """

    query_id: int
    query: DoorRangeQuery
    opening: tuple[int, int, int]
    status: PublishedPointStatus
    publication: RangeAttemptSummary | None
    latest: RangeAttemptSummary | None
    region: SmallestRecordRegion | None
    configurations: tuple[DoorRegionConfiguration, ...]

    @classmethod
    def from_publication(cls, publication: RangePublication, width: int) -> PublishedDoorPoint:
        """Select an opening from a publication without evaluating live source formulas."""
        if type(width) is not int or not 1 <= width <= 2**31 - 1:
            raise ValidationError(
                tr(t"records-opening-width-invalid = Opening width must be a positive 32-bit integer.")
            )
        query = publication.query
        active = publication.active
        status = PublishedPointStatus.NOT_PUBLISHED
        region = None
        configurations: tuple[DoorRegionConfiguration, ...] = ()
        if width < query.minimum or (query.maximum is not None and width > query.maximum):
            status = PublishedPointStatus.OUTSIDE_QUERY
        elif active is not None:
            if active.result is None or active.result.analysis.status is not RegionAnalysisStatus.EXACT:
                msg = "An active range publication must contain an exact analysis."
                raise DataIntegrityError(msg, context={"run_id": active.id})
            matches = [r for r in active.result.analysis.regions if r.domain.contains(width)]
            if len(matches) != 1:
                msg = "A published opening must belong to exactly one saved region."
                raise DataIntegrityError(msg, context={"run_id": active.id, "width": width})
            status = PublishedPointStatus.PUBLISHED
            region = matches[0]
            configurations = active.result.configurations
        return cls(
            publication.query_id,
            query,
            (width, query.height, query.depth),
            status,
            None if active is None else RangeAttemptSummary.from_attempt(active),
            None if publication.latest is None else RangeAttemptSummary.from_attempt(publication.latest),
            region,
            configurations,
        )
