"""Application orchestration for account-owned revisioned drafts."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID, uuid4

from whenever import Instant

from squid.core.actors import Actor
from squid.core.errors import InvalidStateError, JSONValue, ValidationError
from squid.core.i18n import tr
from squid.messages.domain import MessageSnapshot
from squid.permissions.application.ports import PermissionUnitOfWork
from squid.permissions.domain import PermissionNode
from squid.permissions.domain.catalogue import BUILD_SUBMISSION_EDIT
from squid.submissions.application.forms import FormManifestRegistry
from squid.submissions.domain import (
    DraftChange,
    DraftSnapshot,
    DraftStatus,
    FormManifest,
    SubmissionOrigin,
)
from squid.submissions.domain.finalization import SubmissionAttentionIssue
from squid.submissions.domain.source_files import SubmissionSourceFile
from squid.submissions.errors import (
    DraftAccessDeniedError,
    DraftNotFoundError,
    DraftSchemaUnsupportedError,
    DraftStateConflictError,
    DraftValidationError,
)
from squid.unit_of_work import UnitOfWorkFactory
from squid_i18n import md_tr

DEFAULT_DRAFT_RETENTION_DAYS = 7
DEFAULT_ACCOUNT_DRAFT_CAPACITY = 10
DEFAULT_INFERRED_DRAFT_CAPACITY = 1_000
INGESTION_OPTIONAL_FIELDS = frozenset(
    {
        "creators",
        "capture_width",
        "capture_height",
        "capture_depth",
        "source_version",
        "opening_width",
        "opening_height",
        "opening_depth",
        "door_orientation",
        "movement_orientation",
        "extension_length",
    }
)

type DraftActor = int | Actor


def draft_actor_id(actor: DraftActor) -> int:
    """Return the account ID from an integer or permission subject; reject anonymous actors."""
    identifier = actor if isinstance(actor, int) else actor.account_id
    if identifier is None or identifier < 1:
        raise DraftAccessDeniedError
    return identifier


class DraftPermissions(Protocol):
    """Live permission checks used for staff correction of account-owned drafts."""

    async def allows_in(self, uow: PermissionUnitOfWork, subject: Actor, node: PermissionNode) -> bool: ...


@dataclass(frozen=True, slots=True)
class StoredDraft:
    """Draft answers together with expiry, source files, and preparation status."""

    snapshot: DraftSnapshot
    origin: SubmissionOrigin
    created_at: Instant
    updated_at: Instant
    expires_at: Instant
    source_installation_id: UUID | None = None
    preparation_issues: tuple[SubmissionAttentionIssue, ...] = ()
    preparation_retry_at: Instant | None = None
    inferred: bool = False
    submission_actor_account_id: int | None = None
    source_messages: tuple[MessageSnapshot, ...] = ()
    source_files: tuple[SubmissionSourceFile, ...] = ()
    source_issues: tuple[str, ...] = ()
    inference_run_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.origin is not SubmissionOrigin.PAPER and self.source_installation_id is not None:
            msg = tr(t"submissions-paper-may-retain = Only Paper drafts may retain an installation provenance ID.")
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
    """Answers checked against the draft's form revision, ready for artifact checks."""

    draft: StoredDraft
    normalized_answers: dict[str, JSONValue]


