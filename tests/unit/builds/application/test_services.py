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

from squid.core.actors import ActingContext, Actor
from squid.core.builds.application import BuildEditPatch, RestrictionDefinition
from squid.core.builds.application.services import (
    BuildService,
)
from squid.core.builds.application.tags import TagResolution, apply_build_tags
from squid.core.builds.domain import BuildCategory, BuildStatus, ShowcaseTag, normalize_tag_name
from squid.core.builds.errors import (
    BuildBusyError,
    BuildEditValidationError,
    BuildNotFoundError,
    BuildRevisionMismatchError,
    InvalidBuildError,
)
from squid.core.builds.models import Build, DoorBuild, ExtenderBuild, OtherBuild, UtilityBuild
from squid.core.errors import AuthorizationError, InvalidStateError
from squid.core.messages.domain import MessageRef
from squid.core.permissions.application import PermissionService
from squid.core.permissions.application.ports import PermissionRepository, SubjectRecords
from squid.core.permissions.models import PermissionGrant
from squid.core.tags.domain import TagAuthority, TagModerationStatus, TagSemanticKind, TagValueType
from squid.core.tags.models import TagAssignment, TagDefinition
from tests.support.permissions import permission_uow_factory
from tests.support.unit_of_work import FakeBuildEditing, PublishingFakeUnitOfWork


class FakeBuildRepository(FakeBuildEditing):
    def __init__(self, build: Build | None = None) -> None:
        self.build = build
        self.saved: list[Build] = []
        self.confirmed: list[Build] = []
        self.denied: list[Build] = []
        self.events: list[str] = []

    async def save(self, build: Build) -> None:
        self.events.append("save")
        self.saved.append(build)

    async def set_status(self, build: Build, status: BuildStatus) -> None:
        build.submission_status = status

    async def approve(self, build: Build) -> None:
        self.confirmed.append(build)

    async def reject(self, build: Build) -> None:
        self.denied.append(build)

    async def get_for_update(self, build_id: int) -> Build | None:
        return await self.get_by_id(build_id)

    async def get_by_id(self, build_id: int) -> Build | None:
        if self.build is not None and self.build.id == build_id:
            return self.build
        return None

    async def get_many(self, build_ids: Sequence[int]) -> Sequence[Build]:
        return [self.build] if self.build is not None and self.build.id in build_ids else []

    async def get_by_source_submission_draft_id(self, draft_id: UUID) -> Build | None:
        if self.build is not None and self.build.source_submission_draft_id == draft_id:
            return self.build
        return None

    async def next_attachment_review(self, after: int) -> int | None:
        if self.build and self.build.id and self.build.id > after and self.build.extra_info.get("attachment_failures"):
            return self.build.id
        return None

    async def list_ids_for_source_message(self, message_id: int) -> Sequence[int]:
        # ORM identity columns may still be None before flush.
        if self.build is None or self.build.id is None:  # pyright: ignore[reportUnnecessaryComparison]
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
    async def load_for_subject(self, subject: Actor) -> SubjectRecords:
        return SubjectRecords(epoch=1, subject=subject)

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
    def __init__(self, repository: FakeBuildRepository, permissions: PermissionRepository | None = None) -> None:
        super().__init__(repository, clone=copy_repository)
        self.permissions = permissions or EmptyPermissions()

    @override
    async def commit(self) -> None:
        await super().commit()
        self.state.events.append("commit")

    @property
    @override
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
    async def newest(self, edition: Literal["Java", "Bedrock"], *, context: ActingContext) -> str:
        return f"{edition} 1.21.0"


class FakeTagResolver:
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

    async def resolve_tags(
        self,
        *,
        build_kind: str | None,
        restrictions: Sequence[str],
        patterns: Sequence[str],
        context: ActingContext,
    ) -> TagResolution:
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
        return TagResolution(
            assignments=tuple(assignments),
            unknown_restrictions=frozenset(unknown_restrictions),
            unknown_patterns=frozenset(unknown_patterns),
        )

    async def showcase_definitions(self, keys: Sequence[str], *, context: ActingContext) -> Sequence[TagDefinition]:
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
    "label": _showcase(2004, "label", TagValueType.TEXT),
}


