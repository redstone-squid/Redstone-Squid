"""Build application service tests."""

import asyncio
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from typing import Literal, cast
from uuid import UUID

import pytest
from whenever import Instant

from squid.builds.application import BuildEditPatch, DoorSubmissionInput, RestrictionDefinition
from squid.builds.application.services import (
    BuildEditor,
    BuildService,
)
from squid.builds.application.taxonomy import TaxonomyResolution, normalize_tag_name
from squid.builds.domain import Build, BuildCategory, DoorBuild, OtherBuild, Status, UtilityBuild
from squid.builds.errors import BuildBusyError, BuildNotFoundError, BuildRevisionMismatchError
from squid.core.errors import AuthorizationError, InvalidStateError
from squid.permissions.application import PermissionService
from squid.permissions.application.ports import GrantRecord, SubjectRecords
from squid.permissions.domain import Subject
from squid.sponsors import PublicSponsor
from squid.tags.domain import (
    TagAssignment,
    TagAuthority,
    TagDefinition,
    TagModerationStatus,
    TagSemanticKind,
    TagValueType,
)


class FakeBuildRepository:
    def __init__(self, build: Build | None = None) -> None:
        self.build = build
        self.saved: list[Build] = []
        self.confirmed: list[Build] = []
        self.denied: list[Build] = []

    async def save(self, build: Build) -> None:
        self.saved.append(build)

    async def confirm(self, build: Build) -> None:
        self.confirmed.append(build)

    async def deny(self, build: Build) -> None:
        self.denied.append(build)

    async def get_by_id(self, build_id: int) -> Build | None:
        if self.build is not None and self.build.id == build_id:
            return self.build
        return None

    async def get_by_source_submission_draft_id(self, draft_id: UUID) -> Build | None:
        if self.build is not None and self.build.source_submission_draft_id == draft_id:
            return self.build
        return None

    async def list_ids_for_source_message(self, message_id: int) -> Sequence[int]:
        if self.build is None or self.build.id is None:
            return []
        return [self.build.id for source in self.build.source_messages if source.message_id == message_id]


class FakeRestrictionRepository:
    async def fetch_all_restrictions(self) -> list[RestrictionDefinition]:
        return [RestrictionDefinition("Seamless", "wiring-placement")]

    async def add_alias(self, restriction: str, alias: str) -> None:
        return None


class FakeBuildLocks:
    def __init__(self) -> None:
        self.acquire_result = True
        self.acquisitions: list[tuple[int, bool, float]] = []
        self.releases: list[int] = []
        self.cleanups: list[Instant] = []

    async def acquire(self, build_id: int, *, blocking: bool = True, timeout: float = -1) -> bool:
        self.acquisitions.append((build_id, blocking, timeout))
        return self.acquire_result

    async def release(self, build_id: int) -> None:
        self.releases.append(build_id)

    async def clean_stale(self, *, older_than: Instant) -> None:
        self.cleanups.append(older_than)

    @asynccontextmanager
    async def locked(self, build_id: int, *, timeout: float = 30) -> AsyncGenerator[None]:
        if not await self.acquire(build_id, blocking=True, timeout=timeout):
            raise BuildBusyError(build_id)
        try:
            yield
        finally:
            await self.release(build_id)


class FakeVersions:
    async def newest(self, edition: Literal["Java", "Bedrock"]) -> str:
        return f"{edition} 1.21.0"


class FakeEmbeddings:
    def __init__(self) -> None:
        self.prepared: list[Build] = []
        self.indexed: list[Build] = []

    async def prepare(self, build: Build) -> None:
        self.prepared.append(build)
        build.embedding = [1.0, 2.0]

    async def index(self, build: Build) -> None:
        self.indexed.append(build)


