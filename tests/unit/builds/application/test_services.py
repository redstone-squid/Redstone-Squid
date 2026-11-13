"""Build application service tests."""

from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from copy import copy
from decimal import Decimal
from functools import partial
from typing import Literal, cast, override
from uuid import UUID

import anyio
import pytest
from whenever import Instant

from squid.builds.application import BuildEditPatch, RestrictionDefinition
from squid.builds.application.services import (
    BuildEditor,
    BuildService,
)
from squid.builds.application.taxonomy import TaxonomyResolution, apply_build_taxonomy
from squid.builds.domain import BuildCategory, Status, normalize_tag_name
from squid.builds.errors import BuildBusyError, BuildNotFoundError, BuildRevisionMismatchError, InvalidBuildError
from squid.builds.models import Build, DoorBuild, OtherBuild, UtilityBuild
from squid.core.actors import Actor
from squid.core.errors import AuthorizationError, InvalidStateError
from squid.permissions.application import PermissionService
from squid.permissions.application.ports import PermissionStore, SubjectRecords
from squid.permissions.models import PermissionGrant
from squid.tags.domain import TagAuthority, TagModerationStatus, TagSemanticKind, TagValueType
from squid.tags.models import TagAssignment, TagDefinition
from tests.support.permissions import permission_uow_factory
from tests.support.unit_of_work import FakeBuildEditing, PublishingFakeUnitOfWork


class FakeBuildRepository(FakeBuildEditing):
    def __init__(self, build: Build | None = None) -> None:
        self.build = build
        self.saved: list[Build] = []
        self.confirmed: list[Build] = []
        self.denied: list[Build] = []

    async def save(self, build: Build) -> None:
        self.saved.append(build)

    async def set_status(self, build: Build, status: Status) -> None:
        build.submission_status = status

    async def confirm(self, build: Build) -> None:
        self.confirmed.append(build)

    async def deny(self, build: Build) -> None:
        self.denied.append(build)

    async def get_for_update(self, build_id: int) -> Build | None:
        return await self.get_by_id(build_id)

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
        return [self.build.id for source in self.build.source_messages if source.id == message_id]


class FakeRestrictionRepository:
    async def fetch_all_restrictions(self) -> list[RestrictionDefinition]:
        return [RestrictionDefinition("Seamless", "wiring-placement")]

    async def add_alias(self, restriction: str, alias: str) -> None:
        return None

    async def replace_alias(self, restriction: str, alias: str, expected: bool, replacement: bool) -> bool:
        raise NotImplementedError


class EmptyPermissions:
    async def load_for_subject(self, **_kwargs: object) -> SubjectRecords:
        return SubjectRecords(epoch=1)

    async def epoch(self) -> int:
        return 1


def copy_repository(repository: FakeBuildRepository) -> FakeBuildRepository:
    clone = copy(repository)
    clone.build = None if repository.build is None else repository.build.copy_for_edit()
    clone.saved = list(repository.saved)
    clone.confirmed = list(repository.confirmed)
    clone.denied = list(repository.denied)
    return clone


