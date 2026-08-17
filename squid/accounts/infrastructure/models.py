"""SQLAlchemy account models, keyed by account rather than by identity provider."""

import uuid

from sqlalchemy import (
    UUID,
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    event,
    func,
    text,
)
from sqlalchemy.engine.default import DefaultExecutionContext
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm.attributes import get_history
from whenever import Instant

from squid.accounts.domain import ClaimMethod, ClaimStatus, IdentityProvider, fold_creator_name
from squid.persistence.base import Base
from squid.persistence.types import InstantUTC

_PROVIDER_VALUES = ", ".join(f"'{provider.value}'" for provider in IdentityProvider)
"""Generated from the enum so the CHECK cannot drift from the domain."""


def _fold_from_name(context: DefaultExecutionContext) -> str:
    """Derive `normalized_name` from the `name` being inserted.

    Attached to the column rather than left to callers so that no insert path can skip it:
    this fires for ORM flushes, Core `insert()`, `pg_insert(...).on_conflict_do_nothing`,
    and executemany alike.

    Insert only. A column-level `onupdate` would fire for every UPDATE of the row, including
    the claim updates that touch only `account_id`, where `name` is not among the parameters
    at all. `_refold_on_name_change` handles the update side precisely instead.
    """
    return fold_creator_name(context.get_current_parameters()["name"])


class Account(Base):
    """An internal caller independent of every external identity provider."""

    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("public_creator_id", name="accounts_public_creator_id_key"),
        CheckConstraint(
            "(consent_version IS NULL) = (consented_at IS NULL)",
            name="accounts_consent_receipt_complete",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    public_creator_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, server_default=text("gen_random_uuid()"), default_factory=uuid.uuid4
    )
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
    consent_version: Mapped[str | None] = mapped_column(Text, default=None)
    consented_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)