class FakeTaxonomyResolver:
    """Echo known names back as official assignments, like the real resolver.

    Restriction names must appear in ``known_restrictions`` (name -> bucket) to
    resolve; patterns resolve when listed in ``known_patterns``. Anything else
    is reported unknown, normalized.
    """

    def __init__(
        self,
        known_restrictions: dict[str, str] | None = None,
        known_patterns: tuple[str, ...] = ("Regular", "Full Lamp"),
    ) -> None:
        self.known_restrictions = known_restrictions or {
            "Seamless": "wiring-placement",
            "Locational": "miscellaneous",
            "Locational with fixes": "miscellaneous",
            "Directional": "miscellaneous",
            "Directional with fixes": "miscellaneous",
        }
        self.known_patterns = known_patterns

    async def resolve_official(
        self,
        *,
        build_kind: str | None,
        restrictions: Sequence[str],
        patterns: Sequence[str],
    ) -> TaxonomyResolution:
        del build_kind
        restriction_lookup = {
            normalize_tag_name(name): (name, bucket) for name, bucket in self.known_restrictions.items()
        }
        pattern_lookup = {normalize_tag_name(name): name for name in self.known_patterns}
        assignments: list[TagAssignment] = []
        unknown_restrictions: set[str] = set()
        unknown_patterns: set[str] = set()
        next_id = 1
        for requested in dict.fromkeys(restrictions):
            match = restriction_lookup.get(normalize_tag_name(requested))
            if match is None:
                unknown_restrictions.add(normalize_tag_name(requested))
                continue
            name, bucket = match
            assignments.append(
                TagAssignment(
                    definition=TagDefinition(
                        id=next_id,
                        stable_key=normalize_tag_name(name).replace(" ", "-"),
                        display_name=name,
                        authority=TagAuthority.OFFICIAL,
                        semantic_kind=TagSemanticKind.RESTRICTION,
                        value_type=TagValueType.NONE,
                        moderation_status=TagModerationStatus.APPROVED,
                        restriction_type=bucket,
                    )
                )
            )
            next_id += 1
        for requested in dict.fromkeys(patterns):
            name = pattern_lookup.get(normalize_tag_name(requested))
            if name is None:
                unknown_patterns.add(normalize_tag_name(requested))
                continue
            assignments.append(
                TagAssignment(
                    definition=TagDefinition(
                        id=1000 + next_id,
                        stable_key="pattern-" + normalize_tag_name(name).replace(" ", "-"),
                        display_name=name,
                        authority=TagAuthority.OFFICIAL,
                        semantic_kind=TagSemanticKind.PATTERN,
                        value_type=TagValueType.NONE,
                        moderation_status=TagModerationStatus.APPROVED,
                    )
                )
            )
            next_id += 1
        return TaxonomyResolution(
            assignments=tuple(assignments),
            unknown_restrictions=frozenset(unknown_restrictions),
            unknown_patterns=frozenset(unknown_patterns),
        )


def build_service(
    repository: FakeBuildRepository,
    locks: FakeBuildLocks | None = None,
    *,
    permissions: PermissionService | None = None,
) -> BuildService:
    return BuildService(
        repository,
        locks or FakeBuildLocks(),
        FakeRestrictionRepository(),
        FakeVersions(),
        FakeEmbeddings(),
        FakeTaxonomyResolver(),
        permissions=permissions,
    )


@pytest.fixture
def existing_build() -> DoorBuild:
    return DoorBuild(
        id=42,
        submitter_account_id=1,
        submission_status=Status.PENDING,
        miscellaneous_restrictions=["Locational", "Directional with fixes", "Other"],
        extra_info={
            "user": "old",
            "server_info": {"server_ip": "old.example", "coordinates": "1 2 3"},
        },
    )


async def test_edit_applies_only_after_lock_and_releases_on_cancel(existing_build: DoorBuild) -> None:
    repository = FakeBuildRepository(existing_build)
    locks = FakeBuildLocks()
    service = build_service(repository, locks)
    patch = BuildEditPatch(
        dimensions=(10, 11, 12),
        locationality="Not locational",
        extra_user_info=None,
        server_ip="new.example",
        coordinates=None,
    )

    lease = service.edit(42, patch)
    assert existing_build.dimensions == (None, None, None)

    async with lease:
        assert lease.build.dimensions == (10, 11, 12)
        assert lease.build.miscellaneous_restrictions == ["Directional with fixes", "Other"]
        assert "user" not in lease.build.extra_info
        assert lease.build.extra_info.get("server_info") == {"server_ip": "new.example"}

    assert repository.saved == []
    assert locks.acquisitions == [(42, False, 30)]
    assert locks.releases == [42]


