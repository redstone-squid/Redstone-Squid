"""Tag library edits: alias, proposal, and moderation history against current authority."""

from functools import partial

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.accounts.domain import AccountConsent
from squid.builds.application.restrictions import RestrictionService
from squid.core.actors import Actor
from squid.core.errors import AuthorizationError, ConflictError, InvalidStateError
from squid.edits.application import EditExecutor
from squid.edits.domain import EditCommand, Revision, RevisionConflictError
from squid.permissions.application.services import PermissionService
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.tags.application import TagService
from squid.tags.application.edits import (
    ALIAS_EDIT,
    MODERATION_EDIT,
    PROPOSAL_EDIT,
    AliasValues,
    ModerationValues,
    ProposalValues,
    TagAliasEdit,
    TagModerationEdit,
    TagProposalEdit,
    tag_target,
)
from squid.tags.domain import TagAuthority, TagModerationStatus, TagSemanticKind, TagValueType
from squid.tags.models import TagAlias, TagDefinition


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
        await executor.execute(saved.receipt.undo(saved.outcome.revisions), Actor(discord_user_id=2))
    undone = await executor.execute(saved.receipt.undo(saved.outcome.revisions), actor)
    assert undone.receipt is not None
    async with factory() as uow:
        assert not await uow.restrictions.has_alias(definition.id, "Alternate")
    redone = await executor.execute(saved.receipt.redo(undone.outcome.revisions), actor)
    assert redone.receipt is not None
    await restrictions.add_alias("Restriction", "Other alias")
    with pytest.raises(RevisionConflictError):
        await executor.execute(redone.receipt.undo(redone.outcome.revisions), actor)


async def test_proposals_keep_identity_through_undo_and_moderation_is_final(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory, verification_code_pepper="test-pepper")
    async with factory() as uow:
        account = await uow.accounts.create(consent=AccountConsent.grant_current())
        await uow.commit()
    tags = TagService(uow_factory=factory)
    identity = await tags.reserve_identity()
    executor = EditExecutor(
        factory,
        {
            PROPOSAL_EDIT: TagProposalEdit(),
            MODERATION_EDIT: TagModerationEdit(PermissionService(uow_factory=factory)),
        },
    )
    actor = Actor(account_id=account.id)
    values = ProposalValues(display_name=" Compact ", value_type=TagValueType.NONE, query_name="compact")
    command = EditCommand(PROPOSAL_EDIT, (Revision(tag_target(identity), 0),), values.edit_values())
    created = await executor.execute(command, actor)
    assert created.receipt is not None
    assert [row.id for row in await tags.pending()] == [identity]
    assert await tags.public_definition(identity) is None
    with pytest.raises(ConflictError):
        await executor.execute(
            EditCommand(PROPOSAL_EDIT, (Revision(tag_target(await tags.reserve_identity()), 0),), values.edit_values()),
            actor,
        )
    undone = await executor.execute(created.receipt.undo(created.outcome.revisions), actor)
    assert undone.receipt is not None
    assert await tags.pending() == ()
    assert (await executor.execute(command, actor)).replayed
    assert await tags.pending() == ()
    redone = await executor.execute(created.receipt.redo(undone.outcome.revisions), actor)
    assert redone.receipt is not None
    (restored,) = await tags.pending()
    assert restored.id == identity
    assert restored.stable_key == values.stable_key
    moderation = EditCommand(
        MODERATION_EDIT,
        redone.outcome.revisions,
        ModerationValues(status=TagModerationStatus.APPROVED).edit_values(),
    )
    with pytest.raises(AuthorizationError):
        await executor.execute(moderation, actor)
    approved = await executor.execute(moderation, Actor(account_id=account.id, is_bot_owner=True))
    assert approved.receipt is not None
    assert approved.receipt.final
    with pytest.raises(InvalidStateError):
        approved.receipt.undo(approved.outcome.revisions)
    assert [row.id for row in await tags.public_definitions()] == [identity]
    assert await tags.public_definition(identity) is not None
    with pytest.raises(ConflictError):
        await executor.execute(redone.receipt.undo(approved.outcome.revisions), actor)


async def test_proposal_undo_refuses_a_definition_already_in_use(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory, verification_code_pepper="test-pepper")
    async with factory() as uow:
        account = await uow.accounts.create(consent=AccountConsent.grant_current())
        await uow.commit()
    tags = TagService(uow_factory=factory)
    identity = await tags.reserve_identity()
    actor = Actor(account_id=account.id)
    executor = EditExecutor(factory, {PROPOSAL_EDIT: TagProposalEdit()})
    result = await executor.execute(
        EditCommand(
            PROPOSAL_EDIT,
            (Revision(tag_target(identity), 0),),
            ProposalValues(display_name="Used proposal", value_type=TagValueType.NONE).edit_values(),
        ),
        actor,
    )
    assert result.receipt is not None
    async with migrated_session_factory.begin() as session:
        definition = await session.get(TagDefinition, identity)
        assert definition is not None
        session.add(TagAlias(definition=definition, alias="Referenced", normalized_alias="referenced"))
    with pytest.raises(ConflictError):
        await executor.execute(result.receipt.undo(result.outcome.revisions), actor)
