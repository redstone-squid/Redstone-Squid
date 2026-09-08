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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine.default import DefaultExecutionContext
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm.attributes import get_history
from whenever import Instant

from squid.accounts.domain import (
    MERGE_TICKET_TTL_SECONDS,
    ClaimMethod,
    ClaimStatus,
    IdentityProvider,
    fold_creator_name,
)
from squid.persistence.base import Base
from squid.persistence.types import InstantUTC, now

_PROVIDER_VALUES = ", ".join(f"'{provider.value}'" for provider in IdentityProvider)
"""Generated from the enum so the CHECK cannot drift from the domain."""


def _fold_from_name(context: DefaultExecutionContext) -> str:
    """Derive `normalized_name` from the `name` being inserted.

    On the column so no insert path can skip it: ORM flushes, Core `insert()`,
    `on_conflict_do_nothing` and executemany alike. Insert only, because an `onupdate` would also
    fire for claim updates that never pass `name`; `_refold_on_name_change` covers updates.
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
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
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
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    """Whether this identity appears on the account's public creator profile."""

    avatar_key: Mapped[str | None] = mapped_column(Text, default=None)
    """Provider rendering key, where the subject alone is not enough to build an avatar URL.

    Discord avatar URLs need the hash, which only the gateway supplies. Java heads derive from
    the UUID, so this stays NULL there.
    """


class AccountProfile(Base):
    """What an account chooses to publish about itself on its creator page.

    A child of `accounts` rather than more columns on it: the account row is the identity anchor
    that link and merge paths lock `FOR UPDATE`, and every identity read would otherwise pay for a
    bio.
    """

    __tablename__ = "account_profiles"
    __table_args__ = (
        # Length and shape only. The application owns normalization (NFKC-fold, trim, control
        # character rejection) for the same reason `creator_aliases.normalized_name` does, so
        # these catch a hand-written SQL insert rather than defining the value.
        CheckConstraint(
            "display_name IS NULL OR char_length(display_name) BETWEEN 1 AND 64",
            name="account_profiles_display_name_length",
        ),
        CheckConstraint(
            "display_name IS NULL OR display_name = btrim(display_name)",
            name="account_profiles_display_name_trimmed",
        ),
        CheckConstraint("bio IS NULL OR char_length(bio) BETWEEN 1 AND 500", name="account_profiles_bio_length"),
        CheckConstraint(
            "pronouns IS NULL OR char_length(pronouns) BETWEEN 1 AND 40",
            name="account_profiles_pronouns_length",
        ),
        CheckConstraint(
            "jsonb_typeof(links) = 'array' AND jsonb_array_length(links) <= 10",
            name="account_profiles_links_shape",
        ),
        # An unindexed foreign key makes deleting an identity scan this table; the same defect
        # class was closed across the schema in b9d3e6a1f8c5.
        Index("account_profiles_avatar_identity_idx", "avatar_identity_id"),
    )
    account_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("accounts.id", name="account_profiles_account_id_fkey", ondelete="CASCADE"),
        primary_key=True,
    )
    display_name: Mapped[str | None] = mapped_column(Text, default=None)
    """Presentation only, deliberately not a `creator_aliases` name: renaming yourself here moves
    no build credit and needs no staff review."""

    bio: Mapped[str | None] = mapped_column(Text, default=None)
    pronouns: Mapped[str | None] = mapped_column(Text, default=None)
    links: Mapped[list[dict[str, str]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"), default_factory=list
    )
    """External links as `[{"label": ..., "url": ...}]`.

    JSONB rather than a child table: nothing queries links, and every write replaces the whole
    list from one owner, so a table would buy referential integrity to nothing and cost a join on
    the hottest public read.
    """

    hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    """Whether to withhold the profile. A hidden profile still serves its aliases and build
    credits, because a creator page that vanished would strand every build crediting it."""

    avatar_identity_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("account_identities.id", name="account_profiles_avatar_identity_id_fkey", ondelete="SET NULL"),
        default=None,
    )
    """The linked identity this profile's avatar is rendered from.

    `SET NULL` so unlinking that identity clears the avatar. That the identity belongs to this same
    account is checked in the repository: the composite foreign key enforcing it cannot coexist with
    `ON DELETE SET NULL`.
    """
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    updated_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
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
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
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
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )


@event.listens_for(CreatorAlias, "before_update")
def _refold_on_name_change(_mapper: object, _connection: object, target: CreatorAlias) -> None:
    """Recompute the fold when, and only when, a display spelling is corrected.

    Conditional on `name` being dirty, because claim updates touch `account_id` and leave the
    spelling alone.
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
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
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
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    expires: Mapped[Instant] = mapped_column(
        InstantUTC(),
        nullable=False,
        server_default=text("(now() + '00:10:00'::interval)"),
        default_factory=lambda: Instant.now().add(minutes=10),
    )
    reserved_token: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    """Digest of the token held by whoever is being shown this code's consent prompt.

    Says nothing about who reserved it: the prompt runs before an account exists, and the notice
    promises that cancelling stores no account information.
    """

    reserved_until: Mapped[Instant | None] = mapped_column(InstantUTC(), nullable=True, default=None)
    """When the hold lapses, freeing the code without anything having to reap it.

    A crashed prompt therefore costs one hold's delay rather than a permanently stuck code.
    """


class VerificationAttempt(Base):
    """Consecutive failed code redemptions for one external identity.

    Keyed on `(provider, subject)` with no foreign key, because the guesser may not have an account
    yet. The counter is consecutive: a success clears it.
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
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )


class AccountMergeTicket(Base):
    """A live, single-use claim that one account consents to being absorbed by another.

    A merge needs recent proof of both accounts and no session holds both, so the ticket carries the
    absorbed side's: minting one is that side authenticating, redeeming it is the survivor's.

    Keyed on the account rather than the digest, so minting replaces. One live ticket per account is
    most of why an eight-character code is enough.
    """

    __tablename__ = "account_merge_tickets"
    __table_args__ = (
        CheckConstraint("expires_at > created_at", name="account_merge_tickets_expiry_after_creation"),
        Index("account_merge_tickets_code_digest_idx", "code_digest"),
    )

    account_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("accounts.id", name="account_merge_tickets_account_id_fkey", ondelete="CASCADE"),
        primary_key=True,
    )
    code_digest: Mapped[str] = mapped_column(Text, nullable=False)
    """Digest, never the code: the plaintext is shown once at mint time and never stored."""

    expires_at: Mapped[Instant] = mapped_column(
        InstantUTC(),
        nullable=False,
        default_factory=lambda: Instant.now().add(seconds=MERGE_TICKET_TTL_SECONDS),
    )
    """The TTL equals `MERGE_PROOF_MAX_AGE_SECONDS`, so a ticket stays redeemable for exactly as long
    as `RecentAccountProof` accepts the `created_at` it stands for."""

    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
