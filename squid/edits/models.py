"""Tables for edit outcomes, kept 24 hours for retries, and resource leases.

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


class EditResourceLease(Base, kw_only=True):
    """Resource identities only; inverse values remain in the mounted editor."""

    __tablename__ = "edit_resource_leases"
    __table_args__ = (Index("edit_resource_leases_resource_expiry_idx", "resource", "expires_at"),)

    operation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    resource: Mapped[str] = mapped_column(Text, primary_key=True)
    expires_at: Mapped[Instant] = mapped_column(InstantUTC(), nullable=False)