async def test_edit_commit_uses_repository_and_releases_after_save(existing_build: DoorBuild) -> None:
    repository = FakeBuildRepository(existing_build)
    locks = FakeBuildLocks()
    service = build_service(repository, locks)

    async with service.edit(42, BuildEditPatch(door_dimensions=(2, 3, 4))) as lease:
        result = await lease.commit()

    assert result is existing_build
    assert existing_build.door_dimensions == (2, 3, 4)
    assert repository.saved == [existing_build]
    assert locks.acquisitions == [(42, False, 30)]
    assert locks.releases == [42]


async def test_edit_releases_lock_when_patch_application_fails(existing_build: DoorBuild) -> None:
    repository = FakeBuildRepository(existing_build)
    locks = FakeBuildLocks()
    service = build_service(repository, locks)
    invalid_dimensions = cast(tuple[int | None, int | None, int | None], (1, 2))
    patch = BuildEditPatch(dimensions=invalid_dimensions)

    with pytest.raises(ValueError, match="not enough values to unpack"):
        async with service.edit(42, patch):
            pass

    assert locks.releases == [42]


async def test_edit_releases_lease_when_cancelled_while_loading(existing_build: DoorBuild) -> None:
    """__aexit__ never runs if __aenter__ is cancelled, so __aenter__ must release itself.

    Without this, the process-local lease survives forever and every later
    acquire for the build spins the backoff loop until it reports the build busy.
    """
    loading = asyncio.Event()

    class BlockingRepository(FakeBuildRepository):
        async def get_by_id(self, build_id: int) -> Build | None:
            loading.set()
            await asyncio.sleep(3600)
            raise AssertionError("unreachable")

    locks = FakeBuildLocks()
    service = build_service(BlockingRepository(existing_build), locks)

    async def enter() -> None:
        async with service.edit(42, BuildEditPatch()):
            pass

    task = asyncio.create_task(enter())
    await loading.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(locks.acquisitions) == 1
    assert locks.releases == [42]


async def test_edit_reports_missing_and_busy_builds(existing_build: DoorBuild) -> None:
    missing_locks = FakeBuildLocks()
    missing_service = build_service(FakeBuildRepository(), missing_locks)
    with pytest.raises(BuildNotFoundError):
        async with missing_service.edit(42, BuildEditPatch()):
            pass
    assert missing_locks.releases == [42]

    busy_locks = FakeBuildLocks()
    busy_locks.acquire_result = False
    busy_service = build_service(FakeBuildRepository(existing_build), busy_locks)
    with pytest.raises(BuildBusyError):
        async with busy_service.edit(42, BuildEditPatch()):
            pass


async def test_edit_loads_build_only_after_acquiring_lease(existing_build: DoorBuild) -> None:
    locks = FakeBuildLocks()
    repository = FakeBuildRepository(existing_build)
    original_get = repository.get_by_id

    async def get_after_lock(build_id: int) -> Build | None:
        assert locks.acquisitions == [(42, False, 30)]
        return await original_get(build_id)

    repository.get_by_id = get_after_lock  # type: ignore[method-assign]

    async with build_service(repository, locks).edit(42, BuildEditPatch()):
        pass


async def test_edit_rejects_a_stale_expected_revision(existing_build: DoorBuild) -> None:
    repository = FakeBuildRepository(existing_build)
    locks = FakeBuildLocks()

    with pytest.raises(BuildRevisionMismatchError) as error:
        async with build_service(repository, locks).edit(42, BuildEditPatch(dimensions=(1, 2, 3)), expected_revision=2):
            pass

    assert error.value.public_context == {"build_id": 42, "expected_revision": 2, "current_revision": 1}
    assert existing_build.dimensions == (None, None, None)
    assert locks.releases == [42]


async def test_status_changes_and_cleanup_use_lock_manager(existing_build: DoorBuild) -> None:
    repository = FakeBuildRepository(existing_build)
    locks = FakeBuildLocks()
    service = build_service(repository, locks)
    cutoff = Instant.from_utc(2026, 7, 29)

    await service.confirm(42)
    await service.deny(42)
    await service.clean_stale_locks(older_than=cutoff)

    assert repository.confirmed == [existing_build]
    assert repository.denied == [existing_build]
    assert len(locks.acquisitions) == 2
    assert locks.releases == [42, 42]
    assert locks.cleanups == [cutoff]


