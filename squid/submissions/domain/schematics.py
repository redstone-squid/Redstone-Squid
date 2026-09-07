"""Private schematic source states and draft selection facts."""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class DraftSchematicState(StrEnum):
    """Source-storage state; quarantine is never evidence of successful sanitization."""

    UPLOADING = "uploading"
    WAITING_SANITIZER = "waiting_sanitizer"
    FAILED = "failed"
    DELETING = "deleting"
    DELETED = "deleted"


@dataclass(frozen=True, slots=True)
class DraftSchematic:
    """A private source artifact and its selection within a draft."""

    id: UUID
    filename: str
    sha256: str | None
    byte_size: int | None
    state: DraftSchematicState
    primary: bool = False
    discarded: bool = False

    @property
    def object_key(self) -> str:
        return f"submissions/quarantine/{self.id}"