class DraftRepository(Protocol):
    """Store drafts and apply edits for SubmissionDraftService.

    An accepted edit replaces the answers and increments the draft revision
    together. Retrying its change key must not apply the edit twice, and a stale
    base revision must not overwrite a newer edit. The service checks access and
    form values; the repository enforces revision and replay checks when it writes.
    """

    async def list_attention(self, *, after: UUID | None, limit: int, now: Instant) -> tuple[StoredDraft, ...]: ...

    async def list_active_for_account(
        self,
        account_id: int,
        *,
        now: Instant,
        limit: int,
    ) -> tuple[StoredDraft, ...]: ...

    async def create(self, draft: StoredDraft, *, capacity: int = DEFAULT_ACCOUNT_DRAFT_CAPACITY) -> StoredDraft: ...

    async def get(self, draft_id: UUID) -> StoredDraft | None: ...

    async def replayed_change(
        self,
        draft_id: UUID,
        account_id: int,
        change: DraftChange,
        *,
        actor_account_id: int | None = None,
    ) -> AppliedDraftChange | None: ...

    async def apply_change(
        self,
        draft_id: UUID,
        account_id: int,
        change: DraftChange,
        *,
        updated_at: Instant,
        expires_at: Instant,
        actor_account_id: int | None = None,
    ) -> AppliedDraftChange: ...

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
    """Resolve staff-adjusted draft capacity for an account."""

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
            msg = tr(
                t"submissions-submission-form-revisions = "
                t"submission form revisions must be upgraded one checked-in revision at a time"
            )
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
    """Use the same configured draft capacity for every account."""

    def __init__(self, limit: int = DEFAULT_ACCOUNT_DRAFT_CAPACITY) -> None:
        if limit < 1:
            msg = tr(t"submissions-draft-capacity-positive = draft capacity must be positive")
            raise InvalidStateError(msg)
        self._limit = limit

    async def limit_for(self, account_id: int) -> int:
        """Return the configured capacity; account ID is accepted for port compatibility."""
        del account_id
        return self._limit