def build_service(
    repository: FakeBuildRepository,
    locks: FakeBuildLocks | None = None,
    *,
    permissions: PermissionRepository | None = None,
) -> BuildService:
    return BuildService(
        locks or FakeBuildLocks(),
        FakeRestrictionRepository(),
        FakeVersions(),
        FakeTagResolver(),
        permissions=None if permissions is None else PermissionService(uow_factory=permission_uow_factory(permissions)),
        uow_factory=lambda: BuildScope(repository, permissions),
    )


@pytest.fixture
def existing_build() -> DoorBuild:
    return DoorBuild(
        id=42,
        submitter_account_id=1,
        submission_status=BuildStatus.PENDING,
        miscellaneous_restrictions=["Locational", "Directional with fixes", "Other"],
        description="old",
        server_address="old.example",
        server_coordinates="1 2 3",
    )


async def test_edit_prepares_a_detached_patch_then_commits(existing_build: DoorBuild) -> None:
    repository = FakeBuildRepository(existing_build)
    locks = FakeBuildLocks()
    service = build_service(repository, locks)
    result = await service.edit(
        42,
        BuildEditPatch(dimensions=(10, 11, 12), extra_user_info="changed"),
        operation="edit",
        source="test",
        context=ActingContext.worker("test"),
    )
    assert result.dimensions == (10, 11, 12)
    assert existing_build.dimensions == (None, None, None)
    assert repository.saved[0].description == "changed"
    assert locks.releases == [42]


async def test_edit_can_carry_forward_an_unchanged_invalid_door_dimension(existing_build: DoorBuild) -> None:
    existing_build.door_width = -1
    existing_build.door_height = 3
    repository = FakeBuildRepository(existing_build)
    service = build_service(repository)

    result = await service.edit(
        42,
        BuildEditPatch(door_dimensions=(-1, 4, None)),
        source="test",
        context=ActingContext.worker("test"),
    )
    assert isinstance(result, DoorBuild)
    assert result.door_dimensions == (-1, 4, None)

    with pytest.raises(BuildEditValidationError) as error:
        await service.edit(
            42,
            BuildEditPatch(door_dimensions=(-2, 3, None)),
            source="test",
            context=ActingContext.worker("test"),
        )
    assert error.value.issues == {"opening_width": "below_minimum"}

    fixed = await service.edit(
        42,
        BuildEditPatch(door_dimensions=(2, 3, None)),
        source="test",
        context=ActingContext.worker("test"),
    )
    assert isinstance(fixed, DoorBuild)
    assert fixed.door_dimensions == (2, 3, None)
    assert repository.saved == [result, fixed]


@pytest.mark.parametrize(
    ("patch", "issues"),
    [
        (BuildEditPatch(extension_length=-1), {"extension_length": "below_minimum"}),
        (BuildEditPatch(extension_length=513), {"extension_length": "above_maximum"}),
        (BuildEditPatch(retraction_time=-1), {"retraction_time": "below_minimum"}),
    ],
)
async def test_edit_rejects_new_extender_issues(patch: BuildEditPatch, issues: dict[str, str]) -> None:
    repository = FakeBuildRepository(ExtenderBuild(id=42))
    with pytest.raises(BuildEditValidationError) as error:
        await build_service(repository).edit(42, patch, source="test", context=ActingContext.worker("test"))
    assert error.value.issues == issues
    assert repository.saved == []


async def test_invalid_patch_does_not_acquire_a_write_lock(existing_build: DoorBuild) -> None:
    repository = FakeBuildRepository(existing_build)
    locks = FakeBuildLocks()
    invalid_dimensions = cast(tuple[int | None, int | None, int | None], (1, 2))
    with pytest.raises(ValueError, match="not enough values to unpack"):
        await build_service(repository, locks).edit(
            42,
            BuildEditPatch(dimensions=invalid_dimensions),
            operation="edit",
            source="test",
            context=ActingContext.worker("test"),
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
            partial(service.edit, context=ActingContext.worker("test"), operation="edit", source="test"),
            42,
            BuildEditPatch(),
        )
        await loading.wait()
        tasks.cancel_scope.cancel()
    assert locks.acquisitions == []


