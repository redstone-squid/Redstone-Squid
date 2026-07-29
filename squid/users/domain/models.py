"""User account domain values."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(slots=True)
class UserAccount:
    """The account information needed by the application layer."""

    discord_id: int | None
    minecraft_uuid: UUID | None
    ign: str | None


@dataclass(frozen=True, slots=True)
class VerificationCode:
    """A valid verification code returned by persistence."""

    minecraft_uuid: UUID
    username: str
