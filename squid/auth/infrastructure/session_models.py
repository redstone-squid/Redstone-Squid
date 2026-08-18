"""SQLAlchemy web-session and OAuth state models."""

import uuid

from sqlalchemy import ForeignKey, Index, LargeBinary, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from whenever import Instant

from squid.accounts.domain import IdentityProvider
from squid.persistence.base import Base
from squid.persistence.types import InstantUTC, now


class WebSession(Base, kw_only=True):
    """A revocable opaque browser session."""

    __tablename__ = "web_sessions"
    __table_args__ = (
        Index("web_sessions_account_idx", "account_id"),
        UniqueConstraint("token_hash", name="web_sessions_token_hash_key"),
        Index("web_sessions_active_idx", "expires_at", postgresql_where=text("revoked_at IS NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default_factory=uuid.uuid4)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", name="web_sessions_account_id_fkey", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[Instant] = mapped_column(InstantUTC(), server_default=func.now(), default_factory=now)
    expires_at: Mapped[Instant] = mapped_column(InstantUTC(), nullable=False)
    last_seen_at: Mapped[Instant] = mapped_column(InstantUTC(), server_default=func.now(), default_factory=now)
    revoked_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    user_agent: Mapped[str | None] = mapped_column(Text, default=None)


class OAuthStateModel(Base, kw_only=True):
    """One-time OAuth PKCE state shared across API replicas."""

    __tablename__ = "oauth_states"

    state: Mapped[str] = mapped_column(Text, primary_key=True)
    code_verifier: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[IdentityProvider] = mapped_column(Text, nullable=False)
    """The namespace this state was minted for; the callback refuses a mismatch."""
    redirect_to: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[Instant] = mapped_column(InstantUTC(), server_default=func.now(), default_factory=now)
    expires_at: Mapped[Instant] = mapped_column(InstantUTC(), nullable=False)