async def test_edit_reports_missing_and_busy_builds(existing_build: DoorBuild) -> None:
    with pytest.raises(BuildNotFoundError):
        await build_service(FakeBuildRepository()).edit(
            42, BuildEditPatch(), operation="edit", source="test", context=ActingContext.worker("test")
        )
    locks = FakeBuildLocks()
    locks.acquire_result = False
    with pytest.raises(BuildBusyError):
        await build_service(FakeBuildRepository(existing_build), locks).edit(
            42, BuildEditPatch(), operation="edit", source="test", context=ActingContext.worker("test")
        )


async def test_edit_rechecks_revision_after_preparation(
    existing_build: DoorBuild, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = FakeBuildRepository(existing_build)
    service = build_service(repository)

    prepare = service._prepare_for_persistence

    async def prepare_and_change(build: Build) -> None:
        await prepare(build)
        existing_build.revision += 1

    monkeypatch.setattr(service, "_prepare_for_persistence", prepare_and_change)
    with pytest.raises(BuildRevisionMismatchError):
        await service.edit(
            42,
            BuildEditPatch(extra_user_info="stale"),
            expected_revision=1,
            operation="edit",
            source="test",
            context=ActingContext.worker("test"),
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
            operation="edit",
            source="test",
            context=ActingContext.worker("test"),
        )
    assert error.value.public_context == {"build_id": 42, "expected_revision": 2, "current_revision": 1}
    assert locks.acquisitions == []
    assert existing_build.dimensions == (None, None, None)


async def test_status_changes_and_cleanup_use_lock_manager(existing_build: DoorBuild) -> None:
    repository = FakeBuildRepository(existing_build)
    locks = FakeBuildLocks()
    service = build_service(repository, locks)
    cutoff = Instant.from_utc(2026, 7, 29)

    await service.approve(
        42, operation="edit", source="test", context=ActingContext.worker("events.core.vote_session.closed")
    )
    await service.reject(
        42, operation="edit", source="test", context=ActingContext.worker("events.core.vote_session.closed")
    )
    await service.clean_stale_locks(older_than=cutoff, context=ActingContext.worker("test-worker"))

    assert [build.id for build in repository.confirmed] == [existing_build.id]
    assert [build.id for build in repository.denied] == [existing_build.id]
    assert len(locks.acquisitions) == 2
    assert locks.releases == [42, 42]
    assert locks.cleanups == [cutoff]


async def test_classify_restrictions_replaces_existing_values_without_persisting() -> None:
    repository = FakeBuildRepository()
    service = build_service(repository)
    build = UtilityBuild(component_restrictions=["Old"], miscellaneous_restrictions=["Old"])

    result = await service.classify_restrictions(build, ["seamless", "unknown"], context=ActingContext.direct(Actor()))

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
        context=ActingContext.direct(Actor(account_id=17)),
    )

    assert build.submitter_account_id == 17
    assert build.submitter_discord_id is None
    assert build.source_submission_draft_id == draft_id
    assert build.display_name == "Workshop prototype"
    assert build.category is BuildCategory.OTHER
    assert build.submission_status is BuildStatus.PENDING
    assert repository.saved == []


async def test_get_by_source_submission_draft_id_returns_an_existing_build() -> None:
    draft_id = UUID("11111111-1111-4111-8111-111111111111")
    existing = OtherBuild(id=41, source_submission_draft_id=draft_id)

    result = await build_service(FakeBuildRepository(existing)).get_by_source_submission_draft_id(
        draft_id, context=ActingContext.worker("submission-finalization")
    )

    assert result is not None
    assert result.id == existing.id


async def test_save_commits_without_waiting_for_an_embedding() -> None:
    repository = FakeBuildRepository()
    locks = FakeBuildLocks()
    service = BuildService(
        locks,
        FakeRestrictionRepository(),
        FakeVersions(),
        FakeTagResolver(),
        uow_factory=lambda: BuildScope(repository),
    )
    build = DoorBuild(id=42)
    repository.build = build.copy_for_edit()

    await service.save(build, operation="edit", source="test", context=ActingContext.worker("test"))

    assert build.versions == []
    assert build.embedding is None
    assert repository.saved == [build]
    assert locks.acquisitions == [(42, True, 30)]
    assert locks.releases == [42]
    assert repository.events == ["save", "commit"]


