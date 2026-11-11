"""Store Squid accounts, their external identities, and the names credited on builds.

AccountIdentity links a provider subject to one Account; AccountProfile stores
that account's editable public presentation. CreatorAlias separately associates
a credited name with an account, and AliasClaim records requests for staff review
when that association needs approval. Changing profile text does not move credit.

VerificationCode rows support Java identity linking. MergeTicket rows authorize
combining existing accounts; PublicCreatorRedirect preserves the absorbed
account's public creator ID after the merge.
"""

import uuid
from typing import override

from sqlalchemy import (
    UUID,
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    event,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Dialect
from sqlalchemy.engine.default import DefaultExecutionContext
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm.attributes import get_history
from sqlalchemy.types import TypeDecorator
from whenever import Instant

from squid.accounts.domain import (
    MERGE_TICKET_TTL_SECONDS,
    ClaimMethod,
    ClaimStatus,
    IdentityProvider,
    fold_creator_name,
)
from squid.accounts.domain.consent import AccountConsent, consent_refresh_required
from squid.accounts.domain.models import _POSITIVE_DECIMAL, _identity_order
from squid.accounts.domain.profiles import ProfileLink
from squid.core.errors import ValidationError
from squid.core.i18n import tr
from squid.persistence.base import Base
from squid.persistence.revisions import Revisioned
from squid.persistence.types import InstantUTC, StrEnumText

_PROVIDER_VALUES = ", ".join(f"'{provider.value}'" for provider in IdentityProvider)
"""Generated from the enum so the CHECK cannot drift from the domain."""


def _fold_from_name(context: DefaultExecutionContext) -> str:
    """Derive the normalized alias for ORM and Core inserts."""
    return fold_creator_name(context.get_current_parameters()["name"])


class ProfileLinks(TypeDecorator[tuple[ProfileLink, ...]]):
    """Store profile links as the existing JSON array."""

    impl = JSONB
    cache_ok = True

    @override
    def process_bind_param(
        self, value: tuple[ProfileLink, ...] | None, dialect: Dialect
    ) -> list[dict[str, str]] | None:
        return None if value is None else [{"label": link.label, "url": link.url} for link in value]

    @override
    def process_result_value(
        self, value: list[dict[str, str]] | None, dialect: Dialect
    ) -> tuple[ProfileLink, ...] | None:
        return None if value is None else tuple(ProfileLink(**link) for link in value)


class Account(Revisioned, Base, kw_only=True):
    """An internal caller independent of every external identity provider."""

    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("public_creator_id", name="accounts_public_creator_id_key"),
        CheckConstraint("revision > 0", name="accounts_revision_positive"),
        CheckConstraint(
            "(consent_version IS NULL) = (consented_at IS NULL)",
            name="accounts_consent_receipt_complete",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True, default=None)
    """Internal account identifier; assigned when the account is first saved."""
    public_creator_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, server_default=text("gen_random_uuid()"), default_factory=uuid.uuid4
    )
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
    consent_version: Mapped[str | None] = mapped_column(Text, default=None)
    consented_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)

    identities: tuple[AccountIdentity, ...] = ()
    """Linked identities, populated together by the repository."""

    consent: AccountConsent | None = None

    def identities_for(self, provider: IdentityProvider) -> tuple[AccountIdentity, ...]:
        """Return every identity for *provider* in persistence order."""
        return tuple(identity for identity in self.identities if identity.provider is provider)

    def identity_for(self, provider: IdentityProvider, subject: str) -> AccountIdentity | None:
        """Return the identity for one exact canonical provider subject, if present."""
        return next((identity for identity in self.identities_for(provider) if identity.subject == subject), None)

    def most_recent_identity_for(self, provider: IdentityProvider) -> AccountIdentity | None:
        """Return the most recently verified identity for *provider*, breaking ties by persistence order."""
        selected: AccountIdentity | None = None
        for identity in self.identities_for(provider):
            if selected is None:
                selected = identity
                continue
            if selected.verified_at is None:
                if identity.verified_at is not None or _identity_order(identity) > _identity_order(selected):
                    selected = identity
                continue
            if identity.verified_at is not None and (
                identity.verified_at > selected.verified_at
                or (
                    identity.verified_at == selected.verified_at
                    and _identity_order(identity) > _identity_order(selected)
                )
            ):
                selected = identity
        return selected

    @property
    def needs_consent_refresh(self) -> bool:
        """Whether the current privacy notice must be accepted before storing more identity data."""
        return consent_refresh_required(
            self.created_at,
            None if self.consent is None else self.consent.version,
        )


