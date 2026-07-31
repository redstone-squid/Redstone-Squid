"""User application service tests."""

from collections.abc import Awaitable, Callable
from uuid import UUID

import pytest

from squid.users.application import UserService
from squid.users.domain import UserAccount, VerificationCode
from squid.users.errors import AccountAlreadyLinkedError, InvalidVerificationCodeError, MinecraftAccountNotFoundError

EXISTING_MINECRAFT_UUID = UUID("11111111-1111-1111-1111-111111111111")


class FakeUserRepository:
    def __init__(self) -> None:
        self.user: UserAccount | None = None
        self.code: VerificationCode | None = None
        self.created_code: str | None = None

    async def add(
        self,
        *,
        discord_id: int | None = None,
        minecraft_uuid: UUID | None = None,
        ign: str | None = None,
    ) -> UserAccount:
        self.user = UserAccount(discord_id, minecraft_uuid, ign)
        return self.user

    async def get_by_discord_id(self, discord_id: int) -> UserAccount | None:
        return self.user if self.user is not None and self.user.discord_id == discord_id else None

    async def update(self, user: UserAccount) -> None:
        self.user = user

    async def unlink_minecraft_account(self, discord_id: int) -> bool:
        if self.user is None or self.user.discord_id != discord_id:
            return False
        self.user.minecraft_uuid = None
        return True

    async def get_valid_verification_code(self, code: str) -> VerificationCode | None:
        return self.code

    async def invalidate_codes(self, minecraft_uuid: UUID) -> None:
        return None

    async def create_verification_code(self, *, minecraft_uuid: UUID, code: str, username: str) -> None:
        self.created_code = code


def username_lookup(username: str | None) -> Callable[[UUID], Awaitable[str | None]]:
    async def lookup(_minecraft_uuid: UUID) -> str | None:
        return username

    return lookup


async def test_user_link_rejects_invalid_code() -> None:
    service = UserService(FakeUserRepository(), username_lookup("Player"), lambda: 123456)

    with pytest.raises(InvalidVerificationCodeError, match="invalid or expired"):
        await service.link_minecraft_account(1, "bad")


async def test_user_link_and_code_generation() -> None:
    repository = FakeUserRepository()
    minecraft_uuid = EXISTING_MINECRAFT_UUID
    repository.code = VerificationCode(minecraft_uuid, "Player")
    service = UserService(repository, username_lookup("Player"), lambda: 123456)

    await service.link_minecraft_account(1, "valid")
    generated = await service.generate_verification_code(minecraft_uuid)

    assert repository.user == UserAccount(1, minecraft_uuid, "Player")
    assert generated == 123456
    assert repository.created_code == "123456"


async def test_user_link_rejects_a_different_existing_account() -> None:
    repository = FakeUserRepository()
    existing_uuid = EXISTING_MINECRAFT_UUID
    requested_uuid = UUID("22222222-2222-2222-2222-222222222222")
    repository.user = UserAccount(1, existing_uuid, "Existing")
    repository.code = VerificationCode(requested_uuid, "Requested")
    service = UserService(repository, username_lookup("Requested"), lambda: 123456)

    with pytest.raises(AccountAlreadyLinkedError) as exc_info:
        await service.link_minecraft_account(1, "valid")

    assert exc_info.value.context == {"discord_id": 1, "minecraft_uuid": str(existing_uuid)}
    assert exc_info.value.public_context == {}


async def test_code_generation_rejects_unknown_minecraft_account() -> None:
    minecraft_uuid = EXISTING_MINECRAFT_UUID
    service = UserService(FakeUserRepository(), username_lookup(None), lambda: 123456)

    with pytest.raises(MinecraftAccountNotFoundError) as exc_info:
        await service.generate_verification_code(minecraft_uuid)

    assert exc_info.value.public_context == {"minecraft_uuid": str(minecraft_uuid)}