class BuildScope(PublishingFakeUnitOfWork[FakeBuildRepository]):
    def __init__(self, repository: FakeBuildRepository, permissions: PermissionStore | None = None) -> None:
        super().__init__(repository, clone=copy_repository)
        self.permissions = permissions or EmptyPermissions()

    @property
    def builds(self) -> FakeBuildRepository:
        return self.state


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
    """Resolve configured restriction and pattern names to official tag assignments.

    known_restrictions maps names to restriction types; known_patterns supplies pattern names.
    Matching normalizes case and whitespace. Unknown names are returned in normalized form.
    The fake ignores build_kind and does not look up aliases.
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

    async def showcase_definitions(self, keys: Sequence[str]) -> Sequence[TagDefinition]:
        return [definition for definition in SHOWCASE_TAGS.values() if definition.stable_key in keys]


def _showcase(tag_id: int, key: str, value_type: TagValueType = TagValueType.NONE) -> TagDefinition:
    return TagDefinition(
        id=tag_id,
        stable_key=key,
        display_name=key.title(),
        authority=TagAuthority.USER,
        semantic_kind=TagSemanticKind.SHOWCASE,
        value_type=value_type,
        moderation_status=TagModerationStatus.APPROVED,
    )


SHOWCASE_TAGS = {
    "compact": _showcase(2001, "compact"),
    "tileable": _showcase(2002, "tileable"),
    "speed": _showcase(2003, "speed", TagValueType.NUMERIC),
}


def build_service(
    repository: FakeBuildRepository,
    locks: FakeBuildLocks | None = None,
    *,
    permissions: PermissionStore | None = None,
) -> BuildService:
    return BuildService(
        locks or FakeBuildLocks(),
        FakeRestrictionRepository(),
        FakeVersions(),
        FakeEmbeddings(),
        FakeTaxonomyResolver(),
        permissions=None if permissions is None else PermissionService(uow_factory=permission_uow_factory(permissions)),
        uow_factory=lambda: BuildScope(repository, permissions),
    )


@pytest.fixture
def existing_build() -> DoorBuild:
    return DoorBuild(
        id=42,
        submitter_account_id=1,
        submission_status=Status.PENDING,
        miscellaneous_restrictions=["Locational", "Directional with fixes", "Other"],
        description="old",
        extra_info={
            "server_info": {"server_ip": "old.example", "coordinates": "1 2 3"},
        },
    )


async def test_edit_prepares_a_detached_patch_then_commits(existing_build: DoorBuild) -> None:
    repository = FakeBuildRepository(existing_build)
    locks = FakeBuildLocks()
    service = build_service(repository, locks)
    result = await service.edit(
        42,
        BuildEditPatch(dimensions=(10, 11, 12), extra_user_info="changed"),
        actor="test",
        operation="edit",
        source="test",
    )
    assert result.dimensions == (10, 11, 12)
    assert existing_build.dimensions == (None, None, None)
    assert repository.saved[0].description == "changed"
    assert locks.releases == [42]


async def test_invalid_patch_does_not_acquire_a_write_lock(existing_build: DoorBuild) -> None:
    repository = FakeBuildRepository(existing_build)
    locks = FakeBuildLocks()
    invalid_dimensions = cast(tuple[int | None, int | None, int | None], (1, 2))
    with pytest.raises(ValueError, match="not enough values to unpack"):
        await build_service(repository, locks).edit(
            42,
            BuildEditPatch(dimensions=invalid_dimensions),
            actor="test",
            operation="edit",
            source="test",
        )
    assert locks.acquisitions == []
    assert repository.saved == []


async def test_cancellation_during_preparation_holds_no_write_lock(existing_build: DoorBuild) -> None:
    loading = anyio.Event()

    class BlockingRepository(FakeBuildRepository):
        @override
        async def get_by_id(self, build_id: int) -> Build | None:
            loading.set()
            await anyio.sleep_forever()

    locks = FakeBuildLocks()
    service = build_service(BlockingRepository(existing_build), locks)
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(
            partial(service.edit, actor="test", operation="edit", source="test"),
            42,
            BuildEditPatch(),
        )
        await loading.wait()
        tasks.cancel_scope.cancel()
    assert locks.acquisitions == []


async def test_edit_reports_missing_and_busy_builds(existing_build: DoorBuild) -> None:
    with pytest.raises(BuildNotFoundError):
        await build_service(FakeBuildRepository()).edit(
            42, BuildEditPatch(), actor="test", operation="edit", source="test"
        )
    locks = FakeBuildLocks()
    locks.acquire_result = False
    with pytest.raises(BuildBusyError):
        await build_service(FakeBuildRepository(existing_build), locks).edit(
            42, BuildEditPatch(), actor="test", operation="edit", source="test"
        )


async def test_edit_rechecks_revision_after_preparation(existing_build: DoorBuild) -> None:
    repository = FakeBuildRepository(existing_build)
    service = build_service(repository)

    class ConcurrentEdit(FakeEmbeddings):
        async def prepare(self, build: Build) -> None:
            existing_build.revision += 1

    service._embeddings = ConcurrentEdit()
    with pytest.raises(BuildRevisionMismatchError):
        await service.edit(
            42,
            BuildEditPatch(extra_user_info="stale"),
            expected_revision=1,
            actor="test",
            operation="edit",
            source="test",
        )
    assert repository.saved == []


async def test_edit_rejects_a_stale_expected_revision(existing_build: DoorBuild) -> None:
    repository = FakeBuildRepository(existing_build)
    locks = FakeBuildLocks()
    with pytest.raises(BuildRevisionMismatchError) as error:
        await build_service(repository, locks).edit(
            42,
            BuildEditPatch(dimensions=(1, 2, 3)),
            expected_revision=2,
            actor="test",
            operation="edit",
            source="test",
        )
    assert error.value.public_context == {"build_id": 42, "expected_revision": 2, "current_revision": 1}
    assert locks.acquisitions == []
    assert existing_build.dimensions == (None, None, None)


async def test_status_changes_and_cleanup_use_lock_manager(existing_build: DoorBuild) -> None:
    repository = FakeBuildRepository(existing_build)
    locks = FakeBuildLocks()
    service = build_service(repository, locks)
    cutoff = Instant.from_utc(2026, 7, 29)

    await service.confirm(42, actor="test", operation="edit", source="test")
    await service.deny(42, actor="test", operation="edit", source="test")
    await service.clean_stale_locks(older_than=cutoff)

    assert [build.id for build in repository.confirmed] == [existing_build.id]
    assert [build.id for build in repository.denied] == [existing_build.id]
    assert len(locks.acquisitions) == 2
    assert locks.releases == [42, 42]
    assert locks.cleanups == [cutoff]


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


async def test_prepare_for_account_does_not_require_a_discord_identity() -> None:
    repository = FakeBuildRepository()
    service = build_service(repository)
    draft_id = UUID("11111111-1111-1111-1111-111111111111")

    build = await service.prepare_for_account(
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
    assert repository.saved == []


async def test_get_by_source_submission_draft_id_returns_an_existing_build() -> None:
    draft_id = UUID("11111111-1111-4111-8111-111111111111")
    existing = OtherBuild(id=41, source_submission_draft_id=draft_id)

    result = await build_service(FakeBuildRepository(existing)).get_by_source_submission_draft_id(draft_id)

    assert result is not None
    assert result.id == existing.id


async def test_save_prepares_defaults_then_indexes_after_relational_persistence() -> None:
    repository = FakeBuildRepository()
    embeddings = FakeEmbeddings()
    locks = FakeBuildLocks()
    service = BuildService(
        locks,
        FakeRestrictionRepository(),
        FakeVersions(),
        embeddings,
        FakeTaxonomyResolver(),
        uow_factory=lambda: BuildScope(repository),
    )
    build = DoorBuild(id=42)
    repository.build = build.copy_for_edit()

    await service.save(build, actor="test", operation="edit", source="test")

    assert build.versions == []
    assert build.embedding == [1.0, 2.0]
    assert repository.saved == [build]
    assert locks.acquisitions == [(42, True, 30)]
    assert locks.releases == [42]
    assert embeddings.prepared == [build]
    assert embeddings.indexed == [build]


class TestAuthorizedEditing:
    """Pending submission owners can edit; other edits require the shared permission."""

    OWNER = BuildEditor(subject=Actor(account_id=1))
    STRANGER = BuildEditor(subject=Actor(account_id=2))

    @staticmethod
    def _permissions(*held: str) -> PermissionStore:
        class Store:
            async def load_for_subject(self, **_kwargs: object) -> SubjectRecords:
                return SubjectRecords(
                    epoch=1,
                    grants=tuple(PermissionGrant(pattern=node, effect=1, subject_account_id=2) for node in held),
                )

            async def epoch(self) -> int:
                return 1

        return Store()

    async def test_the_owner_may_edit_their_pending_build(self, existing_build: DoorBuild) -> None:
        repository = FakeBuildRepository(existing_build)
        service = build_service(repository, permissions=self._permissions())

        build = await service.apply_edit(self.OWNER, 42, BuildEditPatch(extra_user_info="mine"))

        assert build.description == "mine"
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

        assert build.description == "moderated"

    async def test_ownership_does_not_survive_confirmation(self, existing_build: DoorBuild) -> None:
        """Confirmation ends the submitter's permission to edit through ownership alone."""
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
        """A missing permission service must not silently authorize edits."""
        service = build_service(FakeBuildRepository(existing_build))

        with pytest.raises(InvalidStateError):
            await service.apply_edit(self.OWNER, 42, BuildEditPatch(extra_user_info="mine"))


