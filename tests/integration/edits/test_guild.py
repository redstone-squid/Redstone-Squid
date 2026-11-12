"""Guild scalar and child edits use the same optimistic boundary."""

from functools import partial

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.core.actors import Actor
from squid.core.errors import AuthorizationError
from squid.edits.application import EditExecutor
from squid.edits.domain import EditCommand, EditValues, Revision, RevisionConflictError
from squid.events.models import DomainEvent, DomainEventDeliveryRecord
from squid.permissions.application.services import PermissionService
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.settings.application.edits import GUILD_EDIT, GuildSettingsEdit, guild_target
from squid.settings.application.services import SettingsService
from squid.voting.domain import RoleWeight, VoteKind
from squid.voting.domain.models import VoteConfiguration

GUILD = 70001


async def test_guild_child_writes_advance_root_and_block_stale_undo(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    settings = SettingsService(uow_factory=factory)
    executor = EditExecutor(factory, {GUILD_EDIT: GuildSettingsEdit(PermissionService(uow_factory=factory))})
    actor = Actor(account_id=None, guild_id=GUILD, is_bot_owner=True, discord_user_id=17)
    _, revision = await settings.guild_snapshot(GUILD)
    saved = await executor.execute(
        EditCommand(GUILD_EDIT, (Revision(guild_target(GUILD), revision),), EditValues.of({"locale": "zh-CN"})), actor
    )
    assert saved.receipt is not None
    async with factory() as uow:
        await uow.votes.set_role_weight(RoleWeight(GUILD, VoteKind.BUILD, 99, 2))
        await uow.commit()
    async with migrated_session_factory() as session:
        queued = await session.scalar(
            select(DomainEventDeliveryRecord)
            .join(DomainEvent)
            .where(
                DomainEvent.event_type == "guild.voting_changed",
                DomainEvent.aggregate_id == GUILD,
                DomainEventDeliveryRecord.consumer == "core",
            )
        )
        assert queued is not None
    snapshot, current = await settings.guild_snapshot(GUILD)
    assert current > saved.outcome.revisions[0].value
    assert snapshot.voting[VoteKind.BUILD].weights == (RoleWeight(GUILD, VoteKind.BUILD, 99, 2),)
    with pytest.raises(RevisionConflictError):
        await executor.execute(saved.receipt.undo(saved.outcome.revisions), actor)
    # Returning to the old value does not return to the old version (the ABA case).
    for locale in (None, "zh-CN"):
        async with factory() as uow:
            row = await uow.settings.get_or_create_for_update(GUILD)
            row.locale = locale
            await uow.commit()
    with pytest.raises(RevisionConflictError):
        await executor.execute(saved.receipt.undo(saved.outcome.revisions), actor)


async def test_guild_voting_round_trip_and_current_authority(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    settings = SettingsService(uow_factory=factory)
    executor = EditExecutor(factory, {GUILD_EDIT: GuildSettingsEdit(PermissionService(uow_factory=factory))})
    actor = Actor(guild_id=GUILD, is_bot_owner=True, discord_user_id=17)
    snapshot, revision = await settings.guild_snapshot(GUILD)
    voting = snapshot.voting | {VoteKind.BUILD: VoteConfiguration(None, (RoleWeight(GUILD, VoteKind.BUILD, 99, 2),))}
    replacement = snapshot.model_copy(update={"voting": voting})
    saved = await executor.execute(
        EditCommand(
            GUILD_EDIT,
            (Revision(guild_target(GUILD), revision),),
            EditValues.of({"voting": replacement.model_dump(mode="json")["voting"]}),
        ),
        actor,
    )
    assert saved.receipt is not None
    revoked = Actor(guild_id=GUILD, discord_user_id=17)
    with pytest.raises(AuthorizationError):
        await executor.execute(saved.receipt.undo(saved.outcome.revisions), revoked)
    undone = await executor.execute(saved.receipt.undo(saved.outcome.revisions), actor)
    assert undone.outcome.revisions[0].value > saved.outcome.revisions[0].value
    assert (await settings.guild_snapshot(GUILD))[0].voting[VoteKind.BUILD].weights == ()
