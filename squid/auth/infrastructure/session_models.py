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
    """A revocable opaque browser session, valid until it expires or is revoked."""

    __tablename__ = "web_sessions"
    __table_args__ = (
        Index("web_sessions_account_idx", "account_id"),
        UniqueConstraint("token_hash", name="web_sessions_token_hash_key"),
        Index("web_sessions_active_idx", "expires_at", postgresql_where=text("revoked_at IS NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default_factory=uuid.uuid4)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    """Keyed digest of the session token, which is disclosed to the browser once and never stored."""
    account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", name="web_sessions_account_id_fkey", ondelete="CASCADE"), nullable=False
    )
    """Account this session authenticates as; deleting the account deletes the session."""
    created_at: Mapped[Instant] = mapped_column(InstantUTC(), server_default=func.now(), default_factory=now)
    expires_at: Mapped[Instant] = mapped_column(InstantUTC(), nullable=False)
    """When the session stops authenticating, set from the deployment's session TTL at creation."""
    last_seen_at: Mapped[Instant] = mapped_column(InstantUTC(), server_default=func.now(), default_factory=now)
    """Stamped on every successful authentication with this session."""
    revoked_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    """When logout revoked the session; null while it is still usable."""
    user_agent: Mapped[str | None] = mapped_column(Text, default=None)
    """User agent that created the session, kept so a person can recognise their own sessions."""


class OAuthStateModel(Base, kw_only=True):
    """One-time OAuth PKCE state shared across API replicas; the callback deletes the row it reads."""

    __tablename__ = "oauth_states"

    state: Mapped[str] = mapped_column(Text, primary_key=True)
    """The opaque value handed to the provider and returned on the callback."""
    code_verifier: Mapped[str] = mapped_column(Text, nullable=False)
    """PKCE verifier whose S256 hash went to the provider as the challenge."""
    provider: Mapped[IdentityProvider] = mapped_column(Text, nullable=False)
    """The namespace this state was minted for; the callback refuses a mismatch."""
    redirect_to: Mapped[str | None] = mapped_column(Text, default=None)
    """Where to send the browser after login; null returns it to the default landing page."""
    created_at: Mapped[Instant] = mapped_column(InstantUTC(), server_default=func.now(), default_factory=now)
    expires_at: Mapped[Instant] = mapped_column(InstantUTC(), nullable=False)
    """Ten minutes after minting; a state consumed later is refused rather than redeemed."""
