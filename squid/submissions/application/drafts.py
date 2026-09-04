"""Application orchestration for account-owned revisioned drafts."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID, uuid4

from whenever import Instant

from squid.core.errors import InvalidStateError, JSONValue, ValidationError
from squid.core.i18n import tr
from squid.submissions.application.forms import FormManifestRegistry
from squid.submissions.domain import (
    DraftChange,
    DraftChangeKey,
    DraftSnapshot,
    DraftStatus,
    FormManifest,
    SubmissionOrigin,
)
from squid.submissions.errors import (
    DraftAccessDeniedError,
    DraftCapacityExceededError,
    DraftNotFoundError,
    DraftSchemaUnsupportedError,
    DraftStateConflictError,
    DraftValidationError,
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
class AppliedDraftUpgrade:
    """Result of moving a draft to a newer checked-in manifest revision."""

    draft: StoredDraft
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class ValidatedDraft:
    """A complete pinned draft prepared for server-owned finalization checks."""

    draft: StoredDraft
    normalized_answers: dict[str, JSONValue]


class DraftRepository(Protocol):
    """Atomic persistence required by the draft application service."""

    async def count_active_for_account(self, account_id: int) -> int: ...

    async def list_active_for_account(
        self,
        account_id: int,
        *,
        now: Instant,
        limit: int,
    ) -> tuple[StoredDraft, ...]: ...

    async def create(self, draft: StoredDraft) -> StoredDraft: ...

    async def get(self, draft_id: UUID) -> StoredDraft | None: ...

    async def replayed_change(
        self,
        draft_id: UUID,
        account_id: int,
        idempotency_key: DraftChangeKey,
    ) -> AppliedDraftChange | None: ...

    async def apply_change(
        self,
        draft_id: UUID,
        account_id: int,
        change: DraftChange,
        *,
        updated_at: Instant,
        expires_at: Instant,
    ) -> AppliedDraftChange: ...

    async def transition(
        self,
        draft_id: UUID,
        account_id: int,
        *,
        expected_revision: int,
        status: DraftStatus,
        updated_at: Instant,
        expires_at: Instant,
    ) -> StoredDraft: ...

    async def upgrade_manifest(
        self,
        draft_id: UUID,
        account_id: int,
        *,
        expected_revision: int,
        target_schema_revision: int,
        answers: Mapping[str, JSONValue],
        updated_at: Instant,
        expires_at: Instant,
    ) -> AppliedDraftUpgrade: ...

    async def delete_owned(self, draft_id: UUID, account_id: int) -> bool: ...

    async def expire_due(self, *, now: Instant, limit: int = 100) -> int: ...


class AccountDraftCapacity(Protocol):
    """Resolve staff-adjusted synchronized-draft capacity for an account."""

    async def limit_for(self, account_id: int) -> int: ...


class FormRevisionMigration(Protocol):
    """Transform answers between two immutable manifests without persistence authority."""

    def migrate(
        self,
        draft: StoredDraft,
        source: FormManifest,
        target: FormManifest,
    ) -> Mapping[str, JSONValue]: ...


class CheckedInFormRevisionMigration:
    """Migrate compatible checked-in revisions while preserving answer field IDs."""

    def migrate(
        self,
        draft: StoredDraft,
        source: FormManifest,
        target: FormManifest,
    ) -> Mapping[str, JSONValue]:
        if source.schema_id != target.schema_id or target.revision != source.revision + 1:
            msg = tr(t"submission form revisions must be upgraded one checked-in revision at a time")
            raise ValidationError(msg)
        source_fields = {field.id for field in source.fields_for(draft.snapshot.category)}
        target_fields = {field.id for field in target.fields_for(draft.snapshot.category)}
        removed_answers = draft.snapshot.answers.keys() & (source_fields - target_fields)
        if removed_answers:
            errors = {field_id: "removed_field" for field_id in sorted(removed_answers)}
            raise DraftValidationError(errors)
        errors = target.validate_answers(
            draft.snapshot.category,
            draft.snapshot.answers,
            origin=draft.origin,
            require_complete=False,
        )
        if errors:
            raise DraftValidationError(errors)
        return dict(draft.snapshot.answers)


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
        revision_migration: FormRevisionMigration | None = None,
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
        self._revision_migration = revision_migration or CheckedInFormRevisionMigration()
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
        """Create an empty synchronized draft pinned to the current schema revision."""
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
        """List a bounded newest-first view of one account's unexpired active drafts."""
        if not 1 <= limit <= DEFAULT_ACCOUNT_DRAFT_CAPACITY:
            maximum = DEFAULT_ACCOUNT_DRAFT_CAPACITY
            raise InvalidStateError(tr(t"draft discovery limit must be between 1 and {maximum}"))
        return await self._repository.list_active_for_account(account_id, now=self._now(), limit=limit)

    async def get_owned(self, draft_id: UUID, account_id: int) -> StoredDraft:
        """Return one draft after enforcing its v1 single-owner boundary."""
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
        """Delete an owned draft only while it remains user-editable."""
        current = await self.get_owned(draft_id, account_id)
        if current.snapshot.status not in {DraftStatus.EDITING, DraftStatus.NEEDS_ATTENTION}:
            raise DraftStateConflictError(current.snapshot.status.value, operation="delete")
        if not await self._repository.delete_owned(draft_id, account_id):
            raise DraftNotFoundError(draft_id)

    async def expire_due(self, *, limit: int = 100, now: Instant | None = None) -> int:
        """Expire one bounded batch using the same authoritative service clock."""
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
        """Validate field IDs/types and atomically persist one optimistic edit."""
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
            raise DraftValidationError(errors)
        touched_at = now or self._now()
        return await self._repository.apply_change(
            draft_id,
            account_id,
            change,
            updated_at=touched_at,
            expires_at=touched_at.add(days=self._retention_days, days_assumed_24h_ok=True),
        )

    async def upgrade_manifest(
        self,
        draft_id: UUID,
        account_id: int,
        *,
        expected_revision: int,
        target_revision: int,
        locale: str | None,
        now: Instant | None = None,
    ) -> AppliedDraftUpgrade:
        """Move an editable draft to the next checked-in manifest revision."""
        current = await self.get_owned(draft_id, account_id)
        if current.snapshot.schema_revision == target_revision:
            return AppliedDraftUpgrade(current, replayed=True)
        if current.snapshot.status not in {DraftStatus.EDITING, DraftStatus.NEEDS_ATTENTION}:
            raise DraftStateConflictError(current.snapshot.status.value, operation="upgrade_manifest")
        source = await self._pinned_manifest(current, locale)
        target = await self._manifests.get(current.snapshot.schema_id, target_revision, locale=locale)
        if target is None:
            raise DraftSchemaUnsupportedError((f"{current.snapshot.schema_id}@{target_revision}",))
        answers = self._revision_migration.migrate(current, source, target)
        touched_at = now or self._now()
        return await self._repository.upgrade_manifest(
            draft_id,
            account_id,
            expected_revision=expected_revision,
            target_schema_revision=target_revision,
            answers=answers,
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
        """Validate a pinned manifest without trusting client artifact assertions.

        The finalization service performs backend-owned artifact checks and atomically
        changes the draft state while enqueuing its durable job.
        """
        current = await self.get_owned(draft_id, account_id)
        manifest = await self._pinned_manifest(current, locale)
        errors = manifest.validate_answers(
            current.snapshot.category,
            current.snapshot.answers,
            origin=current.origin,
        )
        if errors:
            raise DraftValidationError(errors)
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
