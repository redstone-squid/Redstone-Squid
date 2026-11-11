"""Showcase history conflicts with every build edit and rechecks current authority."""

from functools import partial

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.accounts.domain import AccountConsent
from squid.accounts.errors import ConsentRequiredError
from squid.builds.application.edits import build_target
from squid.builds.application.restrictions import RestrictionService
from squid.builds.domain import Status
from squid.builds.history_models import BuildHistoryEntry
from squid.builds.models import OtherBuild
from squid.core.actors import Actor
from squid.core.errors import AuthorizationError
from squid.edits.application import EditExecutor
from squid.edits.domain import EditCommand, Revision, RevisionConflictError
from squid.permissions.application.services import PermissionService
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.tags.application.edits import (
    ALIAS_EDIT,
    SHOWCASE_EDIT,
    AliasValues,
    ShowcaseAssignmentEdit,
    ShowcaseValues,
    TagAliasEdit,
)
from squid.tags.domain import TagAuthority, TagModerationStatus, TagSemanticKind, TagValueType
from squid.tags.models import TagDefinition


async def test_showcase_history_and_external_build_edits_share_one_revision(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    factory = partial(SqlAlchemyUnitOfWork, sessions, verification_code_pepper="test-pepper")
    async with factory() as uow:
        owner = await uow.accounts.create(consent=AccountConsent.grant_current())
        tag = await uow.tags.create_showcase(
            stable_key="test_showcase",
            display_name="Showcase",
            normalized_name="showcase",
            value_type=TagValueType.TEXT,
            query_name=None,
            created_by_account_id=owner.id,
        )
        await uow.tags.set_status(tag.id, TagModerationStatus.APPROVED)
        build = OtherBuild(submitter_account_id=owner.id, submission_status=Status.PENDING)
        async with uow.builds.editing(actor=owner.id, source="test"):
            await uow.builds.save(build)
        await uow.commit()
    assert build.id is not None
    actor = Actor(account_id=owner.id)
    executor = EditExecutor(factory, {SHOWCASE_EDIT: ShowcaseAssignmentEdit()})
    command = EditCommand(
        SHOWCASE_EDIT,
        (Revision(build_target(build.id), build.revision),),
        ShowcaseValues(tag_id=tag.id, value="First").edit_values(),
    )
    saved = await executor.execute(command, actor)
    assert saved.receipt is not None
    assert (await executor.execute(command, actor)).replayed
    with pytest.raises(AuthorizationError):
        await executor.execute(saved.receipt.inverse(saved.outcome.revisions), Actor(account_id=owner.id + 1))
    undone = await executor.execute(saved.receipt.inverse(saved.outcome.revisions), actor)
    assert undone.receipt is not None
    async with factory() as uow:
        assert await uow.tags.assignment(build.id, tag.id) is None
        assert await uow.tags.build_revision(build.id) == undone.outcome.revisions[0].value
    redone = await executor.execute(undone.receipt.inverse(undone.outcome.revisions, redo=True), actor)
    assert redone.receipt is not None
    async with factory() as uow:
        account = await uow.accounts.lock_for_edit(owner.id)
        account.consent_version = None
        account.consented_at = None
        await uow.commit()
    with pytest.raises(ConsentRequiredError):
        await executor.execute(redone.receipt.inverse(redone.outcome.revisions), actor)
    async with factory() as uow, uow.builds.editing(actor=owner.id, source="external"):
        current = await uow.builds.get_for_update(build.id)
        assert current is not None
        current.description = "Changed elsewhere"
        await uow.builds.save(current)
        await uow.commit()
    with pytest.raises(RevisionConflictError):
        await executor.execute(redone.receipt.inverse(redone.outcome.revisions), actor)
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(BuildHistoryEntry)) == 5


async def test_alias_history_fences_legacy_writers_and_current_permissions(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    factory = partial(SqlAlchemyUnitOfWork, sessions)
    async with sessions.begin() as session:
        definition = TagDefinition(
            stable_key="test_restriction",
            display_name="Restriction",
            normalized_name="restriction",
            authority=TagAuthority.OFFICIAL,
            semantic_kind=TagSemanticKind.RESTRICTION,
            restriction_type="miscellaneous",
            value_type=TagValueType.NONE,
            moderation_status=TagModerationStatus.APPROVED,
        )
        session.add(definition)
    permissions = PermissionService(uow_factory=factory)
    executor = EditExecutor(factory, {ALIAS_EDIT: TagAliasEdit(permissions)})
    restrictions = RestrictionService(uow_factory=factory)
    actor = Actor(discord_user_id=1, is_bot_owner=True)
    command = EditCommand(
        ALIAS_EDIT,
        (await restrictions.alias_revision("Restriction"),),
        AliasValues(alias="Alternate").edit_values(),
    )
    saved = await executor.execute(command, actor)
    assert saved.receipt is not None
    assert (await executor.execute(command, actor)).replayed
    with pytest.raises(AuthorizationError):
        await executor.execute(saved.receipt.inverse(saved.outcome.revisions), Actor(discord_user_id=2))
    undone = await executor.execute(saved.receipt.inverse(saved.outcome.revisions), actor)
    assert undone.receipt is not None
    async with factory() as uow:
        assert not await uow.restrictions.has_alias(definition.id, "Alternate")
    redone = await executor.execute(undone.receipt.inverse(undone.outcome.revisions, redo=True), actor)
    assert redone.receipt is not None
    await restrictions.add_alias("Restriction", "Other alias")
    with pytest.raises(RevisionConflictError):
        await executor.execute(redone.receipt.inverse(redone.outcome.revisions), actor)