class AccountIdentity(Base):
    """A verified provider subject attached to exactly one account."""

    __tablename__ = "account_identities"
    __table_args__ = (
        UniqueConstraint("provider", "subject", name="account_identities_provider_subject_key"),
        # Membership only. `AccountIdentity.for_provider` is the authority on subject
        # *format*, so that adding a provider states its format in one exhaustive `match`
        # rather than in a SQL predicate a migration has to keep in step.
        CheckConstraint(f"provider IN ({_PROVIDER_VALUES})", name="account_identities_provider_check"),
        CheckConstraint("subject = btrim(subject) AND subject <> ''", name="account_identities_subject_check"),
        Index("account_identities_account_provider_idx", "account_id", "provider"),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    account_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("accounts.id", name="account_identities_account_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[IdentityProvider] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text, default=None)
    verified_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )


class PublicCreatorRedirect(Base):
    """Permanent redirect from a merged public creator identifier."""

    __tablename__ = "public_creator_redirects"
    __table_args__ = (Index("public_creator_redirects_target_idx", "target_account_id"),)
    retired_public_creator_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    target_account_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("accounts.id", name="public_creator_redirects_target_account_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )


class CreatorAlias(Base):
    """A creator name credited on a build, optionally claimed by an account."""

    __tablename__ = "creator_aliases"
    __table_args__ = (
        Index("creator_aliases_account_idx", "account_id"),
        UniqueConstraint("normalized_name", name="creator_aliases_normalized_name_key"),
        Index(
            # The unique constraint above serves equality only; a creator typeahead needs a prefix
            # scan, which under a non-C collation requires an explicit `text_pattern_ops` index.
            "creator_aliases_normalized_name_prefix_idx",
            "normalized_name",
            postgresql_ops={"normalized_name": "text_pattern_ops"},
        ),
        CheckConstraint(
            # The application owns the folding (`fold_creator_name`), which Postgres cannot
            # reproduce. These two conditions hold for any casefold output, so they never
            # reject a legitimately folded name, but they do catch a raw SQL write that
            # stored the display spelling verbatim.
            "normalized_name = btrim(normalized_name) AND normalized_name !~ '[A-Z]'",
            name="creator_aliases_normalized_name_folded",
        ),
        CheckConstraint(
            "(account_id IS NULL) = (claimed_at IS NULL)",
            name="creator_aliases_claim_complete",
        ),
        CheckConstraint(
            "(account_id IS NULL) = (claim_method IS NULL)",
            name="creator_aliases_claim_method_complete",
        ),
        CheckConstraint(
            "claim_method IS NULL OR claim_method IN ('verified_ign', 'staff_approved', 'migrated')",
            name="creator_aliases_claim_method_check",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        insert_default=_fold_from_name,
        init=False,
    )
    account_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("accounts.id", name="creator_aliases_account_id_fkey", ondelete="SET NULL"),
        default=None,
    )
    claimed_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    claim_method: Mapped[ClaimMethod | None] = mapped_column(Text, default=None)
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )


@event.listens_for(CreatorAlias, "before_update")
def _refold_on_name_change(_mapper: object, _connection: object, target: CreatorAlias) -> None:
    """Recompute the fold when, and only when, a display spelling is corrected.

    The claim paths update `account_id` and friends without touching `name`, so this has to
    be conditional on the attribute actually being dirty rather than a blanket column
    `onupdate`.
    """
    if get_history(target, "name").has_changes():
        target.normalized_name = fold_creator_name(target.name)


class CreatorAliasClaim(Base):
    """An account's request to be credited under a creator alias."""

    __tablename__ = "creator_alias_claims"
    __table_args__ = (
        Index("creator_alias_claims_account_idx", "account_id"),
        Index("creator_alias_claims_resolved_by_idx", "resolved_by_account_id"),
        Index(
            "creator_alias_claims_one_pending_per_account",
            "alias_id",
            "account_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="creator_alias_claims_status_check",
        ),
        CheckConstraint(
            "(status = 'pending') = (resolved_at IS NULL)",
            name="creator_alias_claims_resolution_complete",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    alias_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("creator_aliases.id", name="creator_alias_claims_alias_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    account_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("accounts.id", name="creator_alias_claims_account_id_fkey", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[ClaimStatus] = mapped_column(Text, nullable=False, default=ClaimStatus.PENDING)
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
    resolved_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    resolved_by_account_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("accounts.id", name="creator_alias_claims_resolved_by_account_id_fkey", ondelete="SET NULL"),
        default=None,
    )


class VerificationCode(Base):
    """A verification code for linking Java Edition identities."""

    __tablename__ = "verification_codes"
    __table_args__ = (
        CheckConstraint(
            "(reserved_token IS NULL) = (reserved_until IS NULL)",
            name="verification_codes_reservation_complete",
        ),
    )

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, init=False)
    minecraft_uuid: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    username: Mapped[str] = mapped_column(Text, nullable=False, default="")
    valid: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    created: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
    expires: Mapped[Instant] = mapped_column(
        InstantUTC(),
        nullable=False,
        server_default=text("(now() + '00:10:00'::interval)"),
        default_factory=lambda: Instant.now().add(minutes=10),
    )
    reserved_token: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    """Digest of the token held by whoever is currently being shown this code's consent prompt.

    A digest rather than the token, for the same reason `code` is one. Deliberately says nothing
    about *who* reserved it: the prompt runs before an account exists, and the notice promises that
    cancelling stores no account information, so a reservation identifies nobody.
    """

    reserved_until: Mapped[Instant | None] = mapped_column(InstantUTC(), nullable=True, default=None)
    """When the hold lapses, freeing the code without anything having to reap it.

    A crashed process therefore costs one prompt's worth of delay rather than a stuck code, and the
    legitimate owner can always mint a fresh one from the game.
    """


class VerificationAttempt(Base):
    """Consecutive failed code redemptions for one external identity.

    Keyed on `(provider, subject)` rather than on an account, because the guesser may not have an
    account yet: a redemption is the first thing many callers ever do, and creating a row for
    someone in order to rate-limit them would defeat the point. No foreign key for the same reason.

    The counter is *consecutive*: a success clears it, so an honest user who mistypes twice and then
    gets it right is never closer to a lockout than someone who never failed.
    """

    __tablename__ = "verification_attempts"
    __table_args__ = (
        CheckConstraint(f"provider IN ({_PROVIDER_VALUES})", name="verification_attempts_provider_valid"),
        CheckConstraint("consecutive_failures >= 0", name="verification_attempts_failures_non_negative"),
    )

    provider: Mapped[IdentityProvider] = mapped_column(Text, primary_key=True)
    subject: Mapped[str] = mapped_column(Text, primary_key=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    locked_until: Mapped[Instant | None] = mapped_column(InstantUTC(), nullable=True, default=None)
    updated_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
