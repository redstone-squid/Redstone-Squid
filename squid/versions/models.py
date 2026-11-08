"""SQLAlchemy Minecraft version models."""

from sqlalchemy import CheckConstraint, Integer, SmallInteger, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from squid.persistence.base import Base


class Version(Base):
    """A version of Minecraft that a build is compatible with."""

    __tablename__ = "versions"
    __table_args__ = (
        UniqueConstraint("edition", "major_version", "minor_version", "patch_number", name="versions_number_key"),
        CheckConstraint("edition IN ('Java', 'Bedrock')", name="versions_edition_check"),
        CheckConstraint(
            "major_version >= 0 AND minor_version >= 0 AND patch_number >= 0", name="versions_number_check"
        ),
    )
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, init=False)
    edition: Mapped[str] = mapped_column(Text, nullable=False)
    major_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    minor_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    patch_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    data_version: Mapped[int | None] = mapped_column(Integer, default=None)
    """Minecraft's world-format number, used when retargeting schematics.

    None means no data version is recorded. Bedrock and early Java releases do not
    have one.
    """
