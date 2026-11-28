"""Account-facing views of submitted builds and proposed edits."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import JsonValue


class SubmissionStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    STALE = "stale"


@dataclass(frozen=True)
class SubmissionSummary:
    id: UUID
    build_id: int
    title: str | None
    kind: Literal["new_build", "edit"]
    status: SubmissionStatus
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
    feedback: str | None = None
    can_approve: bool = False
    can_reject: bool = False


@dataclass(frozen=True)
class CreditedBuild:
    build_id: int
    title: str | None
    roles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SubmissionListFilters:
    statuses: tuple[SubmissionStatus, ...] = ()


ALL_SUBMISSIONS = SubmissionListFilters()
