"""SQLAlchemy model for durable idempotency records."""

import uuid

from sqlalchemy import CheckConstraint, Index, LargeBinary, SmallInteger, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from whenever import Instant

from squid.persistence.base import Base
from squid.persistence.types import InstantUTC, now


class IdempotencyRequest(Base, kw_only=True):
    """One caller-scoped mutation reservation and its completed HTTP response."""

    __tablename__ = "idempotency_requests"
    __table_args__ = (
        CheckConstraint("state IN ('in_progress', 'completed')", name="idempotency_requests_state_check"),
        CheckConstraint(
            "(state = 'in_progress' AND response_status IS NULL AND response_headers IS NULL "
            "AND response_body_ciphertext IS NULL AND response_body_key_id IS NULL AND response_body_nonce IS NULL "
            "AND completed_at IS NULL) OR (state = 'completed' AND response_status IS NOT NULL "
            "AND response_headers IS NOT NULL AND response_body_ciphertext IS NOT NULL "
            "AND response_body_key_id IS NOT NULL AND response_body_nonce IS NOT NULL AND completed_at IS NOT NULL)",
            name="idempotency_requests_response_state_check",
        ),
        UniqueConstraint("principal", "idempotency_key", name="idempotency_requests_principal_key"),
        Index("idempotency_requests_expires_at_idx", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default_factory=uuid.uuid4)
    principal: Mapped[str] = mapped_column(Text, nullable=False)
    """The caller namespace a key is reserved in; keys of different callers never collide.

    The application layer calls this the *caller*, as do the `RateLimit-Policy` partition and
    `SQUID_API_RATE_LIMIT_PRINCIPAL_REQUESTS`.
    """
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    """The key the caller sent, unique within its principal."""
    request_fingerprint: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    """Digest of the request this key was reserved for; a replay that differs is a conflict."""
    method: Mapped[str] = mapped_column(Text, nullable=False)
    """HTTP method of the reserved request, kept for diagnostics and response authentication."""
    route: Mapped[str] = mapped_column(Text, nullable=False)
    """Route template of the reserved request, kept for diagnostics and response authentication."""
    state: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'in_progress'"), default="in_progress"
    )
    """Either ``in_progress`` or ``completed``; a check constraint ties the response columns to it."""
    response_status: Mapped[int | None] = mapped_column(SmallInteger, default=None)
    """Status of the retained response; null until the request completes."""
    response_headers: Mapped[dict[str, str] | None] = mapped_column(JSONB, default=None)
    """Headers replayed with the retained response, and authenticated as part of its ciphertext."""
    response_body_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)
    """AES-256-GCM body, bound to this row's identity so it cannot be replayed under another key."""
    response_body_key_id: Mapped[str | None] = mapped_column(Text, default=None)
    """Which keyring entry sealed the body; a rotated-out key makes the row unreadable."""
    response_body_nonce: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)
    """The 12-byte AES-GCM nonce, generated per response."""
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=now
    )
    completed_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    """When the response was stored; null while the request is still in progress."""
    expires_at: Mapped[Instant] = mapped_column(InstantUTC(), nullable=False)
    """When the reservation stops replaying and becomes eligible for deletion."""
