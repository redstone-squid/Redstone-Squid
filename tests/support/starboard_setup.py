"""Create starboards through the production edit handlers in integration fixtures."""

from squid.core.actors import ActingContext, Actor
from squid.core.edits.application import EditExecutor
from squid.core.edits.domain import EditCommand, Revision
from squid.core.permissions.application.services import PermissionService
from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.core.starboard.application.edits import (
    STARBOARD_CREATE,
    BoardPresence,
    BoardValues,
    StarboardCreation,
    starboard_target,
)
from squid.core.starboard.application.services import StarboardService
from squid.core.starboard.application.snapshots import StarboardSnapshot
from squid.core.starboard.models import StarboardConfig, StarboardEmoji
from squid.core.unit_of_work import UnitOfWorkFactory


async def create_starboard(
    factory: UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    guild_id: int,
    channel_id: int,
    *,
    required: float = 3.0,
) -> StarboardConfig:
    service = StarboardService(uow_factory=factory)
    identity = await service.reserve_id(context=ActingContext.direct(Actor()))
    config = StarboardConfig(
        id=identity,
        guild_id=guild_id,
        channel_id=channel_id,
        name="main",
        required=required,
        emojis=(StarboardEmoji(emoji="⭐", direction="up"), StarboardEmoji(emoji="💩", direction="down", position=1)),
    )
    values = BoardPresence(**BoardValues.capture(StarboardSnapshot.capture(config, {})).model_dump(), present=True)
    executor = EditExecutor(factory, {STARBOARD_CREATE: StarboardCreation(PermissionService(uow_factory=factory))})
    await executor.execute(
        EditCommand(STARBOARD_CREATE, (Revision(starboard_target(identity), 0),), values.values()),
        context=ActingContext.direct(Actor(discord_user_id=42, guild_id=guild_id, is_bot_owner=True)),
    )
    stored = await service.get(guild_id, "main", context=ActingContext.direct(Actor()))
    assert stored is not None
    return stored
