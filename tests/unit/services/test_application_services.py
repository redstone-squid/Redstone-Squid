"""Tests for small application services and their expected outcomes."""

from collections.abc import Awaitable, Callable, Iterable, Sequence
from typing import Unpack
from uuid import UUID

import pytest

from squid.db.schema import Message, MessagePurposeLiteral, Setting
from squid.exceptions import (
    AccountAlreadyLinkedError,
    InvalidMessageError,
    InvalidVerificationCodeError,
    MinecraftAccountNotFoundError,
    VersionCatalogUnavailableError,
)
from squid.services.messages import MessageService, TrackedMessage
from squid.services.settings import SettingOptions, SettingsService
from squid.services.users import UserAccount, UserService, VerificationCode
from squid.services.versions import Edition, MinecraftVersion, VersionService


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
    minecraft_uuid = UUID("11111111-1111-1111-1111-111111111111")
    repository.code = VerificationCode(minecraft_uuid, "Player")
    service = UserService(repository, username_lookup("Player"), lambda: 123456)

    await service.link_minecraft_account(1, "valid")
    generated = await service.generate_verification_code(minecraft_uuid)

    assert repository.user == UserAccount(1, minecraft_uuid, "Player")
    assert generated == 123456
    assert repository.created_code == "123456"


async def test_user_link_rejects_a_different_existing_account() -> None:
    repository = FakeUserRepository()
    existing_uuid = UUID("11111111-1111-1111-1111-111111111111")
    requested_uuid = UUID("22222222-2222-2222-2222-222222222222")
    repository.user = UserAccount(1, existing_uuid, "Existing")
    repository.code = VerificationCode(requested_uuid, "Requested")
    service = UserService(repository, username_lookup("Requested"), lambda: 123456)

    with pytest.raises(AccountAlreadyLinkedError) as exc_info:
        await service.link_minecraft_account(1, "valid")

    assert exc_info.value.context == {"discord_id": 1, "minecraft_uuid": str(existing_uuid)}
    assert exc_info.value.public_context == {}


async def test_code_generation_rejects_unknown_minecraft_account() -> None:
    minecraft_uuid = UUID("11111111-1111-1111-1111-111111111111")
    service = UserService(FakeUserRepository(), username_lookup(None), lambda: 123456)

    with pytest.raises(MinecraftAccountNotFoundError) as exc_info:
        await service.generate_verification_code(minecraft_uuid)

    assert exc_info.value.public_context == {"minecraft_uuid": str(minecraft_uuid)}


class FakeSettingsRepository:
    def __init__(self) -> None:
        self.values = SettingOptions()

    async def get(self, server_ids: Iterable[int], setting: Setting) -> dict[int, int | list[int] | None]:
        return {}

    async def get_single(self, server_id: int, setting: Setting) -> int | list[int] | None:
        if setting == "Staff":
            return self.values.get("Staff", [])
        if setting == "Trusted":
            return self.values.get("Trusted", [])
        return self.values.get(setting)

    async def get_all(self, server_id: int) -> SettingOptions:
        return self.values

    async def set(self, server_id: int, **settings: Unpack[SettingOptions]) -> None:
        self.values.update(settings)

    async def on_guild_join(self, server_id: int) -> None:
        return None

    async def on_guild_remove(self, server_id: int) -> None:
        return None


async def test_settings_clear_uses_shape_specific_empty_values() -> None:
    repository = FakeSettingsRepository()
    service = SettingsService(repository)

    await service.clear(1, "Vote")
    await service.clear(1, "Staff")

    assert repository.values == {"Vote": None, "Staff": []}


class FakeVersionRepository:
    def __init__(self) -> None:
        self.versions: list[MinecraftVersion] = []

    async def add(self, version: MinecraftVersion) -> MinecraftVersion:
        self.versions.append(version)
        return version

    async def list(self, edition: Edition) -> list[MinecraftVersion]:
        return [version for version in self.versions if version.edition == edition]


async def test_version_service_honors_explicit_edition() -> None:
    service = VersionService(FakeVersionRepository())

    version = await service.add("1.21.4", edition="Bedrock")

    assert version == MinecraftVersion("Bedrock", 1, 21, 4)


async def test_version_service_resolves_ranges_and_newest_version() -> None:
    repository = FakeVersionRepository()
    repository.versions = [
        MinecraftVersion("Java", 1, 19, 0),
        MinecraftVersion("Java", 1, 19, 1),
        MinecraftVersion("Java", 1, 20, 0),
        MinecraftVersion("Java", 1, 21, 0),
    ]
    service = VersionService(repository)

    assert await service.newest("Java") == "Java 1.21.0"
    assert await service.resolve_spec("1.19 - 1.20, 1.21+") == [
        "Java 1.19.0",
        "Java 1.19.1",
        "Java 1.20.0",
        "Java 1.21.0",
    ]


async def test_version_service_reports_an_empty_catalog_as_infrastructure_failure() -> None:
    service = VersionService(FakeVersionRepository())

    with pytest.raises(VersionCatalogUnavailableError) as exc_info:
        await service.newest("Bedrock")

    assert exc_info.value.context == {"edition": "Bedrock"}


class FakeMessageRepository:
    def __init__(self) -> None:
        self.inserted: tuple[int, int | None] | None = None

    async def insert(
        self,
        message_id: int,
        server_id: int,
        channel_id: int,
        author_id: int,
        purpose: MessagePurposeLiteral,
        content: str | None,
        *,
        build_id: int | None = None,
        vote_session_id: int | None = None,
    ) -> None:
        self.inserted = message_id, vote_session_id

    async def update_edited_time(self, message_id: int) -> None:
        return None

    async def get_by_id(self, message_id: int) -> Message | None:
        return None

    async def delete_by_id(self, message_id: int) -> Message:
        msg = "not implemented by this test fake"
        raise LookupError(msg)

    async def get_outdated_messages(self, server_id: int) -> Sequence[Message]:
        return []


async def test_message_service_requires_vote_session_id() -> None:
    service = MessageService(FakeMessageRepository())
    message = TrackedMessage(1, 2, 3, 4, "content")

    with pytest.raises(InvalidMessageError, match="vote_session_id"):
        await service.track(message, "vote")