class TestAuthorizedEditing:
    """Pending submission owners can edit; other edits require the shared permission."""

    OWNER = Actor(account_id=1)
    STRANGER = Actor(account_id=2)

    @staticmethod
    def _permissions(*held: str) -> PermissionRepository:
        class Store:
            async def load_for_subject(self, subject: Actor) -> SubjectRecords:
                return SubjectRecords(
                    epoch=1,
                    grants=tuple(PermissionGrant(pattern=node, effect=1, subject_account_id=2) for node in held),
                    subject=subject,
                )

            async def epoch(self) -> int:
                return 1

        return Store()

    @pytest.mark.parametrize(
        ("actor", "granted", "visible"),
        [
            (Actor(), "", False),
            (Actor(account_id=1), "", True),
            (Actor(account_id=2), "", False),
            (Actor(account_id=2), "build.submission.edit", True),
            (Actor(account_id=2), "build.submission.view_pending", True),
            (Actor(account_id=2, credential_scopes=frozenset()), "build.submission.edit", False),
            (Actor(account_id=1, credential_scopes=frozenset({"account.self.read"})), "", False),
            (Actor(worker_name="submission-finalization"), "", True),
        ],
    )
    async def test_private_build_lookups_share_visibility(self, actor: Actor, granted: str, visible: bool) -> None:
        draft_id = UUID(int=12)
        build = OtherBuild(
            id=42,
            submission_status=BuildStatus.PENDING,
            submitter_account_id=1,
            source_submission_draft_id=draft_id,
            source_messages=(MessageRef(id=9),),
        )
        service = build_service(
            FakeBuildRepository(build), permissions=self._permissions(*([granted] if granted else []))
        )
        context = ActingContext.direct(actor)
        assert (await service.get(42, context=context) is not None) is visible
        assert (await service.get_by_source_submission_draft_id(draft_id, context=context) is not None) is visible
        assert list(await service.list_ids_for_source_message(9, context=context)) == ([42] if visible else [])

    async def test_attachment_review_requires_edit_authority(self) -> None:
        build = OtherBuild(
            id=7,
            submission_status=BuildStatus.APPROVED,
            extra_info={
                "attachment_failures": [
                    {
                        "attachment_id": "1",
                        "filename": "example.litematic",
                        "stage": "download",
                        "detail": "missing file",
                    }
                ]
            },
        )
        repository = FakeBuildRepository(build)
        service = build_service(repository, permissions=self._permissions("build.submission.edit"))
        unprivileged = build_service(repository, permissions=self._permissions())
        with pytest.raises(AuthorizationError):
            await unprivileged.next_attachment_review(0, context=ActingContext.direct(self.OWNER))
        assert await service.next_attachment_review(0, context=ActingContext.direct(self.STRANGER)) == 7

    @pytest.mark.parametrize("outcome", ["approve", "reject"])
    async def test_moderation_requires_its_grant_or_closed_vote_delivery(
        self, existing_build: DoorBuild, outcome: str
    ) -> None:
        repository = FakeBuildRepository(existing_build)
        service = build_service(repository, permissions=self._permissions())
        for context in (ActingContext.direct(self.STRANGER), ActingContext.worker("unrelated")):
            with pytest.raises(AuthorizationError):
                await getattr(service, outcome)(42, source="vote_outcome", context=context)
        assert repository.confirmed == []
        assert repository.denied == []
        granted = build_service(repository, permissions=self._permissions(f"build.submission.{outcome}"))
        await getattr(granted, outcome)(42, source="moderator", context=ActingContext.direct(self.STRANGER))
        assert repository.confirmed or repository.denied

    async def test_the_owner_may_edit_their_pending_build(self, existing_build: DoorBuild) -> None:
        repository = FakeBuildRepository(existing_build)
        service = build_service(repository, permissions=self._permissions())

        build = await service.apply_edit(
            42, BuildEditPatch(extra_user_info="mine"), context=ActingContext.for_actor(self.OWNER)
        )

        assert build.description == "mine"
        assert len(repository.saved) == 1

    async def test_a_non_owner_without_the_node_is_refused_without_committing(self, existing_build: DoorBuild) -> None:
        repository = FakeBuildRepository(existing_build)
        service = build_service(repository, permissions=self._permissions())

        with pytest.raises(BuildNotFoundError):
            await service.apply_edit(
                42, BuildEditPatch(extra_user_info="theirs"), context=ActingContext.for_actor(self.STRANGER)
            )

        assert repository.saved == []

    async def test_a_non_owner_holding_the_node_may_edit(self, existing_build: DoorBuild) -> None:
        repository = FakeBuildRepository(existing_build)
        service = build_service(repository, permissions=self._permissions("build.submission.edit"))

        build = await service.apply_edit(
            42, BuildEditPatch(extra_user_info="moderated"), context=ActingContext.for_actor(self.STRANGER)
        )

        assert build.description == "moderated"

    async def test_a_creator_can_edit_published_description(self, existing_build: DoorBuild) -> None:
        existing_build.submission_status = BuildStatus.APPROVED
        existing_build.credited_creator_account_ids = frozenset({2})
        await apply_build_tags(existing_build, FakeTagResolver(), context=ActingContext.direct(Actor()))
        repository = FakeBuildRepository(existing_build)
        service = build_service(repository, permissions=self._permissions())

        build = await service.apply_edit(
            42, BuildEditPatch(extra_user_info="updated"), context=ActingContext.for_actor(self.STRANGER)
        )
        assert build.description == "updated"

    async def test_a_creator_cannot_directly_change_published_dimensions(self, existing_build: DoorBuild) -> None:
        existing_build.submission_status = BuildStatus.APPROVED
        repository = FakeBuildRepository(existing_build)
        service = build_service(repository, permissions=self._permissions())

        with pytest.raises(AuthorizationError):
            await service.apply_edit(
                42, BuildEditPatch(dimensions=(1, 2, 3)), context=ActingContext.for_actor(self.OWNER)
            )

        assert repository.saved == []

    async def test_a_missing_build_is_reported_before_authorization(self) -> None:
        service = build_service(FakeBuildRepository(), permissions=self._permissions())

        with pytest.raises(BuildNotFoundError):
            await service.apply_edit(
                42, BuildEditPatch(extra_user_info="mine"), context=ActingContext.for_actor(self.OWNER)
            )

    async def test_authorized_editing_requires_a_permission_service(self, existing_build: DoorBuild) -> None:
        """A missing permission service must not silently authorize edits."""
        service = build_service(FakeBuildRepository(existing_build))

        with pytest.raises(InvalidStateError):
            await service.apply_edit(
                42, BuildEditPatch(extra_user_info="mine"), context=ActingContext.for_actor(self.OWNER)
            )


