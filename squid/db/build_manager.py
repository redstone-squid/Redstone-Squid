from __future__ import annotations

import logging
from typing import Any, Mapping, Sequence

from async_lru import alru_cache
from rapidfuzz import process
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from squid.db import DatabaseManager
from squid.db.builds import Build, Status, all_build_columns
from squid.db.schema import (
    Build as SQLBuild,
)
from squid.db.schema import (
    BuildCategory,
    BuildCreator,
    BuildRestriction,
    BuildType,
    BuildVersion,
    Message,
    SmallestDoor,
    Status,
)

logger = logging.getLogger(__name__)


class BuildManager:
    """Service layer responsible for persistence and high-level operations on Build domain object."""

    __slots__ = ("session",)

    def __init__(self, session: async_sessionmaker[AsyncSession]) -> None:
        self.session = session

    async def get_by_id(self, build_id: int) -> Build | None:
        """Creates a new Build object from a database ID.

        Args:
            build_id: The ID of the build to retrieve.

        Returns:
            The Build object with the specified ID, or None if the build was not found.
        """
        async with self.session() as session:
            stmt = select(SQLBuild).where(SQLBuild.id == build_id)
            result = await session.execute(stmt)
            sql_build = result.unique().scalar_one_or_none()
            if sql_build is None:
                return None
            return Build.from_sql_build(sql_build)

    async def get_by_message_id(self, message_id: int) -> Build | None:
        """
        Get the build by a message id.

        Args:
            message_id: The message id to get the build from.

        Returns:
            The Build object with the specified message id, or None if the build was not found.
        """
        async with self.session() as session:
            stmt = select(Message).where(Message.id == message_id)
            result = await session.execute(stmt)
            message = result.scalar_one_or_none()

            if message and message.build_id is not None:
                return await self.get_by_id(message.build_id)
            return None

    async def confirm(self, build: Build) -> None:
        """Marks the build as confirmed.

        Raises:
            ValueError: If the build could not be confirmed.
        """
        if build.id is None:
            raise ValueError("Build ID is missing.")

        async with build.lock(timeout=30):
            build.submission_status = Status.CONFIRMED
            async with self.session() as session:
                stmt = update(SQLBuild).where(SQLBuild.id == build.id).values(submission_status=Status.CONFIRMED)
                result = await session.execute(stmt)
                await session.commit()
                if result.rowcount != 1:
                    raise ValueError("Failed to confirm submission in the database.")

    async def deny(self, build: Build) -> None:
        """Marks the build as denied.

        Raises:
            ValueError: If the build could not be denied.
        """
        if build.id is None:
            raise ValueError("Build ID is missing.")

        async with build.lock(timeout=30):
            build.submission_status = Status.DENIED
            async with self.session() as session:
                stmt = update(SQLBuild).where(SQLBuild.id == build.id).values(submission_status=Status.DENIED)
                result = await session.execute(stmt)
                await session.commit()
                if result.rowcount != 1:
                    raise ValueError("Failed to deny submission in the database.")

    async def get_builds_by_filter(self, *, filter: Mapping[str, Any] | None = None) -> list[Build]:
        """Fetches all builds from the database, optionally filtered by submission status.

        Args:
            filter: A dictionary containing filter criteria, only exact matches are supported.

                A filter is of the format {"column_name": value}, where column_name is the name of the column
                in the database and value is the value to filter by. In general, the attribute names of the Build class
                are the same, but in some cases they are different and the only way to know is to look at the database schema.
                Also, if the attribute you are trying to filter is not in the builds table, you will need to use a join table
                syntax.

                For example, to filter by submission status, use {"submission_status": 1}. To filter by door opening time,
                use {"doors(normal_opening_time)": 0.5}. where doors is a join table. The join is automatically done by
                the supabase client when you use the `select` method with the correct column name.

        Returns:
            A list of Build objects.
        """
        # TODO: This is not trtivial in SQLAlchemy, so we keep the supabase client to do this.
        db = DatabaseManager()
        query = db.table("builds").select(all_build_columns)

        if filter is not None:  # TODO: Support more complex filters (in_ being the most important)
            for column, value in filter.items():
                query = query.eq(column, value)

        response = await query.execute()
        if not response:
            return []
        else:
            return [Build.from_json(build_json) for build_json in response.data]

    async def get_builds_by_id(self, build_ids: list[int]) -> list[Build | None]:
        """Fetches builds from the database with the given IDs."""
        if len(build_ids) == 0:
            return []

        db = DatabaseManager()
        async with db.async_session() as session:
            stmt = (
                select(SQLBuild)
                .options(
                    selectinload(SQLBuild.build_creators).selectinload(BuildCreator.user),
                    selectinload(SQLBuild.build_restrictions).selectinload(BuildRestriction.restriction),
                    selectinload(SQLBuild.build_versions).selectinload(BuildVersion.version),
                    selectinload(SQLBuild.build_types).selectinload(BuildType.type),
                    selectinload(SQLBuild.links),
                    selectinload(SQLBuild.messages),
                )
                .where(SQLBuild.id.in_(build_ids))
            )
            result = await session.execute(stmt)
            sql_builds = result.scalars().all()

            # Create result list with None placeholders
            builds: list[Build | None] = [None] * len(build_ids)

            # Fill in the found builds at their correct positions
            for sql_build in sql_builds:
                idx = build_ids.index(sql_build.id)
                builds[idx] = Build.from_sql_build(sql_build)
            return builds

    async def get_unsent_builds(self, server_id: int) -> list[Build] | None:
        """Get all the builds that have not been posted on the server"""
        # TODO: Convert this to SQLAlchemy. (I believe it is not working right now anyways, because from_json demands
        #   a build joined with many other tables, but get_unsent_builds only returns the builds table.)
        raise NotImplementedError
        # db = DatabaseManager()
        #
        # # Builds that have not been posted on the server
        # response = await db.rpc("get_unsent_builds", {"server_id_input": server_id}).execute()
        # server_unsent_builds = response.data
        # return [Build.from_json(unsent_sub) for unsent_sub in server_unsent_builds]

    async def _get_smallest_door_records_without_title_in_db(self) -> Sequence[SmallestDoor]:
        """Get all the smallest door records that do not have a title in the database."""
        stmt = select(SmallestDoor).where(SmallestDoor.title.is_(None))
        async with self.session() as session:
            result = await session.execute(stmt)
            smallest_doors = result.scalars().all()
        return smallest_doors

    async def update_smallest_door_records_without_title(self) -> None:
        """Update the titles of all records in the database."""
        smallest_door_records_without_title = await self._get_smallest_door_records_without_title_in_db()
        async with self.session() as session:
            for door in smallest_door_records_without_title:
                # Generate a title based on the door's attributes
                build = Build(
                    id=door.id,
                    # These are invariants by the fact that they are in the smallest_door_records table
                    record_category="Smallest",
                    category=BuildCategory.DOOR,
                    submission_status=Status.CONFIRMED,
                    # We assume ai_generated is False to generate the simpler title
                    ai_generated=False,
                    # from the table
                    door_width=door.door_width,
                    door_height=door.door_height,
                    door_depth=door.door_depth,
                    door_type=door.types,
                    door_orientation_type=door.orientation,
                )
                await build.set_restrictions(door.restriction_subset)
                title = build.get_title()
                door.title = title
                session.add(door)
            await session.commit()

    @alru_cache(ttl=3600)  # 1 hour
    async def fetch_all_smallest_door_records(self) -> Sequence[SmallestDoor]:
        stmt = select(SmallestDoor)
        async with self.session() as session:
            result = await session.execute(stmt)
            return result.scalars().all()

    @alru_cache(ttl=3600)  # 1 hour
    async def search_smallest_door_records(self, query: str, limit: int = 25) -> list[tuple[SmallestDoor, float, int]]:
        """Search for smallest door records by title."""
        records = await self.fetch_all_smallest_door_records()
        records = [r for r in records if r.title is not None]  # Filter out records without titles

        def processor(raw: str | SmallestDoor) -> str:
            if isinstance(raw, SmallestDoor):
                return raw.title  # type: ignore  # Title is never None here
            return raw

        return process.extract(query, records, limit=limit, processor=processor)
