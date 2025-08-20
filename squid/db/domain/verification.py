import datetime
import hashlib
import secrets
import uuid
from dataclasses import field
from typing import Final, final

from squid.utils import freezable_dataclass, frozen_field


def _hash(value: str) -> str:
    """Hash a verification code or other string.

    The hashing algorithm is an internal detail and may be changed as needed.

    Args:
        value: The string to hash.

    Returns:
        The hashed value as a hexadecimal string.
    """
    return hashlib.sha256(value.encode()).hexdigest()


@final
@freezable_dataclass(slots=True)
class VerificationCode:
    """A verification code for linking a Minecraft account to a user."""

    hashed_code: Final[str] = frozen_field()
    minecraft_uuid: Final[uuid.UUID] = frozen_field()
    minecraft_username: Final[str] = frozen_field()
    valid: bool = True
    created: Final[datetime.datetime] = frozen_field(
        default_factory=lambda: datetime.datetime.now(tz=datetime.timezone.utc)
    )
    expires: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(minutes=10)
    )

    def is_valid(self) -> bool:
        """Check if the verification code is still valid."""
        # FIXME: This is extremely stupid, but it is how the original code worked. The valid field should never have existed.
        return self.valid and self.expires > datetime.datetime.now(tz=datetime.timezone.utc)

    def invalidate(self) -> None:
        """Invalidate this verification code."""
        self.valid = False

    @classmethod
    async def generate(cls, minecraft_uuid: uuid.UUID, minecraft_username: str) -> tuple["VerificationCode", str]:
        """Generate a new verification code for this Minecraft account.

        Args:
            minecraft_uuid: The user's Minecraft UUID.
            minecraft_username: The user's Minecraft username.

        Returns:
            The generated VerificationCode object and the unhashed code.

        Raises:
            ValueError: user_uuid does not match a valid Minecraft account.
        """

        unhashed_code = str(secrets.randbelow(900_000) + 100_000)  # Generate a random 6-digit code
        verification_code = cls(
            minecraft_uuid=minecraft_uuid, hashed_code=_hash(unhashed_code), minecraft_username=minecraft_username
        )
        return verification_code, unhashed_code