class SubmissionDraftService:
    """Let clients create, resume, and edit account-owned submission drafts.

    A new draft uses the current form manifest. Edits are checked against that
    same manifest revision until the owner explicitly upgrades it, allowing a
    draft to be resumed even after the current form changes. Incomplete answers
    are allowed while editing; validate_for_finalization checks required fields.

    Access checks allow the owner or staff with submission-edit permission.
    Writes use the draft repository's revision and replay checks, and this service
    commits them. SubmissionFinalizationService handles the later step of turning
    a completed draft into a queued build submission.
    """

    def __init__(
        self,
        manifests: FormManifestRegistry,
        capacity: AccountDraftCapacity | None = None,
        revision_migration: FormRevisionMigration | None = None,
        *,
        uow_factory: UnitOfWorkFactory[DraftUnitOfWork],
        retention_days: int = DEFAULT_DRAFT_RETENTION_DAYS,
        now: Callable[[], Instant] = Instant.now,
        inferred_capacity: int = DEFAULT_INFERRED_DRAFT_CAPACITY,
        permissions: DraftPermissions | None = None,
    ) -> None:
        if retention_days < 1 or inferred_capacity < 1:
            msg = tr(t"submissions-draft-retention-positive = draft retention must be positive")
            raise InvalidStateError(msg)
        self._uow_factory = uow_factory
        self._manifests = manifests
        self._capacity = capacity or FixedAccountDraftCapacity()
        self._revision_migration = revision_migration or CheckedInFormRevisionMigration()
        self._retention_days = retention_days
        self._now = now
        self._inferred_capacity = inferred_capacity
        self._permissions = permissions

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
        inferred: bool = False,
        source_messages: tuple[MessageSnapshot, ...] = (),
        source_files: tuple[SubmissionSourceFile, ...] = (),
        source_issues: tuple[str, ...] = (),
        inference_run_id: UUID | None = None,
    ) -> StoredDraft:
        """Create an empty draft pinned to the current schema revision."""
        if (origin is SubmissionOrigin.PAPER) != (source_installation_id is not None):
            msg = tr(
                t"submissions-paper-installation-provenance-required "
                t"= Paper drafts require server-derived installation provenance."
            )
            raise ValidationError(msg)
        if inferred and origin is not SubmissionOrigin.DISCORD:
            message = "Inferred drafts require Discord source provenance."
            raise ValidationError(message)
        limit = self._inferred_capacity if inferred else await self._capacity.limit_for(owner_account_id)
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
            inferred=inferred,
            source_messages=source_messages,
            source_files=source_files,
            source_issues=source_issues,
            inference_run_id=inference_run_id,
        )
        async with self._uow_factory() as uow:
            result = await uow.submission_drafts.create(stored, capacity=limit)
            await uow.commit()
            return result

    async def list_active(self, account_id: int, *, limit: int = 10) -> tuple[StoredDraft, ...]:
        """List a bounded newest-first view of one account's unexpired active drafts."""
        async with self._uow_factory() as uow:
            if not 1 <= limit <= DEFAULT_ACCOUNT_DRAFT_CAPACITY:
                maximum = DEFAULT_ACCOUNT_DRAFT_CAPACITY
                raise InvalidStateError(
                    md_tr(t"submissions-draft-discovery-limit = draft discovery limit must be between 1 and {maximum}")
                )
            return await uow.submission_drafts.list_active_for_account(account_id, now=self._now(), limit=limit)

    async def attention_inbox(
        self,
        actor: DraftActor,
        *,
        after: UUID | None = None,
        limit: int = 20,
    ) -> tuple[StoredDraft, ...]:
        """Page correction work by stable ID after checking current staff permission."""
        async with self._uow_factory() as uow:
            draft_actor_id(actor)
            subject = Actor(account_id=actor) if isinstance(actor, int) else actor
            if self._permissions is None or not await self._permissions.allows_in(uow, subject, BUILD_SUBMISSION_EDIT):
                raise DraftAccessDeniedError
            if not 1 <= limit <= 100:
                message = "Draft inbox limit must be between 1 and 100."
                raise InvalidStateError(message)
            return await uow.submission_drafts.list_attention(after=after, limit=limit, now=self._now())

    async def get_owned(self, draft_id: UUID, account_id: int) -> StoredDraft:
        """Return one unexpired draft under strict owner authority."""
        draft = await self.get_accessible(draft_id, account_id)
        self._require_owner(draft, account_id)
        return draft

    async def get_accessible(self, draft_id: UUID, actor: DraftActor) -> StoredDraft:
        """Return one unexpired draft after checking owner or current staff authority."""
        async with self._uow_factory() as uow:
            return await self.get_accessible_in(uow, draft_id, actor)

    async def get_accessible_in(self, uow: DraftUnitOfWork, draft_id: UUID, actor: DraftActor) -> StoredDraft:
        """Check draft access within the caller's read or write operation."""
        draft = await uow.submission_drafts.get(draft_id)
        if draft is None:
            raise DraftNotFoundError(draft_id)
        account_id = draft_actor_id(actor)
        if draft.snapshot.owner_account_id != account_id:
            subject = Actor(account_id=actor) if isinstance(actor, int) else actor
            if self._permissions is None or not await self._permissions.allows_in(uow, subject, BUILD_SUBMISSION_EDIT):
                raise DraftAccessDeniedError
        if draft.snapshot.status is DraftStatus.EXPIRED or (
            draft.snapshot.status in {DraftStatus.EDITING, DraftStatus.PROCESSING, DraftStatus.NEEDS_ATTENTION}
            and draft.expires_at <= self._now()
        ):
            raise DraftStateConflictError(DraftStatus.EXPIRED.value, operation="access")
        return draft

    async def delete(self, draft_id: UUID, account_id: int) -> None:
        """Delete an owned draft only while it remains user-editable."""
        async with self._uow_factory() as uow:
            current = await self.get_accessible_in(uow, draft_id, account_id)
            self._require_owner(current, account_id)
            if current.snapshot.status not in {DraftStatus.EDITING, DraftStatus.NEEDS_ATTENTION}:
                raise DraftStateConflictError(current.snapshot.status.value, operation="delete")
            if not await uow.submission_drafts.delete_owned(draft_id, account_id):
                raise DraftNotFoundError(draft_id)
            await uow.commit()

    async def expire_due(self, *, limit: int = 100, now: Instant | None = None) -> int:
        """Expire one bounded batch using the same authoritative service clock."""
        async with self._uow_factory() as uow:
            if not 1 <= limit <= 1_000:
                msg = tr(t"submissions-draft-expiry-limit = draft expiry limit must be between 1 and 1000")
                raise InvalidStateError(msg)
            result = await uow.submission_drafts.expire_due(now=now or self._now(), limit=limit)
            await uow.commit()
            return result

    async def apply_change(
        self,
        draft_id: UUID,
        account_id: DraftActor,
        change: DraftChange,
        *,
        locale: str | None,
        now: Instant | None = None,
    ) -> AppliedDraftChange:
        """Validate and save an edit against the draft's expected revision.

        A previously accepted idempotency key returns the current draft snapshot
        without applying the change again. New edits are checked against the pinned
        manifest, then permissions and revision are checked again before commit.
        """
        async with self._uow_factory() as uow:
            current = await self.get_accessible_in(uow, draft_id, account_id)
            owner_id = current.snapshot.owner_account_id
            replayed = await uow.submission_drafts.replayed_change(
                draft_id, owner_id, change, actor_account_id=draft_actor_id(account_id)
            )
            if replayed is not None:
                return replayed
        await self.validate_change(current, change, locale=locale)
        async with self._uow_factory() as uow:
            result = await self.persist_change_in(uow, current, account_id, change, now=now)
            await uow.commit()
            return result

    async def validate_change(self, current: StoredDraft, change: DraftChange, *, locale: str | None) -> DraftSnapshot:
        """Validate a candidate against its pinned manifest without writing."""
        manifest = await self._pinned_manifest(current, locale)
        candidate = current.snapshot.apply(change)
        errors = manifest.validate_answers(
            candidate.category, candidate.answers, origin=current.origin, require_complete=False
        )
        if errors:
            raise DraftValidationError(errors)
        return candidate

    async def persist_change_in(
        self,
        uow: DraftUnitOfWork,
        current: StoredDraft,
        actor: DraftActor,
        change: DraftChange,
        *,
        now: Instant | None = None,
    ) -> AppliedDraftChange:
        """Persist a validated change with current authority and repository revision checks.

        The transaction owner commits this together with any operation outcome.
        """
        await self.get_accessible_in(uow, current.snapshot.id, actor)
        touched_at = now or self._now()
        return await uow.submission_drafts.apply_change(
            current.snapshot.id,
            current.snapshot.owner_account_id,
            change,
            updated_at=touched_at,
            expires_at=touched_at.add(days=self._retention_days, days_assumed_24h_ok=True),
            actor_account_id=draft_actor_id(actor),
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
        """Upgrade to the next form revision without dropping answered fields.

        If the draft already uses target_revision, return it as a replay before
        checking its editability or expected revision.
        """
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
        async with self._uow_factory() as uow:
            latest = await self.get_accessible_in(uow, draft_id, account_id)
            self._require_owner(latest, account_id)
            result = await uow.submission_drafts.upgrade_manifest(
                draft_id,
                account_id,
                expected_revision=expected_revision,
                target_schema_revision=target_revision,
                answers=answers,
                updated_at=touched_at,
                expires_at=touched_at.add(days=self._retention_days, days_assumed_24h_ok=True),
            )
            await uow.commit()
            return result

    async def validate_for_finalization(
        self,
        draft_id: UUID,
        account_id: DraftActor,
        *,
        locale: str | None,
    ) -> ValidatedDraft:
        """Validate a pinned manifest without trusting client artifact assertions.

        The finalization service performs backend-owned artifact checks and atomically
        changes the draft state while enqueuing its durable job.
        """
        current = await self.get_accessible(draft_id, account_id)
        manifest = await self._pinned_manifest(current, locale)
        # Ingestion provenance is set by the server, not by the form's origin or
        # AI checkbox. Normal Discord submissions retain the pinned requirements.
        ingested = (
            current.origin is SubmissionOrigin.DISCORD and current.inferred and current.inference_run_id is not None
        )
        answers = dict(current.snapshot.answers)
        if ingested:
            answers = {
                key: value
                for key, value in answers.items()
                if key not in INGESTION_OPTIONAL_FIELDS or value not in (None, "", [])
            }
        errors = manifest.validate_answers(
            current.snapshot.category,
            answers,
            origin=current.origin,
        )
        if ingested:
            errors = {
                key: reason
                for key, reason in errors.items()
                if key not in INGESTION_OPTIONAL_FIELDS or reason != "required"
            }
        if errors:
            raise DraftValidationError(errors)
        normalized = manifest.visible_answers(
            current.snapshot.category,
            answers,
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


class DraftUnitOfWork(PermissionUnitOfWork, Protocol):
    @property
    def submission_drafts(self) -> DraftRepository: ...
