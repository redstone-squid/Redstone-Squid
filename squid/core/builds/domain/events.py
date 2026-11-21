"""Domain events published by build edits."""

from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True, slots=True)
class BuildEdited:
    event_type: ClassVar[str] = "build.edited"
    aggregate_kind: ClassVar[str] = "build"
    schema_version: ClassVar[int] = 1

    aggregate_id: int
    revision: int

    def payload(self) -> dict[str, object]:
        return {"revision": self.revision}
