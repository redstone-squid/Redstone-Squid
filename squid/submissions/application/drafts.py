"""Application orchestration for account-owned revisioned drafts."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID, uuid4

from whenever import Instant

from squid.core.errors import JSONValue
from squid.submissions.domain import (
    DraftChange,
    DraftSnapshot,
    DraftStatus,
    FormManifest,
    SubmissionOrigin,
)
from squid.submissions.errors import (
    DraftAccessDeniedError,
    DraftCapacityExceededError,
    DraftIncompleteError,
    DraftNotFoundError,
    DraftSchemaUnsupportedError,
    SanitizedSchematicRequiredError,
)

DEFAULT_DRAFT_RETENTION_DAYS = 7
DEFAULT_ACCOUNT_DRAFT_CAPACITY = 10


@dataclass(frozen=True, slots=True)
class StoredDraft:
    """A compacted draft snapshot and its retention/provenance metadata."""

    snapshot: DraftSnapshot
    origin: SubmissionOrigin
    created_at: Instant
    updated_at: Instant
    expires_at: Instant


@dataclass(frozen=True, slots=True)
class AppliedDraftChange:
    """Result of an atomic repository mutation, including idempotent replays."""

    draft: StoredDraft
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class ProcessingDraft:
    """A validated draft locked for asynchronous artifact processing/finalization."""

    draft: StoredDraft
    normalized_answers: dict[str, JSONValue]


class DraftRepository(Protocol):
    """Atomic persistence required by the draft application service."""

    async def count_active_for_account(self, account_id: int) -> int: ...

    async def create(self, draft: StoredDraft) -> StoredDraft: ...

    async def get(self, draft_id: UUID) -> StoredDraft | None: ...

    async def replayed_change(
        self,
        draft_id: UUID,
        account_id: int,
        idempotency_key: str,
    ) -> AppliedDraftChange | None: ...

    async def apply_change(
        self,
        draft_id: UUID,
        account_id: int,
        change: DraftChange,
        *,
        expires_at: Instant,
    ) -> AppliedDraftChange: ...

    async def transition(
        self,
        draft_id: UUID,
        account_id: int,
        *,
        expected_revision: int,
        status: DraftStatus,
        expires_at: Instant,
    ) -> StoredDraft: ...


class FormManifestRegistry(Protocol):
    """Resolve current and still-pinned form revisions."""

    async def current(self, *, locale: str | None) -> FormManifest: ...

    async def get(self, schema_id: str, revision: int, *, locale: str | None) -> FormManifest | None: ...


class AccountDraftCapacity(Protocol):
    """Resolve staff-adjusted synchronized-draft capacity for an account."""

    async def limit_for(self, account_id: int) -> int: ...


class FixedAccountDraftCapacity:
    """Default capacity policy used until an account receives an override."""

    def __init__(self, limit: int = DEFAULT_ACCOUNT_DRAFT_CAPACITY) -> None:
        if limit < 1:
            msg = "draft capacity must be positive"
            raise ValueError(msg)
        self._limit = limit

    async def limit_for(self, account_id: int) -> int:
        """Return the configured capacity; account ID is accepted for port compatibility."""
        del account_id
        return self._limit


class SubmissionDraftService:
    """Create, edit, validate, and lock drafts without transport-specific behavior."""

    def __init__(
        self,
        repository: DraftRepository,
        manifests: FormManifestRegistry,
        capacity: AccountDraftCapacity | None = None,
        *,
        retention_days: int = DEFAULT_DRAFT_RETENTION_DAYS,
    ) -> None:
        if retention_days < 1:
            msg = "draft retention must be positive"
            raise ValueError(msg)
        self._repository = repository
        self._manifests = manifests
        self._capacity = capacity or FixedAccountDraftCapacity()
        self._retention_days = retention_days

    async def create(
        self,
        *,
        owner_account_id: int,
        category: str,
        origin: SubmissionOrigin,
        client_capabilities: frozenset[str],
        locale: str | None,
        now: Instant | None = None,
        draft_id: UUID | None = None,
    ) -> StoredDraft:
        """Create an empty synchronized draft pinned to the current schema revision."""
        limit = await self._capacity.limit_for(owner_account_id)
        if await self._repository.count_active_for_account(owner_account_id) >= limit:
            raise DraftCapacityExceededError(limit)
        manifest = await self._manifests.current(locale=locale)
        manifest.category(category)
        missing = manifest.unsupported_required_capabilities(category, client_capabilities, origin)
        if missing:
            raise DraftSchemaUnsupportedError(missing)
        created_at = now or Instant.now()
        stored = StoredDraft(
            snapshot=DraftSnapshot(
                id=draft_id or uuid4(),
                owner_account_id=owner_account_id,
                schema_id=manifest.schema_id,
                schema_revision=manifest.revision,
                category=category,
            ),
            origin=origin,
            created_at=created_at,
            updated_at=created_at,
            expires_at=created_at.add(days=self._retention_days, days_assumed_24h_ok=True),
        )
        return await self._repository.create(stored)

    async def get_owned(self, draft_id: UUID, account_id: int) -> StoredDraft:
        """Return one draft after enforcing its v1 single-owner boundary."""
        draft = await self._repository.get(draft_id)
        if draft is None:
            raise DraftNotFoundError(draft_id)
        self._require_owner(draft, account_id)
        return draft

    async def apply_change(
        self,
        draft_id: UUID,
        account_id: int,
        change: DraftChange,
        *,
        locale: str | None,
        now: Instant | None = None,
    ) -> AppliedDraftChange:
        """Validate field IDs/types and atomically persist one optimistic edit."""
        replayed = await self._repository.replayed_change(draft_id, account_id, change.idempotency_key)
        if replayed is not None:
            return replayed
        current = await self.get_owned(draft_id, account_id)
        manifest = await self._pinned_manifest(current, locale)
        candidate = current.snapshot.apply(change)
        errors = manifest.validate_answers(
            candidate.category,
            candidate.answers,
            origin=current.origin,
            require_complete=False,
        )
        if errors:
            raise DraftIncompleteError(errors)
        touched_at = now or Instant.now()
        return await self._repository.apply_change(
            draft_id,
            account_id,
            change,
            expires_at=touched_at.add(days=self._retention_days, days_assumed_24h_ok=True),
        )

    async def begin_processing(
        self,
        draft_id: UUID,
        account_id: int,
        *,
        has_sanitized_schematic: bool,
        locale: str | None,
        now: Instant | None = None,
    ) -> ProcessingDraft:
        """Validate and lock a complete draft for asynchronous finalization."""
        current = await self.get_owned(draft_id, account_id)
        if current.origin in {SubmissionOrigin.PAPER, SubmissionOrigin.FABRIC} and not has_sanitized_schematic:
            raise SanitizedSchematicRequiredError
        manifest = await self._pinned_manifest(current, locale)
        errors = manifest.validate_answers(
            current.snapshot.category,
            current.snapshot.answers,
            origin=current.origin,
        )
        if errors:
            raise DraftIncompleteError(errors)
        normalized = manifest.apply_defaults(
            current.snapshot.category,
            current.snapshot.answers,
            origin=current.origin,
        )
        touched_at = now or Instant.now()
        transitioned = await self._repository.transition(
            draft_id,
            account_id,
            expected_revision=current.snapshot.revision,
            status=DraftStatus.PROCESSING,
            expires_at=touched_at.add(days=self._retention_days, days_assumed_24h_ok=True),
        )
        return ProcessingDraft(transitioned, normalized)

    async def _pinned_manifest(self, draft: StoredDraft, locale: str | None) -> FormManifest:
        manifest = await self._manifests.get(
            draft.snapshot.schema_id,
            draft.snapshot.schema_revision,
            locale=locale,
        )
        if manifest is None:
            raise DraftSchemaUnsupportedError((draft.snapshot.schema_id,))
        return manifest

    @staticmethod
    def _require_owner(draft: StoredDraft, account_id: int) -> None:
        if draft.snapshot.owner_account_id != account_id:
            raise DraftAccessDeniedError