class AccountIdentity(Base, kw_only=True):
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
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, default=None)
    account_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("accounts.id", name="account_identities_account_id_fkey", ondelete="CASCADE"),
        nullable=False,
        default=None,
    )
    provider: Mapped[IdentityProvider] = mapped_column(StrEnumText(IdentityProvider), nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text, default=None)
    verified_at: Mapped[Instant | None] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    """Whether this identity appears on the account's public creator profile."""

    avatar_key: Mapped[str | None] = mapped_column(Text, default=None)
    """Provider avatar key, such as a Discord avatar hash. Java avatars use the subject UUID."""

    @classmethod
    def for_provider(
        cls,
        provider: IdentityProvider,
        subject: str,
        *,
        display_name: str | None = None,
        verified_at: Instant | None = None,
    ) -> AccountIdentity:
        """Build an identity with a validated provider subject.

        Discord subjects are positive signed 64-bit decimal IDs; Bedrock subjects are
        positive unsigned 64-bit XUIDs. Neither accepts leading zeroes. Java subjects
        are parsed as UUIDs and stored in lowercase, hyphenated form. The database
        checks provider membership and basic whitespace, not these full formats.
        """
        match provider:
            case IdentityProvider.DISCORD:
                if _POSITIVE_DECIMAL.fullmatch(subject) is None or int(subject) >= 2**63:
                    raise ValidationError(
                        tr(
                            t"accounts-discord-identity-subjects = Discord "
                            t"identity subjects must be positive signed 64-bit integers, got {(subject_text := repr(subject))}."
                        )
                    )
                return cls(provider=provider, subject=subject, display_name=display_name, verified_at=verified_at)
            case IdentityProvider.BEDROCK:
                if _POSITIVE_DECIMAL.fullmatch(subject) is None or int(subject) >= 2**64:
                    raise ValidationError(
                        tr(
                            t"accounts-bedrock-xuids-unsigned = Bedrock XUIDs must "
                            t"be unsigned 64-bit integers, got {(subject_text := repr(subject))}."
                        )
                    )
                return cls(provider=provider, subject=subject, display_name=display_name, verified_at=verified_at)
            case IdentityProvider.JAVA:
                # `UUID` also lowercases and hyphenates, so an uppercase or bare-hex
                # subject from an external API normalizes rather than being rejected.
                try:
                    canonical = str(uuid.UUID(subject))
                except ValueError as error:
                    raise ValidationError(
                        tr(
                            t"accounts-java-identity-subjects = Java identity "
                            t"subjects must be UUIDs, got {(subject_text := repr(subject))}."  # noqa: F841 - captured Fluent binding
                        )
                    ) from error
                return cls(provider=provider, subject=canonical, display_name=display_name, verified_at=verified_at)

    @classmethod
    def discord(cls, discord_id: int, *, verified_at: Instant | None = None) -> AccountIdentity:
        """Create a canonical Discord identity."""
        return cls.for_provider(IdentityProvider.DISCORD, str(discord_id), verified_at=verified_at)

    @classmethod
    def java(
        cls,
        minecraft_uuid: uuid.UUID,
        *,
        username: str | None = None,
        verified_at: Instant | None = None,
    ) -> AccountIdentity:
        """Create a canonical Java Edition identity."""
        return cls.for_provider(
            IdentityProvider.JAVA, str(minecraft_uuid), display_name=username, verified_at=verified_at
        )

    @classmethod
    def bedrock(
        cls,
        xuid: int,
        *,
        gamertag: str | None = None,
        verified_at: Instant | None = None,
    ) -> AccountIdentity:
        """Create a canonical Bedrock identity from an unsigned XUID."""
        return cls.for_provider(IdentityProvider.BEDROCK, str(xuid), display_name=gamertag, verified_at=verified_at)

    @property
    def discord_id(self) -> int | None:
        """Return the Discord snowflake represented by this identity, if any."""
        return int(self.subject) if self.provider is IdentityProvider.DISCORD else None

    @property
    def java_uuid(self) -> uuid.UUID | None:
        """Return the Java UUID represented by this identity, if any."""
        return uuid.UUID(self.subject) if self.provider is IdentityProvider.JAVA else None


