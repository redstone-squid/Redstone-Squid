"""Domain events published by record_run writes."""

from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True, slots=True)
class RecordRunActivated:
    event_type: ClassVar[str] = "record_run.activated"
    aggregate_kind: ClassVar[str] = "record_run"
    schema_version: ClassVar[int] = 1

    aggregate_id: int
    previous_run_id: int | None
    build_kind: str
    version_id: int | None

    def payload(self) -> dict[str, object]:
        return {
            "previous_run_id": self.previous_run_id,
            "baseline": self.previous_run_id is None,
            "build_kind": self.build_kind,
            "version_id": self.version_id,
        }