async def test_submit_door_maps_input_and_saves() -> None:
    repository = FakeBuildRepository()
    service = build_service(repository)
    submission = DoorSubmissionInput(
        submitter_account_id=123,
        door_size=(2, 3, None),
        build_size=(8, 9, 10),
        restrictions=("Seamless",),
        locationality="Locational",
        directionality="Not directional",
        information_about_build="notes",
    )

    build = await service.submit_door(submission)

    assert build.category is BuildCategory.DOOR
    assert build.submission_status is Status.PENDING
    assert build.submitter_account_id == 123
    assert build.door_dimensions == (2, 3, None)
    assert build.dimensions == (8, 9, 10)
    assert build.wiring_placement_restrictions == ["Seamless"]
    assert build.miscellaneous_restrictions == ["Locational"]
    assert build.extra_info.get("user") == "notes"
    assert repository.saved == [build]


async def test_classify_restrictions_replaces_existing_values_without_persisting() -> None:
    repository = FakeBuildRepository()
    service = build_service(repository)
    build = UtilityBuild(component_restrictions=["Old"], miscellaneous_restrictions=["Old"])

    result = await service.classify_restrictions(build, ["seamless", "unknown"])

    assert result is build
    assert build.wiring_placement_restrictions == ["Seamless"]
    assert build.component_restrictions == []
    assert build.miscellaneous_restrictions == []
    assert repository.saved == []


async def test_submit_for_account_does_not_require_a_discord_identity() -> None:
    repository = FakeBuildRepository()
    service = build_service(repository)
    draft_id = UUID("11111111-1111-1111-1111-111111111111")

    build = await service.submit_for_account(
        OtherBuild(description="A submission with no chat client attached"),
        submitter_account_id=17,
        source_submission_draft_id=draft_id,
        display_name="  Workshop prototype  ",
        ai_generated=False,
    )

    assert build.submitter_account_id == 17
    assert build.submitter_discord_id is None
    assert build.source_submission_draft_id == draft_id
    assert build.display_name == "Workshop prototype"
    assert build.category is BuildCategory.OTHER
    assert build.submission_status is Status.PENDING
    assert repository.saved == [build]


async def test_get_by_source_submission_draft_id_returns_an_existing_build() -> None:
    draft_id = UUID("11111111-1111-4111-8111-111111111111")
    existing = OtherBuild(id=41, source_submission_draft_id=draft_id)

    result = await build_service(FakeBuildRepository(existing)).get_by_source_submission_draft_id(draft_id)

    assert result is existing


async def test_submit_for_account_returns_the_build_created_by_an_earlier_retry() -> None:
    draft_id = UUID("22222222-2222-2222-2222-222222222222")
    existing = UtilityBuild(
        id=42,
        submitter_account_id=17,
        source_submission_draft_id=draft_id,
        submission_status=Status.PENDING,
    )
    repository = FakeBuildRepository(existing)

    result = await build_service(repository).submit_for_account(
        OtherBuild(),
        submitter_account_id=17,
        source_submission_draft_id=draft_id,
        display_name="ignored retry value",
        ai_generated=False,
    )

    assert result is existing
    assert repository.saved == []


async def test_submit_for_account_rejects_existing_build_with_different_sponsor() -> None:
    draft_id = UUID("22222222-2222-4222-8222-222222222223")
    installation_id = UUID("33333333-3333-4333-8333-333333333333")
    existing = UtilityBuild(
        id=42,
        submitter_account_id=17,
        source_submission_draft_id=draft_id,
        submission_status=Status.PENDING,
        sponsor=PublicSponsor(installation_id, display_name="Original server"),
    )
    repository = FakeBuildRepository(existing)

    with pytest.raises(InvalidStateError):
        await build_service(repository).submit_for_account(
            OtherBuild(sponsor=PublicSponsor(installation_id, display_name="Changed server")),
            submitter_account_id=17,
            source_submission_draft_id=draft_id,
            display_name="Retry",
            ai_generated=False,
        )

    assert repository.saved == []


