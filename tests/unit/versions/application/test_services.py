"""Version application service tests."""

from functools import partial

import pytest

from squid.core.actors import ActingContext, AnonymousActor, UserActor
from squid.core.errors import AuthorizationError
from squid.core.versions.application.services import VersionService
from squid.core.versions.domain import Edition, MinecraftVersion, VersionCorrection, VersionEntry
from squid.core.versions.errors import VersionCatalogUnavailableError, VersionNotFoundError
from tests.support.unit_of_work import CommittedState, FakeUnitOfWork
from tests.unit.api.fakes import EmptyPermissionStore


class FakeVersionRepository:
    def __init__(self) -> None:
        self.versions: list[MinecraftVersion] = []

    async def add(self, version: MinecraftVersion) -> VersionEntry:
        self.versions.append(version)
        return VersionEntry(len(self.versions), version)

    async def get(self, version_id: int) -> VersionEntry | None:
        return VersionEntry(version_id, self.versions[version_id - 1]) if 0 < version_id <= len(self.versions) else None

    async def update(self, entry: VersionEntry) -> VersionEntry | None:
        self.versions[entry.id - 1] = entry.version
        return entry

    async def delete(self, version_id: int) -> bool:
        return False

    async def list(self, edition: Edition) -> list[VersionEntry]:
        return [
            VersionEntry(index, version)
            for index, version in enumerate(self.versions, start=1)
            if version.edition == edition
        ]

    async def data_version_for(self, version_label: str) -> int | None:
        return None

    async def label_for_data_version(self, data_version: int) -> str | None:
        return None


class FakeVersionUnitOfWork(FakeUnitOfWork[FakeVersionRepository]):
    @property
    def permissions(self) -> EmptyPermissionStore:
        return EmptyPermissionStore()

    @property
    def versions(self) -> FakeVersionRepository:
        return self.state


async def test_manual_version_add_checks_authority_and_named_announcement_worker_can_publish() -> None:
    committed = CommittedState(FakeVersionRepository())
    service = VersionService(uow_factory=partial(FakeVersionUnitOfWork, committed))
    for context in (ActingContext.direct(UserActor(account_id=55)), ActingContext.worker("unrelated")):
        with pytest.raises(AuthorizationError):
            await service.add("1.21.4", context=context)
    entry = await service.add("1.21.4", context=ActingContext.worker("discord.version-announcement"))
    assert entry.version == MinecraftVersion("Java", 1, 21, 4)


async def test_version_service_honors_explicit_edition() -> None:
    committed = CommittedState(FakeVersionRepository())
    service = VersionService(uow_factory=partial(FakeVersionUnitOfWork, committed))

    version = await service.add(
        "1.21.4", edition="Bedrock", context=ActingContext.direct(UserActor(account_id=1, is_bot_owner=True))
    )

    assert version.version == MinecraftVersion("Bedrock", 1, 21, 4)
    assert committed.value.versions == [version.version]


async def test_version_service_lists_both_editions() -> None:
    committed = CommittedState(FakeVersionRepository())
    committed.value.versions = [MinecraftVersion("Java", 1, 21, 5), MinecraftVersion("Bedrock", 1, 21, 50)]

    entries = await VersionService(uow_factory=partial(FakeVersionUnitOfWork, committed)).list_all(
        context=ActingContext.direct(AnonymousActor())
    )
    assert [entry.version for entry in entries] == committed.value.versions


async def test_correction_changes_only_supplied_fields_and_requires_authority() -> None:
    committed = CommittedState(FakeVersionRepository())
    committed.value.versions = [MinecraftVersion("Java", 1, 21, 40)]
    service = VersionService(uow_factory=partial(FakeVersionUnitOfWork, committed))
    correction = VersionCorrection(edition="Bedrock")

    with pytest.raises(AuthorizationError):
        await service.correct(1, correction, context=ActingContext.direct(UserActor(account_id=55)))
    owner = ActingContext.direct(UserActor(account_id=1, is_bot_owner=True))
    corrected = await service.correct(1, correction, context=owner)

    assert corrected == VersionEntry(1, MinecraftVersion("Bedrock", 1, 21, 40))
    with pytest.raises(VersionNotFoundError):
        await service.correct(2, correction, context=owner)