async def test_permission_revoked_during_preparation_prevents_commit(existing_build: DoorBuild) -> None:
    class Permissions(EmptyPermissions):
        held = True

        async def load_for_subject(self, **_kwargs: object) -> SubjectRecords:
            return SubjectRecords(
                epoch=1,
                grants=(PermissionGrant(pattern="build.submission.edit", effect=1, subject_account_id=2),)
                if self.held
                else (),
            )

    permissions = Permissions()
    repository = FakeBuildRepository(existing_build)
    service = build_service(repository, permissions=permissions)

    class RevokeDuringPreparation(FakeEmbeddings):
        async def prepare(self, build: Build) -> None:
            permissions.held = False

    service._embeddings = RevokeDuringPreparation()
    with pytest.raises(AuthorizationError):
        await service.apply_edit(BuildEditor(Actor(account_id=2)), 42, BuildEditPatch(extra_user_info="denied"))
    assert repository.saved == []


async def test_unresolved_names_are_kept_until_an_official_tag_matches_them() -> None:
    build = DoorBuild(component_restrictions=["No Slime"], patterns=["Spiral"])

    await apply_build_taxonomy(build, FakeTaxonomyResolver())

    assert build.component_restrictions == []
    assert build.patterns == []
    assert build.extra_info == {
        "unknown_restrictions": {"component_restrictions": ["No Slime"]},
        "unknown_patterns": ["Spiral"],
    }

    # An unrelated edit keeps the names, and does not add Regular in place of the unresolved pattern.
    await apply_build_taxonomy(build, FakeTaxonomyResolver())
    assert build.patterns == []
    assert build.extra_info["unknown_patterns"] == ["Spiral"]

    resolver = FakeTaxonomyResolver(known_restrictions={"No Slime": "component"}, known_patterns=("Spiral",))
    await apply_build_taxonomy(build, resolver)

    assert build.component_restrictions == ["No Slime"]
    assert build.patterns == ["Spiral"]
    assert build.extra_info == {}