async def test_permission_revoked_during_preparation_prevents_commit(
    existing_build: DoorBuild, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Permissions(EmptyPermissions):
        held = True

        @override
        async def load_for_subject(self, subject: Actor) -> SubjectRecords:
            return SubjectRecords(
                epoch=1,
                grants=(PermissionGrant(pattern="build.submission.edit", effect=1, subject_account_id=2),)
                if self.held
                else (),
                subject=subject,
            )

    permissions = Permissions()
    repository = FakeBuildRepository(existing_build)
    service = build_service(repository, permissions=permissions)

    prepare = service._prepare_for_persistence

    async def prepare_and_change(build: Build) -> None:
        await prepare(build)
        permissions.held = False

    monkeypatch.setattr(service, "_prepare_for_persistence", prepare_and_change)
    with pytest.raises(AuthorizationError):
        await service.apply_edit(
            42, BuildEditPatch(extra_user_info="denied"), context=ActingContext.for_actor(Actor(account_id=2))
        )
    assert repository.saved == []


async def test_unresolved_names_are_kept_until_an_official_tag_matches_them() -> None:
    build = DoorBuild(component_restrictions=["No Slime"], patterns=["Spiral"])

    await apply_build_tags(build, FakeTagResolver(), context=ActingContext.direct(Actor()))

    assert build.component_restrictions == []
    assert build.patterns == []
    assert build.extra_info == {
        "unknown_restrictions": {"component_restrictions": ["No Slime"]},
        "unknown_patterns": ["Spiral"],
    }

    # An unrelated edit keeps the names, and does not add Regular in place of the unresolved pattern.
    await apply_build_tags(build, FakeTagResolver(), context=ActingContext.direct(Actor()))
    assert build.patterns == []
    assert build.extra_info.get("unknown_patterns") == ["Spiral"]

    resolver = FakeTagResolver(known_restrictions={"No Slime": "component"}, known_patterns=("Spiral",))
    await apply_build_tags(build, resolver, context=ActingContext.direct(Actor()))

    assert build.component_restrictions == ["No Slime"]
    assert build.patterns == ["Spiral"]
    assert build.extra_info == {}


@pytest.mark.parametrize("status", [BuildStatus.APPROVED, BuildStatus.REJECTED])
async def test_vote_outcome_leaves_an_existing_decision_unchanged(
    existing_build: DoorBuild, status: BuildStatus
) -> None:
    existing_build.submission_status = status
    repository = FakeBuildRepository(existing_build)
    service = build_service(repository)
    await service.approve(42, source="vote_outcome", context=ActingContext.worker("events.core.vote_session.closed"))
    await service.reject(42, source="vote_outcome", context=ActingContext.worker("events.core.vote_session.closed"))
    assert repository.confirmed == []
    assert repository.denied == []
    assert existing_build.submission_status is status


async def test_showcase_tags_are_replaced_keeping_existing_assignments(existing_build: DoorBuild) -> None:
    existing_build.tags = [
        TagAssignment(SHOWCASE_TAGS["speed"], Decimal(3), display_unit="ticks"),
        TagAssignment(definition=SHOWCASE_TAGS["compact"], origin="submitted"),
    ]
    repository = FakeBuildRepository(existing_build)

    result = await build_service(repository).edit(
        42,
        BuildEditPatch(showcase_tags=[ShowcaseTag("tileable"), ShowcaseTag("speed", "3.0")]),
        operation="edit",
        source="test",
        context=ActingContext.worker("test"),
    )

    showcase = [tag for tag in result.tags if tag.definition.semantic_kind is TagSemanticKind.SHOWCASE]
    assert [(tag.definition.stable_key, tag.value) for tag in showcase] == [("tileable", None), ("speed", Decimal(3))]
    # "3.0" is the stored value, so the assignment is kept as it was.
    assert showcase[1].display_unit == "ticks"
    # Restriction tags come from the build's names, which the patch did not touch.
    assert {tag.definition.display_name for tag in result.tags} >= {"Locational", "Directional with fixes"}


async def test_showcase_tags_can_be_added_with_values_and_have_values_changed(existing_build: DoorBuild) -> None:
    existing_build.tags = [TagAssignment(SHOWCASE_TAGS["speed"], Decimal(3), origin="moderated")]
    repository = FakeBuildRepository(existing_build)
    service = build_service(repository)

    added = await service.edit(
        42,
        BuildEditPatch(showcase_tags=[ShowcaseTag("speed", "4.5"), ShowcaseTag("label", "Fast")]),
        operation="edit",
        source="test",
        context=ActingContext.worker("test"),
    )

    showcase = [tag for tag in added.tags if tag.definition.semantic_kind is TagSemanticKind.SHOWCASE]
    assert [(tag.definition.stable_key, tag.value, tag.origin) for tag in showcase] == [
        ("speed", Decimal("4.5"), "moderated"),
        ("label", "Fast", "submitted"),
    ]


@pytest.mark.parametrize(
    ("tag", "rejected"),
    [
        (ShowcaseTag("unknown"), "unknown"),
        (ShowcaseTag("speed"), "speed"),
        (ShowcaseTag("speed", "fast"), "speed"),
        (ShowcaseTag("compact", "1"), "compact"),
    ],
)
async def test_showcase_tags_need_an_approved_key_and_a_value_that_suits_it(
    existing_build: DoorBuild, tag: ShowcaseTag, rejected: str
) -> None:
    repository = FakeBuildRepository(existing_build)

    with pytest.raises(InvalidBuildError) as error:
        await build_service(repository).edit(
            42,
            BuildEditPatch(showcase_tags=[tag]),
            operation="edit",
            source="test",
            context=ActingContext.worker("test"),
        )
    assert error.value.public_context == {"showcase_tags": [rejected]}
    assert repository.saved == []
