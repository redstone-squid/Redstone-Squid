"""SQLAlchemy model for durable idempotency records."""

import uuid

from sqlalchemy import CheckConstraint, Index, LargeBinary, SmallInteger, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from whenever import Instant

from squid.persistence.base import Base
from squid.persistence.types import InstantUTC


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
    """The caller namespace a key is reserved in.

    The application layer calls this the *caller*; the column keeps the older
    word because renaming it needs a migration, a rewrite of the unique index it
    anchors, and a redeploy window, for a name no client ever sees. The same
    trade applies to the `principal` partition in `RateLimit-Policy` and to
    `SQUID_API_RATE_LIMIT_PRINCIPAL_REQUESTS`, both of which deployments and
    clients can observe. If the ban is meant repo-wide, that is its own commit.
    """
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    request_fingerprint: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    route: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'in_progress'"), default="in_progress"
    )
    response_status: Mapped[int | None] = mapped_column(SmallInteger, default=None)
    response_headers: Mapped[dict[str, str] | None] = mapped_column(JSONB, default=None)
    response_body_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)
    response_body_key_id: Mapped[str | None] = mapped_column(Text, default=None)
    response_body_nonce: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)
    created_at: Mapped[Instant] = mapped_column(
        InstantUTC(), nullable=False, server_default=func.now(), default_factory=Instant.now
    )
    completed_at: Mapped[Instant | None] = mapped_column(InstantUTC(), default=None)
    expires_at: Mapped[Instant] = mapped_column(InstantUTC(), nullable=False)