@pytest.mark.parametrize("status", [Status.CONFIRMED, Status.DENIED])
async def test_vote_outcome_leaves_an_existing_decision_unchanged(existing_build: DoorBuild, status: Status) -> None:
    existing_build.submission_status = status
    repository = FakeBuildRepository(existing_build)
    service = build_service(repository)
    await service.confirm(42, actor="vote_worker", source="vote_outcome")
    await service.deny(42, actor="vote_worker", source="vote_outcome")
    assert repository.confirmed == []
    assert repository.denied == []
    assert existing_build.submission_status is status


async def test_showcase_tags_are_replaced_by_key_keeping_existing_assignments(existing_build: DoorBuild) -> None:
    existing_build.tags = [
        TagAssignment(SHOWCASE_TAGS["speed"], Decimal(3)),
        TagAssignment(definition=SHOWCASE_TAGS["compact"], provenance="submitted"),
    ]
    repository = FakeBuildRepository(existing_build)

    result = await build_service(repository).edit(
        42, BuildEditPatch(showcase_tags=["tileable", "speed"]), actor="test", operation="edit", source="test"
    )

    showcase = [tag for tag in result.tags if tag.definition.semantic_kind is TagSemanticKind.SHOWCASE]
    assert [tag.definition.stable_key for tag in showcase] == ["tileable", "speed"]
    # A new assignment could not carry a value, so the kept value shows the old one survived.
    assert showcase[1].value == Decimal(3)
    # Restriction tags come from the build's names, which the patch did not touch.
    assert {tag.definition.display_name for tag in result.tags} >= {"Locational", "Directional with fixes"}


@pytest.mark.parametrize("key", ["unknown", "speed"])
async def test_a_showcase_tag_can_only_be_added_if_approved_and_valueless(existing_build: DoorBuild, key: str) -> None:
    repository = FakeBuildRepository(existing_build)

    with pytest.raises(InvalidBuildError, match=key):
        await build_service(repository).edit(
            42, BuildEditPatch(showcase_tags=[key]), actor="test", operation="edit", source="test"
        )
    assert repository.saved == []
