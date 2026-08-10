"""Public computed-record representations."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from squid.api.v1.schemas.builds import BuildSummary
from squid.records.application.models import ActiveRecord


class RecordSummary(BaseModel):
    """One active computed record result."""

    model_config = ConfigDict(extra="forbid")

    id: int
    definition_id: int
    title: str
    subtitle: str | None
    record_class: str
    build_kind: str
    version_scope: str
    status: str
    holder_build_ids: list[int]
    computed_at: datetime

    @classmethod
    def from_domain(cls, record: ActiveRecord) -> "RecordSummary":
        return cls(
            id=record.id,
            definition_id=record.definition_id,
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
    """One active record result with its ordered holder builds."""

    holder_builds: list[BuildSummary]
