"""Immutable file provenance supplied alongside inferred message facts."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class SubmissionSourceFile:
    """A supplied Discord file whose assignment must be resolved before submission."""

    id: UUID
    filename: str
    content_type: str | None
    url: str
    size: int
