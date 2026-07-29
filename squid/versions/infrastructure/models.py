"""SQLAlchemy Minecraft version models."""

from sqlalchemy import SmallInteger, Text
from sqlalchemy.orm import Mapped, mapped_column

from squid.persistence.base import Base


class Version(Base):
    """A version of Minecraft that a build is compatible with."""

    __tablename__ = "versions"
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, init=False)
    edition: Mapped[str] = mapped_column(Text, nullable=False)
    major_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    minor_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    patch_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
