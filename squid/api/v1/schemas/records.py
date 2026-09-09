"""Public computed-record representations."""

from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import ConfigDict, Field

from squid.api.v1.schemas import FromDomain
from squid.api.v1.schemas.builds import BuildSummary
from squid.records.application.models import PublishedRecord


class RecordSummary(FromDomain[PublishedRecord]):
    """One published computed record result.

    A result belongs to the currently published computation run for its build kind and version, and a
    newer run replaces it. `competition_id` names the competition across runs; it is what a record
    notification subscription is keyed on.
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    definition_id: int
    competition_id: UUID
    title: str
    subtitle: str | None
    record_class: str = Field(
        description="One of `first`, `fastest`, `smallest`, `fastest_smallest` or `smallest_fastest`."
    )
    build_kind: str = Field(description="One of `door`, `entrance`, `extender` or `utility`.")
    version_scope: str = Field(
        description="`all_time` for a record over every version, `current` for one scoped to the newest."
    )
    status: str = Field(
        description="`resolved` when the competition produced official holders, `unresolved` when the candidates left "
        "it undecided, `no_candidate` when nothing qualified."
    )
    holder_build_ids: list[int] = Field(description="Non-empty only when `status` is `resolved`.")
    computed_at: datetime

    @classmethod
    def from_domain(cls, record: PublishedRecord, /) -> Self:
        return cls(
            id=record.id,
            definition_id=record.definition_id,
            competition_id=record.competition_id,
            title=record.title,
            subtitle=record.subtitle,
            record_class=record.record_class,
            build_kind=record.build_kind,
            version_scope=record.version_scope,
            status=record.status,
            holder_build_ids=list(record.holder_build_ids),
            computed_at=record.computed_at,
        )


class RecordDetail(RecordSummary):
    """One published record result with its ordered holder builds."""

    holder_builds: list[BuildSummary] = Field(description="The builds named by `holder_build_ids`, in that order.")
