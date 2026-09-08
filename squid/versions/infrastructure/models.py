"""SQLAlchemy Minecraft version models."""

from sqlalchemy import Integer, SmallInteger, Text
from sqlalchemy.orm import Mapped, mapped_column

from squid.persistence.base import Base


class Version(Base):
    """One released Minecraft version that builds may declare compatibility with."""

    __tablename__ = "versions"
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, init=False)
    edition: Mapped[str] = mapped_column(Text, nullable=False)
    """'Java' or 'Bedrock'; any other value fails to load."""
    major_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    minor_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    patch_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    data_version: Mapped[int | None] = mapped_column(Integer, default=None)
    """Minecraft's own numeric world-format version, needed to retarget a schematic at this
    release. Nullable because Bedrock has no equivalent and Java releases predating the field
    have none either."""
