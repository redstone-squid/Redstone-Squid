"""PostgreSQL starboard repository."""

from collections.abc import Callable, Mapping, Sequence

from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from squid.starboard.application.ports import EntryKey, EntryState, PendingVote
from squid.starboard.models import (
    OriginMessage,
    StarboardConfig,
    StarboardEmoji,
    StarboardEntry,
    StarboardRoleMultiplier,
    StarboardSource,
    StarboardVote,
)


class PostgresStarboardRepository:
    """Persist configs and serialize each origin's score mutations with an advisory lock."""

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session

    async def relevant_emojis(self, guild_id: int) -> frozenset[str]:
        session = self._session()
        values = await session.scalars(
            select(StarboardEmoji.emoji)
            .join(StarboardConfig, StarboardConfig.id == StarboardEmoji.starboard_id)
            .join(StarboardSource, StarboardSource.starboard_id == StarboardConfig.id)
            .where(StarboardConfig.enabled, StarboardSource.guild_id == guild_id)
            .distinct()
        )
        return frozenset(values)

    async def configs_for_source(self, guild_id: int, channel_id: int) -> Sequence[StarboardConfig]:
        session = self._session()
        rows = (
            await session.scalars(
                select(StarboardConfig)
                .join(StarboardSource, StarboardSource.starboard_id == StarboardConfig.id)
                .where(
                    StarboardConfig.enabled,
                    StarboardSource.guild_id == guild_id,
                    or_(StarboardSource.channel_id == 0, StarboardSource.channel_id == channel_id),
                )
                .distinct()
                .order_by(StarboardConfig.id)
            )
        ).all()
        return await self._configs(session, rows)

    async def role_multipliers(self, starboard_id: int) -> Mapping[int, float]:
        session = self._session()
        rows = (
            await session.scalars(
                select(StarboardRoleMultiplier).where(StarboardRoleMultiplier.starboard_id == starboard_id)
            )
        ).all()
        return {row.role_id: row.multiplier for row in rows}

    async def record_votes(
        self, origin: OriginMessage, user_id: int, votes: Sequence[PendingVote]
    ) -> Sequence[EntryKey]:
        session = self._session()
        if not votes:
            return ()
        await self._lock(session, origin.id)
        await session.scalar(
            pg_insert(OriginMessage)
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
                index_elements=[OriginMessage.id],
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
            .returning(OriginMessage),
            execution_options={"populate_existing": True},
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
                pg_insert(StarboardEntry)
                .values(starboard_id=vote.config.id, origin_message_id=origin.id)
                .on_conflict_do_nothing()
            )
        return await self._refresh_locked(
            session, origin.id, {vote.config.id for vote in votes}, force=False, origin=origin
        )

    async def withdraw_vote(self, origin_message_id: int, user_id: int, emoji: str) -> Sequence[EntryKey]:
        session = self._session()
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
    ) -> Sequence[EntryKey]:
        session = self._session()
        await self._lock(session, origin.id)
        await session.scalar(
            pg_insert(OriginMessage)
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
                index_elements=[OriginMessage.id],
                set_={"seen_at": func.now(), "deleted_at": None, "has_image": origin.has_image},
            )
            .returning(OriginMessage),
            execution_options={"populate_existing": True},
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
                pg_insert(StarboardEntry)
                .values(starboard_id=vote.config.id, origin_message_id=origin.id)
                .on_conflict_do_nothing()
            )
        return await self._refresh_locked(session, origin.id, starboard_ids, force=True, origin=origin)

    async def clear_votes(self, origin_message_id: int, emoji: str | None = None) -> Sequence[EntryKey]:
        session = self._session()
        await self._lock(session, origin_message_id)
        statement = delete(StarboardVote).where(StarboardVote.origin_message_id == origin_message_id)
        if emoji is not None:
            statement = statement.where(StarboardVote.emoji == emoji)
        starboard_ids = set(await session.scalars(statement.returning(StarboardVote.starboard_id)))
        return await self._refresh_locked(session, origin_message_id, starboard_ids, force=False)

    async def refresh(self, origin_message_id: int, *, force: bool = False) -> Sequence[EntryKey]:
        session = self._session()
        await self._lock(session, origin_message_id)
        return await self._refresh_locked(session, origin_message_id, None, force=force)

    async def mark_origin_deleted(self, origin_message_id: int) -> Sequence[EntryKey]:
        session = self._session()
        await self._lock(session, origin_message_id)
        changed = await session.execute(
            update(OriginMessage)
            .where(OriginMessage.id == origin_message_id)
            .values(deleted_at=func.now())
            .returning(OriginMessage.id)
        )
        if changed.scalar_one_or_none() is None:
            return ()
        return await self._refresh_locked(session, origin_message_id, None, force=True)

    async def mark_rendered(self, starboard_id: int, origin_message_id: int, score: float) -> None:
        session = self._session()
        await session.execute(
            update(StarboardEntry)
            .where(
                StarboardEntry.starboard_id == starboard_id,
                StarboardEntry.origin_message_id == origin_message_id,
            )
            .values(last_rendered_score=score, updated_at=func.now())
        )

    async def entry_state(self, starboard_id: int, origin_message_id: int) -> EntryState | None:
        """Load one entry with its board configuration and origin facts."""
        session = self._session()
        entry_row = await session.get(StarboardEntry, (starboard_id, origin_message_id))
        origin_row = await session.get(OriginMessage, origin_message_id)
        if entry_row is None or origin_row is None:
            return None
        starboard_row = await session.get(StarboardConfig, starboard_id)
        if starboard_row is None:
            return None
        config = await self._with_emojis(session, starboard_row)
        return EntryState(config, origin_row, entry_row)

    async def disable_channel(self, channel_id: int) -> None:
        session = self._session()
        await session.execute(
            update(StarboardConfig).where(StarboardConfig.channel_id == channel_id).values(enabled=False)
        )

    async def create(self, config: StarboardConfig) -> StarboardConfig:
        session = self._session()
        del config.id
        session.add(config)
        await session.flush()
        starboard_id = config.id
        await session.execute(
            pg_insert(StarboardSource).values(starboard_id=starboard_id, guild_id=config.guild_id, channel_id=0)
        )
        await self._replace_emojis(session, starboard_id, config.emojis)
        return await self._get_by_id(session, starboard_id)

    async def delete(self, guild_id: int, name: str) -> bool:
        session = self._session()
        result = await session.execute(
            delete(StarboardConfig)
            .where(StarboardConfig.guild_id == guild_id, func.lower(StarboardConfig.name) == name.lower())
            .returning(StarboardConfig.id)
        )
        return result.scalar_one_or_none() is not None

    async def list_for_guild(self, guild_id: int) -> Sequence[StarboardConfig]:
        session = self._session()
        rows = (
            await session.scalars(
                select(StarboardConfig).where(StarboardConfig.guild_id == guild_id).order_by(StarboardConfig.name)
            )
        ).all()
        return await self._configs(session, rows)

    async def get(self, guild_id: int, name: str, *, for_update: bool = False) -> StarboardConfig | None:
        session = self._session()
        statement = select(StarboardConfig).where(
            StarboardConfig.guild_id == guild_id, func.lower(StarboardConfig.name) == name.lower()
        )
        if for_update:
            statement = statement.with_for_update()
        row = await session.scalar(statement)
        return await self._with_emojis(session, row) if row is not None else None

    async def set_emojis(self, starboard_id: int, emojis: Sequence[StarboardEmoji]) -> None:
        session = self._session()
        await self._replace_emojis(session, starboard_id, emojis)

    async def set_role_multiplier(self, starboard_id: int, role_id: int, multiplier: float | None) -> None:
        session = self._session()
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
    ) -> Sequence[EntryKey]:
        if starboard_ids is not None and not starboard_ids:
            return ()
        statement = select(StarboardEntry).where(StarboardEntry.origin_message_id == origin_message_id)
        if starboard_ids is not None:
            statement = statement.where(StarboardEntry.starboard_id.in_(starboard_ids))
        entries = (await session.scalars(statement.order_by(StarboardEntry.starboard_id))).all()
        if origin is None:
            origin_row = await session.get(OriginMessage, origin_message_id)
            if origin_row is None:
                return ()
            origin = origin_row
        keys: list[EntryKey] = []
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
            if force or entry_row.last_rendered_score != entry_row.score:
                keys.append((entry_row.starboard_id, origin_message_id))
        return keys

    @staticmethod
    async def _lock(session: AsyncSession, origin_message_id: int) -> None:
        key = f"starboard:{origin_message_id}"
        await session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(key, 0))))

    async def _get_by_id(self, session: AsyncSession, starboard_id: int) -> StarboardConfig:
        row = await session.get(StarboardConfig, starboard_id)
        if row is None:
            msg = f"Starboard {starboard_id} no longer exists."
            raise LookupError(msg)
        return await self._with_emojis(session, row)

    async def _configs(self, session: AsyncSession, rows: Sequence[StarboardConfig]) -> tuple[StarboardConfig, ...]:
        if not rows:
            return ()
        emojis = (
            await session.scalars(
                select(StarboardEmoji)
                .where(StarboardEmoji.starboard_id.in_([row.id for row in rows]))
                .order_by(StarboardEmoji.position)
            )
        ).all()
        grouped: dict[int, list[StarboardEmoji]] = {}
        for emoji in emojis:
            emoji.validate()
            grouped.setdefault(emoji.starboard_id, []).append(emoji)
        for row in rows:
            row.emojis = tuple(grouped.get(row.id, ()))
            row.validate()
        return tuple(rows)

    async def _with_emojis(self, session: AsyncSession, row: StarboardConfig) -> StarboardConfig:
        return (await self._configs(session, [row]))[0]

    @staticmethod
    async def _replace_emojis(session: AsyncSession, starboard_id: int, emojis: Sequence[StarboardEmoji]) -> None:
        await session.execute(delete(StarboardEmoji).where(StarboardEmoji.starboard_id == starboard_id))
        if emojis:
            await session.execute(
                pg_insert(StarboardEmoji),
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
