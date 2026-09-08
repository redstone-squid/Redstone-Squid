"""Application orchestration for account-owned revisioned drafts."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID, uuid4

from whenever import Instant

from squid.core.errors import InvalidStateError, JSONValue, ValidationError
from squid.core.i18n import tr
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
    DraftStateConflictError,
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
    source_installation_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.origin is not SubmissionOrigin.PAPER and self.source_installation_id is not None:
            msg = tr(t"Only Paper drafts may retain an installation provenance ID.")
            raise ValidationError(msg)


@dataclass(frozen=True, slots=True)
class AppliedDraftChange:
    """Result of an atomic repository mutation, including idempotent replays."""

    draft: StoredDraft
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class ValidatedDraft:
    """A complete pinned draft prepared for server-owned finalization checks."""

    draft: StoredDraft
    normalized_answers: dict[str, JSONValue]


class DraftRepository(Protocol):
    """Atomic persistence required by the draft application service."""

    async def count_active_for_account(self, account_id: int) -> int:
        """How many unexpired drafts in an active state the account owns."""
        ...

    async def list_active_for_account(
        self,
        account_id: int,
        *,
        now: Instant,
        limit: int,
    ) -> tuple[StoredDraft, ...]:
        """The account's unexpired active drafts as of `now`, most recently updated first."""
        ...

    async def create(self, draft: StoredDraft) -> StoredDraft:
        """Persist a new draft together with its owner access row."""
        ...

    async def get(self, draft_id: UUID) -> StoredDraft | None:
        """The draft with this ID whatever its owner or state, or None when there is none."""
        ...

    async def replayed_change(
        self,
        draft_id: UUID,
        account_id: int,
        idempotency_key: str,
    ) -> AppliedDraftChange | None:
        """The outcome already recorded for this idempotency key, or None when the key is new."""
        ...

    async def apply_change(
        self,
        draft_id: UUID,
        account_id: int,
        change: DraftChange,
        *,
        updated_at: Instant,
        expires_at: Instant,
    ) -> AppliedDraftChange:
        """Apply one change under a per-draft lock and retain it as an immutable record.

        Returns the earlier outcome marked `replayed` when the idempotency key was already used.

        Raises:
            DraftNotFoundError: If no draft has this ID.
            DraftAccessDeniedError: If the account does not own the draft.
            DraftRevisionConflictError: If the change is based on a stale revision.
            ValidationError: If the draft is not editable, or the result exceeds the JSON budget.
        """
        ...

    async def transition(
        self,
        draft_id: UUID,
        account_id: int,
        *,
        expected_revision: int,
        status: DraftStatus,
        updated_at: Instant,
        expires_at: Instant,
    ) -> StoredDraft:
        """Move an owned draft to another lifecycle state under a per-draft lock.

        Raises:
            DraftNotFoundError: If no draft has this ID.
            DraftAccessDeniedError: If the account does not own the draft.
            DraftRevisionConflictError: If the stored revision is not `expected_revision`.
            ValidationError: If the transition is not allowed from the current state.
        """
        ...

    async def delete_owned(self, draft_id: UUID, account_id: int) -> bool:
        """Delete an owned draft, returning False when it no longer exists.

        Raises:
            DraftAccessDeniedError: If the account does not own the draft.
            DraftStateConflictError: If the draft is no longer user-editable.
        """
        ...

    async def expire_due(self, *, now: Instant, limit: int = 100) -> int:
        """Expire at most `limit` drafts already past their retention, and return how many.

        Expiring a draft also drops its unfinished finalization job and discards its unfinished
        media normalization work.
        """
        ...


class FormManifestRegistry(Protocol):
    """Resolve current and still-pinned form revisions."""

    async def current(self, *, locale: str | None) -> FormManifest:
        """The newest form revision, with labels localized when a locale is given."""
        ...

    async def get(self, schema_id: str, revision: int, *, locale: str | None) -> FormManifest | None:
        """One pinned revision, or None when this build no longer ships it."""
        ...


class AccountDraftCapacity(Protocol):
    """Resolve staff-adjusted synchronized-draft capacity for an account."""

    async def limit_for(self, account_id: int) -> int:
        """How many active drafts the account may hold at once."""
        ...


