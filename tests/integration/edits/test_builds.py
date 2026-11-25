"""Build receipts preserve audit history and fence every aggregate change."""

from decimal import Decimal
from functools import partial
from unittest.mock import AsyncMock

import anyio
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.config import EMBEDDING_DIMENSION
from squid.core.accounts.models import Account, AccountIdentity
from squid.core.actors import ActingContext, AnonymousActor, UserActor
from squid.core.builds.application import BuildEditPatch, BuildService
from squid.core.builds.application.category_edits import BUILD_CATEGORY_EDIT, BuildCategoryEdit
from squid.core.builds.application.edits import (
    BUILD_EDIT,
    BUILD_STATUS_EDIT,
    BuildFieldsEdit,
    BuildStatusEdit,
    build_target,
    build_values,
    patch_values,
)
from squid.core.builds.application.locks import BuildLockService
from squid.core.builds.application.restrictions import RestrictionService
from squid.core.builds.application.tag_service import BuildTagService
from squid.core.builds.domain import BuildStatus, ShowcaseTag, UnresolvedTags
from squid.core.builds.domain.credits import Credit, CreditKind, CreditRole
from squid.core.builds.errors import BuildNotFoundError
from squid.core.builds.history_models import BuildHistoryEntry
from squid.core.builds.models import Build, DoorBuild, ExtenderBuild, OtherBuild
from squid.core.edits.application import EditExecutor
from squid.core.edits.domain import EditCommand, EditResult, EditValues, LatestRevision, Revision, RevisionConflictError
from squid.core.errors import AuthorizationError
from squid.core.events import DomainEvent
from squid.core.permissions.application.services import PermissionService
from squid.core.permissions.domain.catalogue import BUILD_SUBMISSION_EDIT, BUILD_SUBMISSION_RECLASSIFY
from squid.core.permissions.models import PermissionGrant
from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.core.records.models import DoorTimingVariant
from squid.core.tags.domain import TagAuthority, TagModerationStatus, TagValueType
from squid.core.versions.application import VersionService
from squid.core.voting.application.services import VoteService
from squid.core.voting.domain import BuildEditVoteTarget, VoteChoice, VoteKind, VoteSessionResult
from squid.worker.events import ApplyBuildVoteOutcomeHandler
from tests.support.build_edits import build_edit_session
from tests.support.submission_targets import seed_account_and_version


