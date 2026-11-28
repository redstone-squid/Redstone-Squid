"""Account-facing views of submitted builds and proposed edits."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import JsonValue


@dataclass(frozen=True)
class SubmissionSummary:
    id: UUID
    build_id: int
    title: str | None
    kind: Literal["new_build", "edit"]
    status: str
    submitted_at: datetime
    published: bool
    decided_revision: int | None


@dataclass(frozen=True)
class SubmissionDetail:
    submission: SubmissionSummary
    base_revision: int | None
    vote_session_id: int | None
    facts: dict[str, JsonValue]
    review_revision: int | None
    reviewed_facts: dict[str, JsonValue]
    can_approve: bool = False
    can_reject: bool = False
