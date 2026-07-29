"""SQLAlchemy user account models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import TIMESTAMP, UUID, BigInteger, Boolean, SmallInteger, Text, func, text
from sqlalchemy.ext.associationproxy import AssociationProxy, association_proxy
from sqlalchemy.orm import Mapped, mapped_column, relationship

from squid.persistence.base import Base

if TYPE_CHECKING:
    from squid.builds.infrastructure.models import Build, BuildCreator


def _build_creator(build: Build) -> BuildCreator:
    from squid.builds.infrastructure.models import BuildCreator

    return BuildCreator(build=build)


class User(Base):
    """A user in the system, which can be linked to both Discord and Minecraft accounts."""

    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True, init=False)
    """Internal primary key. Unrelated to the user's Discord or Minecraft identifiers."""
    ign: Mapped[str] = mapped_column(Text, nullable=True, default=None)
    """The user's Minecraft in-game name, as of the last verification."""
    discord_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    """The user's Discord snowflake ID, if they have linked a Discord account."""
    minecraft_uuid: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    """The user's Mojang account UUID, if they have linked a Minecraft account."""
    created_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=False), server_default=func.now(), default=None
    )
    """When this row was first inserted."""

    build_creators: Mapped[list[BuildCreator]] = relationship(
        back_populates="user", default_factory=list, lazy="raise_on_sql", repr=False
    )
    builds: AssociationProxy[list[Build]] = association_proxy(
        "build_creators", "build", default_factory=list, repr=False, creator=_build_creator
    )


class VerificationCode(Base):
    """A verification code for linking Minecraft accounts."""

    __tablename__ = "verification_codes"
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, init=False)
    minecraft_uuid: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    username: Mapped[str] = mapped_column(Text, nullable=False, default="")
    valid: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"), default=True)
    created: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False), nullable=False, server_default=func.now(), default=func.now()
    )
    expires: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=False),
        nullable=False,
        server_default=text("(now() + '00:10:00'::interval)"),
        default=func.now() + text("INTERVAL '10 minutes'"),
    )
