"""Persistence for smallest-door record maintenance and search."""

from collections.abc import Sequence
from typing import cast

from async_lru import alru_cache
from rapidfuzz import process
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.builds.application.queries import SmallestDoorRecord
from squid.builds.domain import Build, BuildCategory, RestrictionTypeLiteral, Status
from squid.builds.infrastructure.models import Restriction, SmallestDoor


class SmallestDoorRecordRepository:
    """Maintain and search the denormalized smallest-door records."""

    __slots__ = ("_session_factory",)

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def _get_records_without_title(self) -> Sequence[SmallestDoor]:
        statement = select(SmallestDoor).where(SmallestDoor.title.is_(None))
        async with self._session_factory() as session:
            result = await session.execute(statement)
            return result.scalars().all()

    async def update_records_without_title(self) -> None:
        records = await self._get_records_without_title()
        async with self._session_factory() as session:
            restriction_definitions: dict[str, RestrictionTypeLiteral | None] = {
                restriction.name: restriction.type for restriction in (await session.scalars(select(Restriction))).all()
            }
            for door in records:
                build = Build(
                    id=door.id,
                    record_category="Smallest",
                    category=BuildCategory.DOOR,
                    submission_status=Status.CONFIRMED,
                    ai_generated=False,
                    door_width=door.door_width,
                    door_height=door.door_height,
                    door_depth=door.door_depth,
                    door_type=door.types,
                    door_orientation_type=door.orientation,
                )
                build.classify_restrictions(door.restriction_subset, restriction_definitions)
                door.title = build.title
                session.add(door)
            await session.commit()

    @alru_cache(ttl=3600)
    async def fetch_all(self) -> Sequence[SmallestDoor]:
        async with self._session_factory() as session:
            result = await session.execute(select(SmallestDoor))
            return result.scalars().all()

    async def search(self, query: str, limit: int = 25) -> list[tuple[SmallestDoorRecord, float, int]]:
        records = [record for record in await self.fetch_all() if record.title is not None]

        def processor(raw: str | SmallestDoor) -> str:
            if isinstance(raw, SmallestDoor):
                return cast(str, raw.title)
            return raw

        matches = process.extract(query, records, limit=limit, processor=processor)
        return [
            (SmallestDoorRecord(record.id, cast(str, record.title)), score, index) for record, score, index in matches
        ]
