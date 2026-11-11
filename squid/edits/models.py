"""Edit outcomes, kept 24 hours so a retried command returns its first result.

The values needed for undo are not stored; they stay in the editor's receipts.
"""

from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, Index, LargeBinary, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from whenever import Instant

from squid.persistence.base import Base
from squid.persistence.types import InstantUTC


class EditOperation(Base, kw_only=True):
    __tablename__ = "edit_operations"
    __table_args__ = (
        CheckConstraint("octet_length(fingerprint) = 32", name="edit_operations_fingerprint_length"),
        Index("edit_operations_expiry_idx", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    revisions: Mapped[list[dict[str, str | int]]] = mapped_column(JSONB, nullable=False)
    changed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    final: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[Instant] = mapped_column(InstantUTC(), nullable=False)
    expires_at: Mapped[Instant] = mapped_column(InstantUTC(), nullable=False)
