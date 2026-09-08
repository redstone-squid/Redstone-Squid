"""SQLAlchemy authentication models."""

from sqlalchemy import ARRAY, BigInteger, ForeignKey, Identity, Index, LargeBinary, Text, func, text
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column
from whenever import Instant

from squid.persistence.base import Base
from squid.persistence.types import InstantUTC, now


class ApiKey(Base):
    """A revocable high-entropy credential used by an API service client."""

    __tablename__ = "api_keys"
    __table_args__ = (
        Index("api_keys_owner_idx", "owner_account_id"),
        Index("api_keys_created_by_idx", "created_by_account_id"),
        Index("api_keys_key_id_key", "key_id", unique=True),
        Index("api_keys_active", "key_id", postgresql_where=text("revoked_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, init=False)
    """Internal identifier unrelated to the public token key ID."""
    key_id: Mapped[str] = mapped_column(Text, nullable=False)
    """Public lookup portion of the API token; unique, and the only part an index is built on."""
    secret_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    """HMAC-SHA256 digest of the unrecoverable token secret.

    Keyed by a deployment pepper that never reaches this database, so these rows alone cannot
    authenticate anything. See `docs/credential-hashing.md`.
    """
    label: Mapped[str] = mapped_column(Text, nullable=False)
    """Human-readable description of the credential's owner or purpose."""
    scopes: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]"), default_factory=list
    )
    """Permission patterns bounding what this credential may do, stored sorted and de-duplicated.

    Text rather than an enum because the catalogue is open: a pattern granted today can select a
    node registered tomorrow.
    """
    owner_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", name="api_keys_owner_account_id_fkey", ondelete="SET NULL"), default=None
    )
    """Account responsible for the credential, whose permissions bounded its scopes at issue.

    Null for a key issued by the CLI bootstrap path, which has no owner to bound it, and set null
    again if that account is deleted.
    """
    created_by_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", name="api_keys_created_by_account_id_fkey", ondelete="SET NULL"),
        default=None,
    )
    """Account that created the credential; set null if that account is deleted."""
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    """When the credential was created."""
    expires_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    """Instant after which authentication is rejected; null means the credential never expires."""
    revoked_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    """When the credential was revoked; null while it is still usable."""
    last_used_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    """Most recent use, rewritten at most once a minute, so it lags a busy key by up to that."""
    last_used_ip: Mapped[str | None] = mapped_column(INET, default=None)
    """IP address associated with the most recent recorded use."""