async def setup(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[BuildService, EditExecutor[SqlAlchemyUnitOfWork], UserActor, Build]:
    account_id = await seed_account_and_version(sessions)
    factory = partial(SqlAlchemyUnitOfWork, sessions)
    service = BuildService(
        BuildLockService(uow_factory=factory),
        RestrictionService(uow_factory=factory),
        VersionService(uow_factory=factory),
        BuildTagService(uow_factory=factory),
        uow_factory=factory,
        permissions=PermissionService(uow_factory=factory),
    )
    build = OtherBuild(
        submitter_account_id=account_id,
        submission_status=BuildStatus.PENDING,
        versions=["Java 1.21.0"],
        description="original",
    )
    async with factory() as uow, uow.builds.editing(actor=account_id, source="test"):
        await uow.builds.save(build)
        await uow.commit()
    return (
        service,
        EditExecutor(factory, {BUILD_EDIT: BuildFieldsEdit(service)}),
        UserActor(account_id=account_id),
        build,
    )


async def test_moderator_changes_category_in_place_and_archives_old_timing_variants(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    factory = partial(SqlAlchemyUnitOfWork, sessions)
    account_id = await seed_account_and_version(sessions)
    actor = UserActor(account_id=account_id)
    edits = EditExecutor(factory, {BUILD_CATEGORY_EDIT: BuildCategoryEdit(PermissionService(uow_factory=factory))})
    door = DoorBuild(
        submitter_account_id=account_id,
        submission_status=BuildStatus.APPROVED,
        width=9,
        door_width=3,
        door_height=4,
        credits=[Credit(name="Builder")],
        versions=["Java 1.21.0"],
        display_name="Example",
    )
    async with factory() as uow, uow.builds.editing(actor=account_id, source="test"):
        await uow.builds.save(door)
        await uow.commit()
    async with sessions.begin() as session:
        session.add(DoorTimingVariant(build_id=door.id, label="parallel", opening_time=12))

    target = build_target(door.id)
    request = EditValues.of({"category": "Extender", "extender": {"extension_length": 5}})
    command = EditCommand(BUILD_CATEGORY_EDIT, (Revision(target, door.revision),), request)
    with pytest.raises(AuthorizationError):
        await edits.execute(command, context=ActingContext.direct(actor))

    async with sessions.begin() as session:
        session.add(PermissionGrant(pattern=BUILD_SUBMISSION_RECLASSIFY.name, effect=1, subject_account_id=account_id))

    result = await edits.execute(command, context=ActingContext.direct(actor))
    assert result.outcome.final
    assert (await edits.execute(command, context=ActingContext.direct(actor))).replayed
    assert result.outcome.revisions == (Revision(target, door.revision + 1),)
    with pytest.raises(RevisionConflictError):
        await edits.execute(
            EditCommand(BUILD_CATEGORY_EDIT, command.expected, request), context=ActingContext.direct(actor)
        )
    async with factory() as uow:
        extender = await uow.builds.get_by_id(door.id)
        assert isinstance(extender, ExtenderBuild)
        assert extender.extension_length == 5
        assert extender.orientation is None
        assert extender.width == 9
        assert extender.credits == door.credits
        assert extender.versions == door.versions
        assert extender.display_name == door.display_name
        history = await uow.build_history.list_history(door.id)
        latest = history.items[0]
        entry = await uow.build_history.get_history_entry(door.id, latest.id)
        assert entry is not None
        assert entry.before is not None
        assert entry.after is not None
        assert entry.before.payload["doors"] is not None
        assert entry.after.payload["doors"] is None
        variants = entry.before.payload["door_timing_variants"]
        assert isinstance(variants, list)
        assert isinstance(variants[0], dict)
        assert variants[0]["opening_time"] == 12
        assert "door_timing_variants" not in entry.after.payload


async def test_credited_creator_proposes_published_changes_and_vote_applies_the_exact_patch(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    service, executor, owner, build = await setup(migrated_session_factory)
    async with migrated_session_factory.begin() as session:
        creator = Account()
        session.add(creator)
        await session.flush()
        identity = AccountIdentity.discord(123)
        identity.account_id = creator.id
        session.add(identity)
    await service.edit(
        build.id,
        BuildEditPatch(credits=[Credit(name="Creator", kind=CreditKind.DISCORD, subject="123")]),
        source="test",
        context=ActingContext.worker("test"),
    )
    await service.approve(build.id, source="test", context=ActingContext.worker("events.core.vote_session.closed"))
    actor = UserActor(account_id=creator.id)
    current = await service.get(build.id, context=ActingContext.direct(AnonymousActor()))
    assert current is not None
    assert current.is_creator(actor.account_id)
    target = build_target(build.id)

    with pytest.raises(AuthorizationError):
        await executor.execute(
            EditCommand(BUILD_EDIT, (LatestRevision(target),), patch_values(BuildEditPatch(credits=[]))),
            context=ActingContext.direct(owner),
        )

    command = EditCommand(
        BUILD_EDIT,
        (Revision(target, current.revision),),
        patch_values(BuildEditPatch(dimensions=(1, 2, 3), extra_user_info="reviewed")),
    )
    result = await executor.execute(command, context=ActingContext.direct(actor))
    vote_id = result.outcome.review_vote_id
    assert vote_id is not None
    assert not result.outcome.changed
    assert (await executor.execute(command, context=ActingContext.direct(actor))).outcome.review_vote_id == vote_id
    published = await service.get(build.id, context=ActingContext.direct(AnonymousActor()))
    assert published is not None
    assert published.description == "original"
    assert published.revision == current.revision
    snapshot = await VoteService(uow_factory=factory).get_session(
        vote_id, context=ActingContext.direct(AnonymousActor())
    )
    assert snapshot is not None
    assert snapshot.kind is VoteKind.BUILD_EDIT
    assert isinstance(snapshot.target, BuildEditVoteTarget)
    async with factory() as uow:
        option = next(option for option in snapshot.options if option.choice is VoteChoice.APPROVE)
        assert owner.account_id is not None
        mutation = await uow.votes.cast_vote(vote_id, owner.account_id, 1, option, 3)
        assert mutation is not None
        await uow.commit()
    closed = await VoteService(uow_factory=factory).get_session(vote_id, context=ActingContext.direct(AnonymousActor()))
    assert closed is not None
    assert closed.result is VoteSessionResult.APPROVED
    handler = ApplyBuildVoteOutcomeHandler(VoteService(uow_factory=factory), service)
    event = DomainEvent(
        id=1,
        event_type="vote_session.closed",
        aggregate_kind="vote_session",
        aggregate_id=vote_id,
        occurred_at=Instant.now(),
    )
    with ActingContext.worker("events.core.vote_session.closed").scope():
        await handler.handle(event)
        await handler.handle(event)
    updated = await service.get(build.id, context=ActingContext.direct(AnonymousActor()))
    assert updated is not None
    assert updated.dimensions == (1, 2, 3)
    assert updated.description == "reviewed"
    assert updated.submission_status is BuildStatus.APPROVED
    async with migrated_session_factory() as session:
        applications = await session.scalars(
            select(BuildHistoryEntry).where(
                BuildHistoryEntry.correlation_kind == "vote_session",
                BuildHistoryEntry.correlation_id == str(vote_id),
            )
        )
        assert len(applications.all()) == 1


@pytest.mark.parametrize("latest", [False, True])
async def test_build_receipts_restore_children_and_unresolved_tags_in_one_audit_entry(
    latest: bool,
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    service, executor, actor, build = await setup(sessions)
    assert build.id is not None
    before = build_values(build)
    result = await executor.execute(
        EditCommand(
            BUILD_EDIT,
            (LatestRevision(build_target(build.id)) if latest else Revision(build_target(build.id), build.revision),),
            patch_values(
                BuildEditPatch(
                    extra_user_info="edited",
                    image_urls=["https://example.test/image.png"],
                    component_restrictions=["Previously unknown restriction"],
                )
            ),
        ),
        context=ActingContext.direct(actor),
    )
    assert result.receipt is not None
    current = await service.get(build.id, context=ActingContext.direct(actor))
    assert current is not None
    assert current.revision == result.outcome.revisions[0].value
    assert UnresolvedTags.read(current.extra_info).restriction_names() == ("Previously unknown restriction",)
    undone = await executor.execute(result.receipt.undo(result.outcome.revisions), context=ActingContext.direct(actor))
    assert undone.receipt is not None
    restored = await service.get(build.id, context=ActingContext.direct(actor))
    assert restored is not None
    assert build_values(restored) == before
    redone = await executor.execute(result.receipt.redo(undone.outcome.revisions), context=ActingContext.direct(actor))
    assert redone.receipt is not None
    assert redone.receipt.after == result.receipt.after
    assert redone.outcome.revisions[0].value > undone.outcome.revisions[0].value > result.outcome.revisions[0].value
    async with sessions() as session:
        entries = (await session.scalars(select(BuildHistoryEntry).order_by(BuildHistoryEntry.id))).all()
        assert len(entries) == 4
        assert entries[1].correlation_kind == "edit_operation"
        assert entries[1].correlation_id == str(result.outcome.operation_id)
    replay = await executor.execute(result.receipt.command, context=ActingContext.direct(actor))
    assert replay.replayed
    assert replay.outcome == result.outcome


async def test_unofficial_component_restriction_round_trips_through_build_edits(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with migrated_session_factory.begin() as session:
        tag_id = await session.scalar(
            text("""
            INSERT INTO tag_definitions (
                stable_key, display_name, normalized_name, authority, semantic_kind, restriction_type,
                value_type, render_template, default_display_order, moderation_status
            ) VALUES ('cracked', 'Cracked', 'cracked', 'user', 'restriction', 'component', 'none', '{name}', 0, 'approved')
            RETURNING id
        """)
        )
        await session.execute(
            text("INSERT INTO tag_applicabilities (tag_id, build_kind) VALUES (:id, 'Other')"), {"id": tag_id}
        )
    service, executor, actor, build = await setup(migrated_session_factory)
    assert build.id is not None
    target = LatestRevision(build_target(build.id))
    for names in (["Cracked"], []):
        await executor.execute(
            EditCommand(BUILD_EDIT, (target,), patch_values(BuildEditPatch(component_restrictions=names))),
            context=ActingContext.direct(actor),
        )
        current = await service.get(build.id, context=ActingContext.direct(actor))
        assert current is not None
        assert current.component_restrictions == names
        assert not UnresolvedTags.read(current.extra_info).restrictions
        assert [assignment.definition.authority for assignment in current.tags] == (
            [TagAuthority.USER] if names else []
        )


async def test_competing_build_edits_and_unrelated_child_write_fence_undo(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    service, executor, actor, build = await setup(sessions)
    assert build.id is not None
    expected = (Revision(build_target(build.id), build.revision),)
    results: list[EditResult | RevisionConflictError] = []

    async def edit(patch: BuildEditPatch) -> None:
        try:
            results.append(
                await executor.execute(
                    EditCommand(BUILD_EDIT, expected, patch_values(patch)), context=ActingContext.direct(actor)
                )
            )
        except RevisionConflictError as error:
            results.append(error)

    async with anyio.create_task_group() as group:
        group.start_soon(edit, BuildEditPatch(extra_user_info="edited"))
        group.start_soon(edit, BuildEditPatch(image_urls=["https://example.test/image.png"]))
    assert sum(isinstance(value, RevisionConflictError) for value in results) == 1
    saved = next(value for value in results if isinstance(value, EditResult))
    assert saved.receipt is not None
    async with build_edit_session(sessions) as session:
        await session.execute(
            text(
                "INSERT INTO build_links (build_id, url, media_type) VALUES (:id, 'https://example.test/other.png', 'image')"
            ),
            {"id": build.id},
        )
    with pytest.raises(RevisionConflictError):
        await executor.execute(saved.receipt.undo(saved.outcome.revisions), context=ActingContext.direct(actor))
    current = await service.get(build.id, context=ActingContext.direct(actor))
    assert current is not None
    async with sessions.begin() as session:
        other = Account()
        session.add(other)
        await session.flush()
        outsider = UserActor(account_id=other.id)
    assert await service.get(build.id, context=ActingContext.direct(outsider)) is None
    with pytest.raises(BuildNotFoundError):
        await executor.execute(
            saved.receipt.undo((Revision(build_target(build.id), current.revision),)),
            context=ActingContext.direct(outsider),
        )


async def test_preparation_does_not_lock_the_build_and_rechecks_after_another_commit(
    monkeypatch: pytest.MonkeyPatch,
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    entered = anyio.Event()
    resume = anyio.Event()

    sessions = migrated_session_factory
    service, executor, actor, build = await setup(sessions)
    prepare = service.prepare_edit

    async def pause(build: Build, patch: BuildEditPatch, *, context: ActingContext) -> Build:
        result = await prepare(build, patch, context=context)
        entered.set()
        await resume.wait()
        return result

    monkeypatch.setattr(service, "prepare_edit", pause)
    assert build.id is not None
    conflicted: list[RevisionConflictError] = []

    async def edit() -> None:
        try:
            await executor.execute(
                EditCommand(
                    BUILD_EDIT,
                    (Revision(build_target(build.id or 0), build.revision),),
                    patch_values(BuildEditPatch(extra_user_info="stale")),
                ),
                context=ActingContext.direct(actor),
            )
        except RevisionConflictError as error:
            conflicted.append(error)

    with anyio.fail_after(10):
        async with anyio.create_task_group() as group:
            group.start_soon(edit)
            await entered.wait()
            async with (
                SqlAlchemyUnitOfWork(sessions) as uow,
                uow.builds.editing(actor=actor.account_id or 0, source="test"),
            ):
                current = await uow.builds.get_for_update(build.id)
                assert current is not None
                current.description = "newer"
                await uow.builds.save(current)
                await uow.commit()
            resume.set()
    assert len(conflicted) == 1
    current = await service.get(build.id, context=ActingContext.direct(actor))
    assert current is not None
    assert current.description == "newer"


async def test_review_undo_redo_preserves_history_and_rejects_stale_decisions(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    service, _, actor, build = await setup(sessions)
    assert build.id is not None
    permissions = AsyncMock(spec=PermissionService)
    permissions.require.return_value = None
    executor = EditExecutor(partial(SqlAlchemyUnitOfWork, sessions), {BUILD_STATUS_EDIT: BuildStatusEdit(permissions)})
    command = EditCommand(
        BUILD_STATUS_EDIT,
        (Revision(build_target(build.id), build.revision),),
        EditValues.of({"submission_status": BuildStatus.APPROVED}),
    )
    result = await executor.execute(command, context=ActingContext.direct(actor))
    assert result.receipt is not None
    current = await service.get(build.id, context=ActingContext.direct(actor))
    assert current is not None
    assert current.submission_status is BuildStatus.APPROVED
    with pytest.raises(RevisionConflictError):
        await executor.execute(
            EditCommand(
                BUILD_STATUS_EDIT, command.expected, EditValues.of({"submission_status": BuildStatus.REJECTED})
            ),
            context=ActingContext.direct(actor),
        )
    undone = await executor.execute(result.receipt.undo(result.outcome.revisions), context=ActingContext.direct(actor))
    current = await service.get(build.id, context=ActingContext.direct(actor))
    assert current is not None
    assert current.submission_status is BuildStatus.PENDING
    await executor.execute(result.receipt.redo(undone.outcome.revisions), context=ActingContext.direct(actor))
    current = await service.get(build.id, context=ActingContext.direct(actor))
    assert current is not None
    assert current.submission_status is BuildStatus.APPROVED
    async with sessions() as session:
        entries = list(
            (await session.scalars(select(BuildHistoryEntry).where(BuildHistoryEntry.build_id == build.id))).all()
        )
        assert len(entries) == 4


async def test_typed_credit_edit_undo_and_redo_preserve_identity_and_role(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    service, executor, actor, build = await setup(migrated_session_factory)
    assert build.id is not None
    async with migrated_session_factory.begin() as session:
        session.add(PermissionGrant(pattern=BUILD_SUBMISSION_EDIT.name, effect=1, subject_account_id=actor.account_id))
    credits = [
        Credit(name="Alex", kind=CreditKind.DISCORD, subject="123", username="alex_d"),
        Credit(name="Alex", role=CreditRole.LAYOUT),
    ]
    result = await executor.execute(
        EditCommand(
            BUILD_EDIT,
            (Revision(build_target(build.id), build.revision),),
            patch_values(BuildEditPatch(credits=credits)),
        ),
        context=ActingContext.direct(actor),
    )
    assert result.receipt is not None
    edited = await service.get(build.id, context=ActingContext.direct(actor))
    assert edited is not None
    assert edited.credits == credits
    undone = await executor.execute(result.receipt.undo(result.outcome.revisions), context=ActingContext.direct(actor))
    restored = await service.get(build.id, context=ActingContext.direct(actor))
    assert restored is not None
    assert restored.credits == []
    await executor.execute(result.receipt.redo(undone.outcome.revisions), context=ActingContext.direct(actor))
    redone = await service.get(build.id, context=ActingContext.direct(actor))
    assert redone is not None
    assert redone.credits == credits


async def test_submitter_can_edit_showcase_tags_and_description_after_confirmation(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    service, executor, actor, build = await setup(sessions)
    assert build.id is not None
    assert actor.account_id is not None
    async with partial(SqlAlchemyUnitOfWork, sessions)() as uow:
        tag = await uow.tags.create_showcase(
            stable_key="compact",
            display_name="Compact",
            normalized_name="compact",
            value_type=TagValueType.NONE,
            query_name=None,
            created_by_account_id=actor.account_id,
        )
        await uow.tags.set_status(tag.id, TagModerationStatus.APPROVED)
        speed = await uow.tags.create_showcase(
            stable_key="speed",
            display_name="Speed",
            normalized_name="speed",
            value_type=TagValueType.NUMERIC,
            query_name=None,
            created_by_account_id=actor.account_id,
        )
        await uow.tags.set_status(speed.id, TagModerationStatus.APPROVED)
        await uow.commit()
    build = await service.approve(
        build.id, source="test", context=ActingContext.worker("events.core.vote_session.closed")
    )

    tagged = await executor.execute(
        EditCommand(
            BUILD_EDIT,
            (Revision(build_target(build.id), build.revision),),
            patch_values(BuildEditPatch(showcase_tags=[ShowcaseTag("compact"), ShowcaseTag("speed", "1.5")])),
        ),
        context=ActingContext.direct(actor),
    )
    assert tagged.receipt is not None
    # Changing only a value is still a showcase-only edit.
    revalued = await executor.execute(
        EditCommand(
            BUILD_EDIT,
            tagged.outcome.revisions,
            patch_values(BuildEditPatch(showcase_tags=[ShowcaseTag("compact"), ShowcaseTag("speed", "2")])),
        ),
        context=ActingContext.direct(actor),
    )
    assert revalued.receipt is not None
    current = await service.get(build.id, context=ActingContext.direct(AnonymousActor()))
    assert current is not None
    assert {tag.definition.stable_key: tag.value for tag in current.tags} == {"compact": None, "speed": Decimal(2)}
    reverted = await executor.execute(
        revalued.receipt.undo(revalued.outcome.revisions), context=ActingContext.direct(actor)
    )
    current = await service.get(build.id, context=ActingContext.direct(AnonymousActor()))
    assert current is not None
    assert {tag.definition.stable_key: tag.value for tag in current.tags} == {"compact": None, "speed": Decimal("1.5")}
    assert reverted.receipt is not None
    # Undo replays every field, which still changes only the tags.
    undone = await executor.execute(
        tagged.receipt.undo(reverted.outcome.revisions), context=ActingContext.direct(actor)
    )
    current = await service.get(build.id, context=ActingContext.direct(AnonymousActor()))
    assert current is not None
    assert current.tags == []

    edited = await executor.execute(
        EditCommand(
            BUILD_EDIT,
            (Revision(build_target(build.id), undone.outcome.revisions[0].value),),
            patch_values(BuildEditPatch(showcase_tags=[ShowcaseTag("compact")], extra_user_info="edited")),
        ),
        context=ActingContext.direct(actor),
    )
    assert edited.outcome.review_vote_id is None
    assert edited.outcome.changed


async def test_build_events_and_vector_share_the_content_transaction(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    _, _, actor, build = await setup(sessions)
    factory = partial(SqlAlchemyUnitOfWork, sessions)
    vector = [1.0] * EMBEDDING_DIMENSION
    async with factory() as uow:
        assert await uow.build_vectors.fill_missing(build.id, build.revision, vector)
        await uow.commit()

    async with factory() as uow, uow.builds.editing(actor=actor.require_account_id(), source="test"):
        draft = await uow.builds.get_by_id(build.id)
        assert draft is not None
        draft.description = "rolled back"
        await uow.builds.save(draft)

    async with factory() as uow, uow.builds.editing(actor=actor.require_account_id(), source="test"):
        current = await uow.builds.get_by_id(build.id)
        assert current is not None
        assert current.embedding is not None
        current.description = "committed"
        await uow.builds.save(current)
        await uow.commit()

    async with factory() as uow:
        assert not await uow.build_vectors.fill_missing(build.id, build.revision, vector)
        deliveries = await uow.domain_events.claim(consumer="build_embeddings", limit=10)
        assert sorted(item.event.event_type for item in deliveries) == ["build.edited", "build.submitted"]
        edited = next(item.event for item in deliveries if item.event.event_type == "build.edited")
        assert edited.payload == {"revision": current.revision}
        saved = await uow.builds.get_by_id(build.id)
        assert saved is not None
        assert saved.embedding is None
        assert await uow.build_vectors.fill_missing(build.id, saved.revision, vector)
        assert not await uow.build_vectors.fill_missing(build.id, saved.revision, vector)