async def test_save_prepares_defaults_then_indexes_after_relational_persistence() -> None:
    repository = FakeBuildRepository()
    embeddings = FakeEmbeddings()
    locks = FakeBuildLocks()
    service = BuildService(
        repository, locks, FakeRestrictionRepository(), FakeVersions(), embeddings, FakeTaxonomyResolver()
    )
    build = DoorBuild(id=42)

    await service.save(build)

    assert build.versions == ["Java 1.21.0"]
    assert build.embedding == [1.0, 2.0]
    assert repository.saved == [build]
    assert locks.acquisitions == [(42, True, 30)]
    assert locks.releases == [42]
    assert embeddings.prepared == [build]
    assert embeddings.indexed == [build]


class TestAuthorizedEditing:
    """The edit policy the HTTP route used to hold.

    It lived in the transport layer, reading the leased build's status and
    submitter there, so the bot's two edit paths could not reuse it. Ownership is
    now the account comparison it always meant, rather than a snowflake one made
    while a perfectly good account id sat one attribute away.
    """

    OWNER = BuildEditor(subject=Subject(account_id=1))
    STRANGER = BuildEditor(subject=Subject(account_id=2))

    @staticmethod
    def _permissions(*held: str) -> PermissionService:
        class Store:
            async def load_for_subject(self, **_kwargs: object) -> SubjectRecords:
                return SubjectRecords(
                    epoch=1,
                    grants=tuple(GrantRecord(pattern=node, effect=1, subject_account_id=2) for node in held),
                )

            async def epoch(self) -> int:
                return 1

        return PermissionService(Store())

    async def test_the_owner_may_edit_their_pending_build(self, existing_build: DoorBuild) -> None:
        repository = FakeBuildRepository(existing_build)
        service = build_service(repository, permissions=self._permissions())

        build = await service.apply_edit(self.OWNER, 42, BuildEditPatch(extra_user_info="mine"))

        assert build.extra_info.get("user") == "mine"
        assert len(repository.saved) == 1

    async def test_a_non_owner_without_the_node_is_refused_without_committing(self, existing_build: DoorBuild) -> None:
        repository = FakeBuildRepository(existing_build)
        service = build_service(repository, permissions=self._permissions())

        with pytest.raises(AuthorizationError):
            await service.apply_edit(self.STRANGER, 42, BuildEditPatch(extra_user_info="theirs"))

        assert repository.saved == []

    async def test_a_non_owner_holding_the_node_may_edit(self, existing_build: DoorBuild) -> None:
        repository = FakeBuildRepository(existing_build)
        service = build_service(repository, permissions=self._permissions("build.submission.edit"))

        build = await service.apply_edit(self.STRANGER, 42, BuildEditPatch(extra_user_info="moderated"))

        assert build.extra_info.get("user") == "moderated"

    async def test_ownership_does_not_survive_confirmation(self, existing_build: DoorBuild) -> None:
        """A submitter edits their build while it is pending; once it is
        confirmed it is catalogue data, and changing it needs the node."""
        existing_build.submission_status = Status.CONFIRMED
        repository = FakeBuildRepository(existing_build)
        service = build_service(repository, permissions=self._permissions())

        with pytest.raises(AuthorizationError):
            await service.apply_edit(self.OWNER, 42, BuildEditPatch(extra_user_info="mine"))

        assert repository.saved == []

    async def test_a_missing_build_is_reported_before_authorization(self) -> None:
        service = build_service(FakeBuildRepository(), permissions=self._permissions())

        with pytest.raises(BuildNotFoundError):
            await service.apply_edit(self.OWNER, 42, BuildEditPatch(extra_user_info="mine"))

    async def test_authorized_editing_requires_a_permission_service(self, existing_build: DoorBuild) -> None:
        """The bot builds the same service; wiring it without permissions must
        fail loudly rather than authorize everything."""
        service = build_service(FakeBuildRepository(existing_build))

        with pytest.raises(InvalidStateError):
            await service.apply_edit(self.OWNER, 42, BuildEditPatch(extra_user_info="mine"))
