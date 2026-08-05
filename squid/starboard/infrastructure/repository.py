"""PostgreSQL starboard repository."""

from collections.abc import Mapping, Sequence
from typing import cast

from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.starboard.application.ports import EntryPlan, PendingVote
from squid.starboard.domain import (
    OriginMessage,
    StarboardConfig,
    StarboardDirection,
    StarboardEmoji,
    StarboardEntry,
    decide_entry_action,
)
from squid.starboard.infrastructure.models import (
    Starboard,
    StarboardOriginMessage,
    StarboardRoleMultiplier,
    StarboardSource,
    StarboardVote,
)
from squid.starboard.infrastructure.models import (
    StarboardEmoji as StarboardEmojiRow,
)
from squid.starboard.infrastructure.models import (
    StarboardEntry as StarboardEntryRow,
)


class PostgresStarboardRepository:
    """Persist configs and serialize each origin's score mutations with an advisory lock."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def relevant_emojis(self, guild_id: int) -> frozenset[str]:
        async with self._session_factory() as session:
            values = await session.scalars(
                select(StarboardEmojiRow.emoji)
                .join(Starboard, Starboard.id == StarboardEmojiRow.starboard_id)
                .join(StarboardSource, StarboardSource.starboard_id == Starboard.id)
                .where(Starboard.enabled, StarboardSource.guild_id == guild_id)
                .distinct()
            )
            return frozenset(values)

    async def configs_for_source(self, guild_id: int, channel_id: int) -> Sequence[StarboardConfig]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(Starboard)
                    .join(StarboardSource, StarboardSource.starboard_id == Starboard.id)
                    .where(
                        Starboard.enabled,
                        StarboardSource.guild_id == guild_id,
                        or_(StarboardSource.channel_id == 0, StarboardSource.channel_id == channel_id),
                    )
                    .distinct()
                    .order_by(Starboard.id)
                )
            ).all()
            return await self._configs(session, rows)

    async def role_multipliers(self, starboard_id: int) -> Mapping[int, float]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(StarboardRoleMultiplier).where(StarboardRoleMultiplier.starboard_id == starboard_id)
                )
            ).all()
            return {row.role_id: row.multiplier for row in rows}

    async def record_votes(
        self, origin: OriginMessage, user_id: int, votes: Sequence[PendingVote]
    ) -> Sequence[EntryPlan]:
        if not votes:
            return ()
        async with self._session_factory.begin() as session:
            await self._lock(session, origin.id)
            await session.execute(
                pg_insert(StarboardOriginMessage)
                .values(
                    id=origin.id,
                    guild_id=origin.guild_id,
                    channel_id=origin.channel_id,
                    author_id=origin.author_id,
                    author_is_bot=origin.author_is_bot,
                    is_nsfw=origin.is_nsfw,
                    has_image=origin.has_image,
                    posted_at=origin.posted_at,
                    deleted_at=None,
                )
                .on_conflict_do_update(
                    index_elements=[StarboardOriginMessage.id],
                    set_={
                        "channel_id": origin.channel_id,
                        "author_id": origin.author_id,
                        "author_is_bot": origin.author_is_bot,
                        "is_nsfw": origin.is_nsfw,
                        "has_image": origin.has_image,
                        "seen_at": func.now(),
                        "deleted_at": None,
                    },
                )
            )
            for vote in votes:
                await session.execute(
                    pg_insert(StarboardVote)
                    .values(
                        starboard_id=vote.config.id,
                        origin_message_id=origin.id,
                        user_id=user_id,
                        emoji=vote.emoji,
                        direction=vote.direction,
                        weight=vote.weight,
                        target_author_id=origin.author_id,
                    )
                    .on_conflict_do_update(
                        index_elements=[
                            StarboardVote.starboard_id,
                            StarboardVote.origin_message_id,
                            StarboardVote.user_id,
                        ],
                        set_={"emoji": vote.emoji, "direction": vote.direction, "weight": vote.weight},
                    )
                )
                await session.execute(
                    pg_insert(StarboardEntryRow)
                    .values(starboard_id=vote.config.id, origin_message_id=origin.id)
                    .on_conflict_do_nothing()
                )
            return await self._refresh_locked(
                session, origin.id, {vote.config.id for vote in votes}, force=False, origin=origin
            )

    async def withdraw_vote(self, origin_message_id: int, user_id: int, emoji: str) -> Sequence[EntryPlan]:
        async with self._session_factory.begin() as session:
            await self._lock(session, origin_message_id)
            starboard_ids = set(
                await session.scalars(
                    delete(StarboardVote)
                    .where(
                        StarboardVote.origin_message_id == origin_message_id,
                        StarboardVote.user_id == user_id,
                        StarboardVote.emoji == emoji,
                    )
                    .returning(StarboardVote.starboard_id)
                )
            )
            return await self._refresh_locked(session, origin_message_id, starboard_ids, force=False)

    async def recount_votes(
        self, origin: OriginMessage, votes: Sequence[tuple[int, PendingVote]]
    ) -> Sequence[EntryPlan]:
        async with self._session_factory.begin() as session:
            await self._lock(session, origin.id)
            await session.execute(
                pg_insert(StarboardOriginMessage)
                .values(
                    id=origin.id,
                    guild_id=origin.guild_id,
                    channel_id=origin.channel_id,
                    author_id=origin.author_id,
                    author_is_bot=origin.author_is_bot,
                    is_nsfw=origin.is_nsfw,
                    has_image=origin.has_image,
                    posted_at=origin.posted_at,
                    deleted_at=None,
                )
                .on_conflict_do_update(
                    index_elements=[StarboardOriginMessage.id],
                    set_={"seen_at": func.now(), "deleted_at": None, "has_image": origin.has_image},
                )
            )
            starboard_ids = set(
                await session.scalars(
                    delete(StarboardVote)
                    .where(StarboardVote.origin_message_id == origin.id)
                    .returning(StarboardVote.starboard_id)
                )
            )
            for user_id, vote in votes:
                starboard_ids.add(vote.config.id)
                await session.execute(
                    pg_insert(StarboardVote).values(
                        starboard_id=vote.config.id,
                        origin_message_id=origin.id,
                        user_id=user_id,
                        emoji=vote.emoji,
                        direction=vote.direction,
                        weight=vote.weight,
                        target_author_id=origin.author_id,
                    )
                )
                await session.execute(
                    pg_insert(StarboardEntryRow)
                    .values(starboard_id=vote.config.id, origin_message_id=origin.id)
                    .on_conflict_do_nothing()
                )
            return await self._refresh_locked(session, origin.id, starboard_ids, force=True, origin=origin)

    async def clear_votes(self, origin_message_id: int, emoji: str | None = None) -> Sequence[EntryPlan]:
        async with self._session_factory.begin() as session:
            await self._lock(session, origin_message_id)
            statement = delete(StarboardVote).where(StarboardVote.origin_message_id == origin_message_id)
            if emoji is not None:
                statement = statement.where(StarboardVote.emoji == emoji)
            starboard_ids = set(await session.scalars(statement.returning(StarboardVote.starboard_id)))
            return await self._refresh_locked(session, origin_message_id, starboard_ids, force=False)

    async def refresh(self, origin_message_id: int, *, force: bool = False) -> Sequence[EntryPlan]:
        async with self._session_factory.begin() as session:
            await self._lock(session, origin_message_id)
            return await self._refresh_locked(session, origin_message_id, None, force=force)

    async def mark_origin_deleted(self, origin_message_id: int) -> Sequence[EntryPlan]:
        async with self._session_factory.begin() as session:
            await self._lock(session, origin_message_id)
            changed = await session.execute(
                update(StarboardOriginMessage)
                .where(StarboardOriginMessage.id == origin_message_id)
                .values(deleted_at=func.now())
                .returning(StarboardOriginMessage.id)
            )
            if changed.scalar_one_or_none() is None:
                return ()
            return await self._refresh_locked(session, origin_message_id, None, force=True)

    async def mark_posted(self, starboard_id: int, origin_message_id: int, message_id: int, channel_id: int) -> None:
        async with self._session_factory.begin() as session:
            await session.execute(
                update(StarboardEntryRow)
                .where(
                    StarboardEntryRow.starboard_id == starboard_id,
                    StarboardEntryRow.origin_message_id == origin_message_id,
                )
                .values(
                    posted_message_id=message_id,
                    posted_channel_id=channel_id,
                    last_rendered_score=StarboardEntryRow.score,
                    first_posted_at=func.coalesce(StarboardEntryRow.first_posted_at, func.now()),
                    updated_at=func.now(),
                )
            )

    async def mark_rendered(self, starboard_id: int, origin_message_id: int, score: float) -> None:
        async with self._session_factory.begin() as session:
            await session.execute(
                update(StarboardEntryRow)
                .where(
                    StarboardEntryRow.starboard_id == starboard_id,
                    StarboardEntryRow.origin_message_id == origin_message_id,
                )
                .values(last_rendered_score=score, updated_at=func.now())
            )

    async def mark_removed(self, starboard_id: int, origin_message_id: int) -> None:
        async with self._session_factory.begin() as session:
            await session.execute(
                update(StarboardEntryRow)
                .where(
                    StarboardEntryRow.starboard_id == starboard_id,
                    StarboardEntryRow.origin_message_id == origin_message_id,
                )
                .values(posted_message_id=None, posted_channel_id=None, last_rendered_score=None, updated_at=func.now())
            )

    async def reset_deleted_post(self, posted_message_id: int) -> tuple[int, int] | None:
        async with self._session_factory.begin() as session:
            result = await session.execute(
                update(StarboardEntryRow)
                .where(StarboardEntryRow.posted_message_id == posted_message_id)
                .values(posted_message_id=None, posted_channel_id=None, last_rendered_score=None, updated_at=func.now())
                .returning(StarboardEntryRow.starboard_id, StarboardEntryRow.origin_message_id)
            )
            row = result.one_or_none()
            return (row[0], row[1]) if row is not None else None

    async def disable_channel(self, channel_id: int) -> None:
        async with self._session_factory.begin() as session:
            await session.execute(update(Starboard).where(Starboard.channel_id == channel_id).values(enabled=False))

    async def create(self, config: StarboardConfig) -> StarboardConfig:
        async with self._session_factory.begin() as session:
            starboard_id = (
                await session.execute(
                    pg_insert(Starboard).values(**self._config_values(config)).returning(Starboard.id)
                )
            ).scalar_one()
            await session.execute(
                pg_insert(StarboardSource).values(starboard_id=starboard_id, guild_id=config.guild_id, channel_id=0)
            )
            await self._replace_emojis(session, starboard_id, config.emojis)
            return await self._get_by_id(session, starboard_id)

    async def delete(self, guild_id: int, name: str) -> bool:
        async with self._session_factory.begin() as session:
            result = await session.execute(
                delete(Starboard)
                .where(Starboard.guild_id == guild_id, func.lower(Starboard.name) == name.lower())
                .returning(Starboard.id)
            )
            return result.scalar_one_or_none() is not None

    async def list_for_guild(self, guild_id: int) -> Sequence[StarboardConfig]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(select(Starboard).where(Starboard.guild_id == guild_id).order_by(Starboard.name))
            ).all()
            return await self._configs(session, rows)

    async def get(self, guild_id: int, name: str) -> StarboardConfig | None:
        async with self._session_factory() as session:
            row = await session.scalar(
                select(Starboard).where(Starboard.guild_id == guild_id, func.lower(Starboard.name) == name.lower())
            )
            return await self._to_config(session, row) if row is not None else None

    async def update(self, guild_id: int, name: str, settings: Mapping[str, object]) -> StarboardConfig | None:
        allowed = set(self._config_values(None)) - {"guild_id", "channel_id", "name"}
        if not settings or not set(settings) <= allowed | {"channel_id", "name"}:
            msg = "Unknown or empty starboard setting update."
            raise ValueError(msg)
        async with self._session_factory.begin() as session:
            starboard_id = await session.scalar(
                update(Starboard)
                .where(Starboard.guild_id == guild_id, func.lower(Starboard.name) == name.lower())
                .values(**settings)
                .returning(Starboard.id)
            )
            return await self._get_by_id(session, starboard_id) if starboard_id is not None else None

    async def set_emojis(self, starboard_id: int, emojis: Sequence[StarboardEmoji]) -> None:
        async with self._session_factory.begin() as session:
            await self._replace_emojis(session, starboard_id, emojis)

    async def set_role_multiplier(self, starboard_id: int, role_id: int, multiplier: float | None) -> None:
        async with self._session_factory.begin() as session:
            if multiplier is None:
                await session.execute(
                    delete(StarboardRoleMultiplier).where(
                        StarboardRoleMultiplier.starboard_id == starboard_id,
                        StarboardRoleMultiplier.role_id == role_id,
                    )
                )
                return
            await session.execute(
                pg_insert(StarboardRoleMultiplier)
                .values(starboard_id=starboard_id, role_id=role_id, multiplier=multiplier)
                .on_conflict_do_update(
                    index_elements=[StarboardRoleMultiplier.starboard_id, StarboardRoleMultiplier.role_id],
                    set_={"multiplier": multiplier},
                )
            )

    async def _refresh_locked(
        self,
        session: AsyncSession,
        origin_message_id: int,
        starboard_ids: set[int] | None,
        *,
        force: bool,
        origin: OriginMessage | None = None,
    ) -> Sequence[EntryPlan]:
        if starboard_ids is not None and not starboard_ids:
            return ()
        statement = select(StarboardEntryRow).where(StarboardEntryRow.origin_message_id == origin_message_id)
        if starboard_ids is not None:
            statement = statement.where(StarboardEntryRow.starboard_id.in_(starboard_ids))
        entries = (await session.scalars(statement.order_by(StarboardEntryRow.starboard_id))).all()
        if origin is None:
            origin_row = await session.get(StarboardOriginMessage, origin_message_id)
            if origin_row is None:
                return ()
            origin = self._origin(origin_row)
        plans: list[EntryPlan] = []
        for entry_row in entries:
            score, raw_count = (
                await session.execute(
                    select(
                        func.coalesce(
                            func.sum(
                                case(
                                    (StarboardVote.direction == "up", StarboardVote.weight), else_=-StarboardVote.weight
                                )
                            ),
                            0.0,
                        ),
                        func.count(StarboardVote.user_id),
                    ).where(
                        StarboardVote.starboard_id == entry_row.starboard_id,
                        StarboardVote.origin_message_id == origin_message_id,
                    )
                )
            ).one()
            entry_row.score = float(score)
            entry_row.raw_count = int(raw_count)
            config = await self._get_by_id(session, entry_row.starboard_id)
            entry = self._entry(entry_row)
            action = decide_entry_action(config, entry, entry.score, origin.present)
            if action.value == "update" and not force and entry.last_rendered_score == entry.score:
                action = type(action).NOOP
            plans.append(EntryPlan(config, origin, entry, action))
        return plans

    @staticmethod
    async def _lock(session: AsyncSession, origin_message_id: int) -> None:
        key = f"starboard:{origin_message_id}"
        await session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(key, 0))))

    async def _get_by_id(self, session: AsyncSession, starboard_id: int) -> StarboardConfig:
        row = await session.get(Starboard, starboard_id)
        if row is None:
            msg = f"Starboard {starboard_id} no longer exists."
            raise LookupError(msg)
        return await self._to_config(session, row)

    async def _configs(self, session: AsyncSession, rows: Sequence[Starboard]) -> tuple[StarboardConfig, ...]:
        return tuple([await self._to_config(session, row) for row in rows])

    async def _to_config(self, session: AsyncSession, row: Starboard) -> StarboardConfig:
        emoji_rows = (
            await session.scalars(
                select(StarboardEmojiRow)
                .where(StarboardEmojiRow.starboard_id == row.id)
                .order_by(StarboardEmojiRow.position)
            )
        ).all()
        emojis = tuple(
            StarboardEmoji(item.emoji, cast(StarboardDirection, item.direction), item.multiplier, item.position)
            for item in emoji_rows
        )
        return StarboardConfig(
            row.id,
            row.guild_id,
            row.channel_id,
            row.name,
            emojis,
            enabled=row.enabled,
            required=row.required,
            required_remove=row.required_remove,
            self_vote=row.self_vote,
            allow_bots=row.allow_bots,
            require_image=row.require_image,
            min_age_seconds=row.min_age_seconds,
            max_age_seconds=row.max_age_seconds,
            autoreact_upvote=row.autoreact_upvote,
            autoreact_downvote=row.autoreact_downvote,
            remove_invalid_reactions=row.remove_invalid_reactions,
            link_edits=row.link_edits,
            link_deletes=row.link_deletes,
            display_emoji=row.display_emoji,
            colour=row.colour,
            jump_to_message=row.jump_to_message,
            attachments_list=row.attachments_list,
            replied_to=row.replied_to,
            ping_author=row.ping_author,
        )

    @staticmethod
    def _origin(row: StarboardOriginMessage) -> OriginMessage:
        return OriginMessage(
            row.id,
            row.guild_id,
            row.channel_id,
            row.author_id,
            row.author_is_bot,
            row.posted_at,
            is_nsfw=row.is_nsfw,
            has_image=row.has_image,
            deleted_at=row.deleted_at,
        )

    @staticmethod
    def _entry(row: StarboardEntryRow) -> StarboardEntry:
        return StarboardEntry(
            row.starboard_id,
            row.origin_message_id,
            row.score,
            row.raw_count,
            row.posted_message_id,
            row.posted_channel_id,
            row.last_rendered_score,
            row.first_posted_at,
            row.updated_at,
        )

    @staticmethod
    def _config_values(config: StarboardConfig | None) -> dict[str, object]:
        if config is None:
            return {
                key: None
                for key in (
                    "guild_id",
                    "channel_id",
                    "name",
                    "enabled",
                    "required",
                    "required_remove",
                    "self_vote",
                    "allow_bots",
                    "require_image",
                    "min_age_seconds",
                    "max_age_seconds",
                    "autoreact_upvote",
                    "autoreact_downvote",
                    "remove_invalid_reactions",
                    "link_edits",
                    "link_deletes",
                    "display_emoji",
                    "colour",
                    "jump_to_message",
                    "attachments_list",
                    "replied_to",
                    "ping_author",
                )
            }
        return {key: getattr(config, key) for key in PostgresStarboardRepository._config_values(None)}

    @staticmethod
    async def _replace_emojis(session: AsyncSession, starboard_id: int, emojis: Sequence[StarboardEmoji]) -> None:
        await session.execute(delete(StarboardEmojiRow).where(StarboardEmojiRow.starboard_id == starboard_id))
        if emojis:
            await session.execute(
                pg_insert(StarboardEmojiRow),
                [
                    {
                        "starboard_id": starboard_id,
                        "emoji": item.emoji,
                        "direction": item.direction,
                        "multiplier": item.multiplier,
                        "position": position,
                    }
                    for position, item in enumerate(emojis)
                ],
            )