async def test_version_service_resolves_ranges_and_newest_version() -> None:
    committed = CommittedState(FakeVersionRepository())
    committed.value.versions = [
        MinecraftVersion("Java", 1, 19, 0),
        MinecraftVersion("Java", 1, 19, 1),
        MinecraftVersion("Java", 1, 20, 0),
        MinecraftVersion("Java", 1, 21, 0),
    ]
    service = VersionService(uow_factory=partial(FakeVersionUnitOfWork, committed))

    assert await service.newest("Java", context=ActingContext.direct(AnonymousActor())) == "Java 1.21.0"
    assert await service.resolve_spec("1.19 - 1.20, 1.21+", context=ActingContext.direct(AnonymousActor())) == [
        "Java 1.19.0",
        "Java 1.19.1",
        "Java 1.20.0",
        "Java 1.21.0",
    ]


async def test_version_service_reports_an_empty_catalog_as_infrastructure_failure() -> None:
    committed = CommittedState(FakeVersionRepository())
    service = VersionService(uow_factory=partial(FakeVersionUnitOfWork, committed))

    with pytest.raises(VersionCatalogUnavailableError) as exc_info:
        await service.newest("Bedrock", context=ActingContext.direct(AnonymousActor()))

    assert exc_info.value.context == {"edition": "Bedrock"}


@pytest.mark.parametrize("separator", ["~", "\uff5e", "\u2013", "\u2014", " - "])
async def test_version_ranges_accept_message_separators(separator: str) -> None:
    committed = CommittedState(FakeVersionRepository())
    committed.value.versions = [
        MinecraftVersion("Java", 1, 16, 4),
        MinecraftVersion("Java", 1, 16, 5),
        MinecraftVersion("Java", 1, 17, 0),
        MinecraftVersion("Java", 1, 17, 1),
        MinecraftVersion("Java", 1, 18, 0),
    ]
    service = VersionService(uow_factory=partial(FakeVersionUnitOfWork, committed))

    assert await service.resolve_spec(
        f"Java 1.16.5{separator}1.17", context=ActingContext.direct(AnonymousActor())
    ) == [
        "Java 1.16.5",
        "Java 1.17.0",
        "Java 1.17.1",
    ]


@pytest.mark.parametrize("edition", ["Java", "Bedrock"])
async def test_edition_without_a_version_does_not_claim_compatibility_with_every_release(edition: str) -> None:
    committed = CommittedState(FakeVersionRepository())
    committed.value.versions = [MinecraftVersion("Java", 1, 21, 5), MinecraftVersion("Bedrock", 1, 21, 50)]
    service = VersionService(uow_factory=partial(FakeVersionUnitOfWork, committed))
    assert await service.resolve_spec(edition, context=ActingContext.direct(AnonymousActor())) == []


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("1.8+, !1.16+", ["Java 1.8.0", "Java 1.15.2"]),
        ("!1.16+, 1.8+", ["Java 1.8.0", "Java 1.15.2"]),
        ("1.8+, !1.16 - 1.21", ["Java 1.8.0", "Java 1.15.2", "Java 26.1.0"]),
        ("1.16+, !1.16.5", ["Java 1.16.0", "Java 1.21.0", "Java 26.1.0"]),
        ("!1.16+", []),
        ("Bedrock 1.8+, !1.16+", ["Bedrock 1.8.0"]),
    ],
)
async def test_version_exclusions_do_not_claim_broken_releases(spec: str, expected: list[str]) -> None:
    committed = CommittedState(FakeVersionRepository())
    committed.value.versions = [
        MinecraftVersion("Java", 1, 8, 0),
        MinecraftVersion("Java", 1, 15, 2),
        MinecraftVersion("Java", 1, 16, 0),
        MinecraftVersion("Java", 1, 16, 5),
        MinecraftVersion("Java", 1, 21, 0),
        MinecraftVersion("Java", 26, 1, 0),
        MinecraftVersion("Bedrock", 1, 8, 0),
        MinecraftVersion("Bedrock", 1, 16, 0),
    ]
    service = VersionService(uow_factory=partial(FakeVersionUnitOfWork, committed))

    assert await service.resolve_spec(spec, context=ActingContext.direct(AnonymousActor())) == expected
