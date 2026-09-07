"""Resolve authoritative inputs before submission execution."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from squid.core.errors import InvalidStateError, JSONValue
from squid.core.i18n import tr
from squid.sponsors import PublicSponsor
from squid.submissions.application.drafts import (
    StoredDraft,
    ValidatedDraft,
)
from squid.submissions.domain import SubmissionOrigin
from squid.submissions.domain.finalization import (
    NormalizedSubmission,
    SchematicArtifactState,
    SubmissionArtifactReadiness,
    SubmissionAttentionIssue,
    SubmissionAttentionReason,
    SubmissionCategory,
)
from squid.submissions.domain.normalization import normalize_submission

_STABLE_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@dataclass(frozen=True, slots=True)
class PreparedSubmission:
    """A validated submission ready for durable finalization."""

    value: NormalizedSubmission


@dataclass(frozen=True, slots=True)
class PreparationRejected:
    """Owner-repair issues found while preparing a validated draft."""

    issues: tuple[SubmissionAttentionIssue, ...]

    def __post_init__(self) -> None:
        if not self.issues:
            msg = tr(t"rejected submission preparation requires at least one issue")
            raise InvalidStateError(msg)


type PreparationResult = PreparedSubmission | PreparationRejected


class DraftArtifactReadiness(Protocol):
    """Read backend-owned artifact state for one draft.

    Implementations must inspect every associated upload. They may expose media UUIDs
    only after normalization and a schematic UUID only after sanitization; pending or
    rejected uploads must be represented by stable attention issues.
    """

    async def assess(self, draft_id: UUID) -> SubmissionArtifactReadiness: ...


class SubmissionSponsorResolver(Protocol):
    """Resolve only an installation's currently authorized public sponsor projection."""

    async def resolve(self, installation_id: UUID) -> PublicSponsor | None: ...


class SubmissionPreparation:
    """Combine validated answers with authoritative sponsor and attachment facts."""

    def __init__(
        self,
        artifacts: DraftArtifactReadiness,
        sponsors: SubmissionSponsorResolver | None = None,
    ) -> None:
        self._artifacts = artifacts
        self._sponsors = sponsors

    async def prepare(self, validated: ValidatedDraft) -> PreparationResult:
        """Return a normalized submission or deterministic owner-repair issues."""
        draft = validated.draft
        answers = validated.normalized_answers
        sponsor, sponsor_issues = await self._resolve_sponsor(draft, answers)
        assessment = await self._artifacts.assess(draft.snapshot.id)
        issues = _unique_issues(
            (
                *_artifact_issues(draft.origin, assessment),
                *_taxonomy_issues(answers, draft.snapshot.category),
                *sponsor_issues,
            )
        )
        if issues:
            return PreparationRejected(issues)
        return PreparedSubmission(
            normalize_submission(
                draft.snapshot,
                answers,
                assessment,
                sponsor,
                origin=draft.origin,
                source_installation_id=draft.source_installation_id,
            )
        )

    async def _resolve_sponsor(
        self,
        draft: StoredDraft,
        answers: Mapping[str, JSONValue],
    ) -> tuple[PublicSponsor | None, tuple[SubmissionAttentionIssue, ...]]:
        requested = draft.origin is SubmissionOrigin.PAPER and answers.get("sponsor_attribution") is True
        if not requested:
            return None, ()
        if draft.source_installation_id is None or self._sponsors is None:
            return None, (_sponsor_unavailable(),)
        sponsor = await self._sponsors.resolve(draft.source_installation_id)
        if sponsor is None or sponsor.installation_id != draft.source_installation_id:
            return None, (_sponsor_unavailable(),)
        return sponsor, ()


def _artifact_issues(
    origin: SubmissionOrigin,
    readiness: SubmissionArtifactReadiness,
) -> tuple[SubmissionAttentionIssue, ...]:
    issues = list(readiness.issues)
    match readiness.schematic_state:
        case SchematicArtifactState.ABSENT:
            if origin in {SubmissionOrigin.PAPER, SubmissionOrigin.FABRIC}:
                issues.append(SubmissionAttentionIssue("schematic", SubmissionAttentionReason.SCHEMATIC_REQUIRED))
        case SchematicArtifactState.PROCESSING:
            issues.append(SubmissionAttentionIssue("schematic", SubmissionAttentionReason.SCHEMATIC_PROCESSING))
        case SchematicArtifactState.REJECTED:
            issues.append(SubmissionAttentionIssue("schematic", SubmissionAttentionReason.SCHEMATIC_REJECTED))
        case SchematicArtifactState.SANITIZED:
            pass
    return _unique_issues(issues)


def _taxonomy_issues(
    answers: Mapping[str, JSONValue],
    category: str,
) -> tuple[SubmissionAttentionIssue, ...]:
    fields = ["restrictions", "showcase_tags"]
    if category in {SubmissionCategory.DOOR.value, SubmissionCategory.EXTENDER.value}:
        fields.append("patterns")
    issues: list[SubmissionAttentionIssue] = []
    for field_id in fields:
        value = answers.get(field_id, ())
        if isinstance(value, Sequence) and not isinstance(value, str | bytes):
            values = [item for item in value if isinstance(item, str)]
            if len(values) != len(set(values)) or any(_STABLE_KEY.fullmatch(item) is None for item in values):
                issues.append(SubmissionAttentionIssue(field_id, SubmissionAttentionReason.UNKNOWN_OPTION))
    return tuple(issues)


def _sponsor_unavailable() -> SubmissionAttentionIssue:
    return SubmissionAttentionIssue("sponsor_attribution", SubmissionAttentionReason.SPONSOR_UNAVAILABLE)


def _unique_issues(issues: Sequence[SubmissionAttentionIssue]) -> tuple[SubmissionAttentionIssue, ...]:
    return tuple(dict.fromkeys(issues))
