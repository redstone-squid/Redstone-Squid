"""SQLAlchemy Minecraft version models."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import SmallInteger, Text
from sqlalchemy.ext.associationproxy import AssociationProxy, association_proxy
from sqlalchemy.orm import Mapped, mapped_column, relationship

from squid.persistence.base import Base

if TYPE_CHECKING:
    from squid.builds.infrastructure.models import Build, BuildVersion


def _build_version(build: Build) -> BuildVersion:
    from squid.builds.infrastructure.models import BuildVersion

    return BuildVersion(build=build)


class Version(Base):
    """A version of Minecraft that a build is compatible with."""

    __tablename__ = "versions"
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, init=False)
    edition: Mapped[str] = mapped_column(Text, nullable=False)
    major_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    minor_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    patch_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    build_versions: Mapped[list[BuildVersion]] = relationship(
        back_populates="version", default_factory=list, lazy="raise_on_sql", repr=False
    )
    builds: AssociationProxy[list[Build]] = association_proxy(
        "build_versions",
        "build",
        default_factory=list,
        repr=False,
        creator=_build_version,
    )