class AccountProfile(Base, kw_only=True):
    """Public profile content and visibility choices for an account."""

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
        # Index the foreign key so deleting an identity does not scan every profile.
        Index("account_profiles_avatar_identity_idx", "avatar_identity_id"),
    )
    account_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("accounts.id", name="account_profiles_account_id_fkey", ondelete="CASCADE"),
        primary_key=True,
    )
    display_name: Mapped[str | None] = mapped_column(Text, default=None)
    """Public display name, independent of the creator aliases used for build credits."""

    bio: Mapped[str | None] = mapped_column(Text, default=None)
    pronouns: Mapped[str | None] = mapped_column(Text, default=None)
    links: Mapped[tuple[ProfileLink, ...]] = mapped_column(
        ProfileLinks(), nullable=False, server_default=text("'[]'::jsonb"), default_factory=tuple
    )
    """Published links, stored as a JSON array. Replace the tuple when editing links."""

    hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"), default=False)
    """Whether to hide profile content. Aliases and build credits remain public."""

    avatar_identity_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("account_identities.id", name="account_profiles_avatar_identity_id_fkey", ondelete="SET NULL"),
        default=None,
    )
    """Linked identity used for the avatar. Unlinking it clears this choice; writes check account ownership."""
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
    updated_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )

    @classmethod
    def empty(cls, account_id: int) -> AccountProfile:
        """Return an empty, visible profile for the account."""
        return cls(account_id=account_id)


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


class CreatorAlias(Base, kw_only=True):
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
            # reproduce. These checks catch surrounding spaces and ASCII capitals in
            # raw SQL writes, but do not prove that the full Unicode fold was applied.
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
    id: Mapped[int] = mapped_column(primary_key=True, default=None)
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
    claim_method: Mapped[ClaimMethod | None] = mapped_column(StrEnumText(ClaimMethod), default=None)
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )

    public_creator_id: uuid.UUID | None = None
    """Public identifier of the account claiming this alias."""

    @property
    def is_claimed(self) -> bool:
        """Whether an account has been credited with this name."""
        return self.account_id is not None


@event.listens_for(CreatorAlias, "before_update")
def _refold_on_name_change(_mapper: object, _connection: object, target: CreatorAlias) -> None:
    """Recompute the normalized alias when its display spelling changes."""
    if get_history(target, "name").has_changes():
        target.normalized_name = fold_creator_name(target.name)


class AliasClaim(Base, kw_only=True):
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
    id: Mapped[int] = mapped_column(primary_key=True, default=None)
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
    status: Mapped[ClaimStatus] = mapped_column(StrEnumText(ClaimStatus), nullable=False, default=ClaimStatus.PENDING)
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
    resolved_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    resolved_by_account_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("accounts.id", name="creator_alias_claims_resolved_by_account_id_fkey", ondelete="SET NULL"),
        default=None,
    )

    alias_name: str = ""
    claimant: Account | None = None
    """Claiming account, populated when a staff queue requests it."""


class VerificationCode(Base, kw_only=True):
    """A verification code for linking Java Edition identities."""

    __tablename__ = "verification_codes"
    __table_args__ = (
        CheckConstraint(
            "(reserved_token IS NULL) = (reserved_until IS NULL)",
            name="verification_codes_reservation_complete",
        ),
        # Lookups match on the code alone, so two valid rows with one code would be ambiguous.
        Index("verification_codes_valid_code_key", "code", unique=True, postgresql_where=text("valid")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, default=None)
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
    """Digest of the token holding this code while its consent prompt is open."""

    reserved_until: Mapped[Instant | None] = mapped_column(InstantUTC(), nullable=True, default=None)
    """When the reservation expires and the code becomes available again."""


class VerificationAttempt(Base):
    """Consecutive failed code redemptions for an external identity. A successful redemption resets the count."""

    __tablename__ = "verification_attempts"
    __table_args__ = (
        CheckConstraint(f"provider IN ({_PROVIDER_VALUES})", name="verification_attempts_provider_valid"),
        CheckConstraint("consecutive_failures >= 0", name="verification_attempts_failures_non_negative"),
    )

    provider: Mapped[IdentityProvider] = mapped_column(StrEnumText(IdentityProvider), primary_key=True)
    subject: Mapped[str] = mapped_column(Text, primary_key=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    locked_until: Mapped[Instant | None] = mapped_column(InstantUTC(), nullable=True, default=None)
    updated_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )


class MergeTicket(Base, kw_only=True):
    """A single-use proof that an account consents to being merged into another account."""

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
    code_digest: Mapped[str] = mapped_column(Text, nullable=False, default="")
    """Digest of the code shown once when the ticket is minted."""

    expires_at: Mapped[Instant] = mapped_column(
        InstantUTC(),
        nullable=False,
        default_factory=lambda: Instant.now().add(seconds=MERGE_TICKET_TTL_SECONDS),
    )
    """End of the authentication proof window in which the ticket can be redeemed."""

    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )

    def is_live_at(self, now: Instant) -> bool:
        """Whether this ticket may still be redeemed."""
        return now < self.expires_at
