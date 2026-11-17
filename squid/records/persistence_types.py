"""Typed conversion for saved concrete door evaluation evidence."""

from typing import override

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

from squid.records.domain.door_evidence import DoorRecordEvidence


class DoorRecordEvidenceJSON(TypeDecorator[DoorRecordEvidence]):
    impl = JSONB(none_as_null=True)
    cache_ok = True

    @property
    @override
    def python_type(self) -> type[DoorRecordEvidence]:
        return DoorRecordEvidence

    @override
    def process_bind_param(self, value: DoorRecordEvidence | None, dialect: Dialect) -> dict[str, object] | None:
        return DoorRecordEvidence.model_validate(value).model_dump(mode="json") if value is not None else None

    @override
    def process_result_value(self, value: object, dialect: Dialect) -> DoorRecordEvidence | None:
        return DoorRecordEvidence.model_validate(value) if value is not None else None
