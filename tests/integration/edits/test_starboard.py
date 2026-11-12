"""Starboard revisions cover child configuration and preserve creation identity."""

from functools import partial

import anyio
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.core.actors import Actor
from squid.core.errors import AuthorizationError, ConflictError
from squid.edits.application import EditExecutor
from squid.edits.domain import EditCommand, EditResult, EditValues, Revision, RevisionConflictError
from squid.messages.models import Message
from squid.permissions.application.services import PermissionService
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.starboard.application.edits import (
    STARBOARD_CREATE,
    STARBOARD_DELETE,
    STARBOARD_EDIT,
    BoardPresence,
    BoardValues,
    EmojiValues,
    StarboardCreation,
    StarboardDeletion,
    StarboardEdit,
    starboard_target,
)
from squid.starboard.application.services import StarboardService
from squid.starboard.application.snapshots import StarboardSnapshot
from squid.starboard.models import OriginMessage, StarboardConfig, StarboardEmoji
from tests.support.starboard_setup import create_starboard

ACTOR = Actor(guild_id=10, is_bot_owner=True, discord_user_id=42)


async def test_configuration_children_share_revision_and_undo_checks_current_authority(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    service = StarboardService(uow_factory=factory)
    executor = EditExecutor(factory, {STARBOARD_EDIT: StarboardEdit(PermissionService(uow_factory=factory))})
    row = await create_starboard(factory, 10, 20)
    snapshot = await service.snapshot(row.id)
    assert snapshot is not None
    expected = (Revision(starboard_target(row.id), snapshot.revision),)
    results: list[EditResult | RevisionConflictError] = []

    async def edit(values: BoardValues) -> None:
        try:
            results.append(await executor.execute(EditCommand(STARBOARD_EDIT, expected, values.values()), ACTOR))
        except RevisionConflictError as error:
            results.append(error)

    before = BoardValues.capture(snapshot)
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(edit, before.model_copy(update={"settings": before.settings | {"required": 5.0}}))
        tasks.start_soon(edit, before.model_copy(update={"weights": {90: 2.0}}))
    assert sum(isinstance(result, RevisionConflictError) for result in results) == 1
    saved = next(result for result in results if isinstance(result, EditResult))
    assert saved.receipt is not None
    with pytest.raises(AuthorizationError):
        await executor.execute(saved.receipt.undo(saved.outcome.revisions), Actor(guild_id=10, discord_user_id=42))
    undone = await executor.execute(saved.receipt.undo(saved.outcome.revisions), ACTOR)
    assert undone.receipt is not None
    current = await service.snapshot(row.id)
    assert current is not None
    values = BoardValues.capture(current).model_copy(
        update={"emojis": (EmojiValues(emoji="✅", direction="up", position=0, multiplier=1.0),)}
    )
    await executor.execute(EditCommand(STARBOARD_EDIT, undone.outcome.revisions, values.values()), ACTOR)
    with pytest.raises(RevisionConflictError):
        await executor.execute(saved.receipt.redo(undone.outcome.revisions), ACTOR)
    assert await service.is_relevant_emoji(10, "✅")
    assert not await service.is_relevant_emoji(10, "⭐")


async def test_creation_retains_identity_and_revision_and_refuses_name_reuse(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    service = StarboardService(uow_factory=factory)
    executor = EditExecutor(factory, {STARBOARD_CREATE: StarboardCreation(PermissionService(uow_factory=factory))})
    identity = await service.reserve_id()
    config = StarboardConfig(
        id=identity, guild_id=10, channel_id=20, name=" main ", emojis=(StarboardEmoji(emoji="⭐", direction="up"),)
    )
    values = BoardPresence(**BoardValues.capture(StarboardSnapshot.capture(config, {})).model_dump(), present=True)
    created = await executor.execute(
        EditCommand(STARBOARD_CREATE, (Revision(starboard_target(identity), 0),), values.values()), ACTOR
    )
    assert created.receipt is not None
    assert created.outcome.revisions == (Revision(starboard_target(identity), 1),)
    undone = await executor.execute(created.receipt.undo(created.outcome.revisions), ACTOR)
    assert undone.receipt is not None
    assert await service.get(10, "main") is None
    redone = await executor.execute(created.receipt.redo(undone.outcome.revisions), ACTOR)
    assert redone.receipt is not None
    assert redone.outcome.revisions == (Revision(starboard_target(identity), 3),)
    restored = await service.get(10, "main")
    assert restored is not None
    assert restored.id == identity
    undone_again = await executor.execute(redone.receipt.undo(redone.outcome.revisions), ACTOR)
    assert undone_again.receipt is not None
    other = await create_starboard(factory, 10, 21)
    assert other.id != identity
    with pytest.raises(ConflictError):
        await executor.execute(redone.receipt.redo(undone_again.outcome.revisions), ACTOR)
    async with factory() as uow:
        tombstone = await uow.starboards.get_by_id(identity, include_archived=True)
        assert tombstone is not None
        assert tombstone.archived
        assert tombstone.revision == 4


async def test_used_board_refuses_creation_undo_but_explicit_delete_is_final(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    service = StarboardService(uow_factory=factory)
    permissions = PermissionService(uow_factory=factory)
    executor = EditExecutor(
        factory, {STARBOARD_CREATE: StarboardCreation(permissions), STARBOARD_DELETE: StarboardDeletion(permissions)}
    )
    identity = await service.reserve_id()
    config = StarboardConfig(
        id=identity, guild_id=10, channel_id=20, name="main", emojis=(StarboardEmoji(emoji="⭐", direction="up"),)
    )
    values = BoardPresence(**BoardValues.capture(StarboardSnapshot.capture(config, {})).model_dump(), present=True)
    created = await executor.execute(
        EditCommand(STARBOARD_CREATE, (Revision(starboard_target(identity), 0),), values.values()), ACTOR
    )
    assert created.receipt is not None
    origin = OriginMessage(
        id=100,
        author_is_bot=False,
        message=Message(
            id=100, guild_id=10, channel_id=30, author_id=40, created_at=Instant.now().subtract(seconds=60)
        ),
    )
    await service.record_vote(origin, Actor(discord_user_id=50, guild_id=10), "⭐")
    with pytest.raises(ConflictError, match="already in use"):
        await executor.execute(created.receipt.undo(created.outcome.revisions), ACTOR)
    deleted = await executor.execute(EditCommand(STARBOARD_DELETE, created.outcome.revisions, EditValues.of({})), ACTOR)
    assert deleted.receipt is not None
    assert deleted.receipt.final
    assert await service.list_for_guild(10) == ()
    assert not await service.is_relevant_emoji(10, "⭐")
    ignored = await service.record_vote(origin, Actor(discord_user_id=51, guild_id=10), "⭐")
    assert ignored.keys == ()


async def test_first_vote_and_creation_undo_cannot_both_win(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    service = StarboardService(uow_factory=factory)
    executor = EditExecutor(factory, {STARBOARD_CREATE: StarboardCreation(PermissionService(uow_factory=factory))})
    identity = await service.reserve_id()
    config = StarboardConfig(
        id=identity, guild_id=10, channel_id=20, name="main", emojis=(StarboardEmoji(emoji="⭐", direction="up"),)
    )
    values = BoardPresence(**BoardValues.capture(StarboardSnapshot.capture(config, {})).model_dump(), present=True)
    created = await executor.execute(
        EditCommand(STARBOARD_CREATE, (Revision(starboard_target(identity), 0),), values.values()), ACTOR
    )
    assert created.receipt is not None
    inverse = created.receipt.undo(created.outcome.revisions)
    origin = OriginMessage(
        id=100,
        author_is_bot=False,
        message=Message(
            id=100, guild_id=10, channel_id=30, author_id=40, created_at=Instant.now().subtract(seconds=60)
        ),
    )
    undo_results: list[EditResult | ConflictError] = []

    async def undo() -> None:
        try:
            undo_results.append(await executor.execute(inverse, ACTOR))
        except ConflictError as error:
            undo_results.append(error)

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(undo)
        tasks.start_soon(service.record_vote, origin, Actor(discord_user_id=50, guild_id=10), "⭐")
    async with factory() as uow:
        stored = await uow.starboards.get_by_id(identity, include_archived=True)
        assert stored is not None
        if isinstance(undo_results[0], EditResult):
            assert stored.archived
            assert await uow.starboards.is_unused(identity)
        else:
            assert not stored.archived
            assert not await uow.starboards.is_unused(identity)