class FixedAccountDraftCapacity:
    """Default capacity policy used until an account receives an override."""

    def __init__(self, limit: int = DEFAULT_ACCOUNT_DRAFT_CAPACITY) -> None:
        if limit < 1:
            msg = tr(t"draft capacity must be positive")
            raise InvalidStateError(msg)
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
        now: Callable[[], Instant] = Instant.now,
    ) -> None:
        if retention_days < 1:
            msg = tr(t"draft retention must be positive")
            raise InvalidStateError(msg)
        self._repository = repository
        self._manifests = manifests
        self._capacity = capacity or FixedAccountDraftCapacity()
        self._retention_days = retention_days
        self._now = now

    async def create(
        self,
        *,
        owner_account_id: int,
        category: str,
        origin: SubmissionOrigin,
        client_capabilities: frozenset[str],
        locale: str | None,
        source_installation_id: UUID | None = None,
        now: Instant | None = None,
        draft_id: UUID | None = None,
    ) -> StoredDraft:
        """Create an empty synchronized draft pinned to the current schema revision.

        Raises:
            ValidationError: If installation provenance is present without a Paper origin, or
                absent with one.
            DraftCapacityExceededError: If the account already holds its limit of active drafts.
            DraftSchemaUnsupportedError: If the client cannot render every required field.
        """
        if (origin is SubmissionOrigin.PAPER) != (source_installation_id is not None):
            msg = tr(t"Paper drafts require server-derived installation provenance.")
            raise ValidationError(msg)
        limit = await self._capacity.limit_for(owner_account_id)
        if await self._repository.count_active_for_account(owner_account_id) >= limit:
            raise DraftCapacityExceededError(limit)
        manifest = await self._manifests.current(locale=locale)
        manifest.category(category)
        missing = manifest.unsupported_required_capabilities(category, client_capabilities, origin)
        if missing:
            raise DraftSchemaUnsupportedError(missing)
        created_at = now or self._now()
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
            source_installation_id=source_installation_id,
        )
        return await self._repository.create(stored)

    async def list_active(self, account_id: int, *, limit: int = 10) -> tuple[StoredDraft, ...]:
        """List a bounded newest-first view of one account's unexpired active drafts.

        Raises:
            InvalidStateError: If `limit` is outside 1..`DEFAULT_ACCOUNT_DRAFT_CAPACITY`.
        """
        if not 1 <= limit <= DEFAULT_ACCOUNT_DRAFT_CAPACITY:
            maximum = DEFAULT_ACCOUNT_DRAFT_CAPACITY
            raise InvalidStateError(tr(t"draft discovery limit must be between 1 and {maximum}"))
        return await self._repository.list_active_for_account(account_id, now=self._now(), limit=limit)

    async def get_owned(self, draft_id: UUID, account_id: int) -> StoredDraft:
        """Return one draft after enforcing its single-owner boundary.

        A draft past its retention is reported as expired even before the sweeper rewrites it.

        Raises:
            DraftNotFoundError: If no draft has this ID.
            DraftAccessDeniedError: If the account does not own it.
            DraftStateConflictError: If the draft has expired.
        """
        draft = await self._repository.get(draft_id)
        if draft is None:
            raise DraftNotFoundError(draft_id)
        self._require_owner(draft, account_id)
        if draft.snapshot.status is DraftStatus.EXPIRED or (
            draft.snapshot.status in {DraftStatus.EDITING, DraftStatus.PROCESSING, DraftStatus.NEEDS_ATTENTION}
            and draft.expires_at <= self._now()
        ):
            raise DraftStateConflictError(DraftStatus.EXPIRED.value, operation="access")
        return draft

    async def delete(self, draft_id: UUID, account_id: int) -> None:
        """Delete an owned draft only while it remains user-editable.

        Raises:
            DraftNotFoundError: If no draft has this ID.
            DraftAccessDeniedError: If the account does not own it.
            DraftStateConflictError: If the draft has expired or is no longer editable.
        """
        current = await self.get_owned(draft_id, account_id)
        if current.snapshot.status not in {DraftStatus.EDITING, DraftStatus.NEEDS_ATTENTION}:
            raise DraftStateConflictError(current.snapshot.status.value, operation="delete")
        if not await self._repository.delete_owned(draft_id, account_id):
            raise DraftNotFoundError(draft_id)

    async def expire_due(self, *, limit: int = 100, now: Instant | None = None) -> int:
        """Expire one bounded batch using the same authoritative service clock.

        Raises:
            InvalidStateError: If `limit` is outside 1..1000.
        """
        if not 1 <= limit <= 1_000:
            msg = tr(t"draft expiry limit must be between 1 and 1000")
            raise InvalidStateError(msg)
        return await self._repository.expire_due(now=now or self._now(), limit=limit)

    async def apply_change(
        self,
        draft_id: UUID,
        account_id: int,
        change: DraftChange,
        *,
        locale: str | None,
        now: Instant | None = None,
    ) -> AppliedDraftChange:
        """Validate field IDs and types, then atomically persist one optimistic edit.

        Required fields may still be missing here; completeness is only enforced at finalization.

        Raises:
            DraftNotFoundError: If no draft has this ID.
            DraftAccessDeniedError: If the account does not own it.
            DraftStateConflictError: If the draft has expired.
            DraftSchemaUnsupportedError: If the pinned schema revision is no longer served.
            DraftIncompleteError: If a supplied value is unknown or invalid.
            DraftRevisionConflictError: If the change is based on a stale revision.
        """
        current = await self.get_owned(draft_id, account_id)
        replayed = await self._repository.replayed_change(draft_id, account_id, change.idempotency_key)
        if replayed is not None:
            return replayed
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
        touched_at = now or self._now()
        return await self._repository.apply_change(
            draft_id,
            account_id,
            change,
            updated_at=touched_at,
            expires_at=touched_at.add(days=self._retention_days, days_assumed_24h_ok=True),
        )

    async def validate_for_finalization(
        self,
        draft_id: UUID,
        account_id: int,
        *,
        locale: str | None,
    ) -> ValidatedDraft:
        """Check the draft against its pinned manifest, defaults applied, without touching artifacts.

        Artifact readiness and the state change belong to the finalization service.

        Raises:
            DraftNotFoundError: If no draft has this ID.
            DraftAccessDeniedError: If the account does not own it.
            DraftStateConflictError: If the draft has expired.
            DraftSchemaUnsupportedError: If the pinned schema revision is no longer served.
            DraftIncompleteError: If any required value is missing or invalid.
        """
        current = await self.get_owned(draft_id, account_id)
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
        return ValidatedDraft(current, normalized)

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
