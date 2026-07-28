"""Persistence and high-level operations for the Build domain object."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, cast

from async_lru import alru_cache
from rapidfuzz import process
from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from squid.db.builds import Build, JoinedBuildRecord
from squid.db.repos._build_lock import BuildLockTracker
from squid.db.schema import (
    Build as SQLBuild,
)
from squid.db.schema import (
    BuildCategory,
    BuildCreator,
    BuildLink,
    BuildRestriction,
    BuildType,
    BuildVersion,
    Door,
    LinkRecord,
    MediaTypeLiteral,
    Message,
    MessageRecord,
    Restriction,
    RestrictionRecord,
    RestrictionTypeLiteral,
    SmallestDoor,
    Status,
    Type,
    UnknownRestrictions,
    User,
    Version,
    VersionRecord,
)
from squid.utils import get_version_string

logger = logging.getLogger(__name__)


class BuildRepository:
    """Persistence and high-level operations on the Build domain object."""

    __slots__ = ("_locks", "_session_factory")

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._locks = BuildLockTracker()

    async def acquire_lock(self, build_id: int, *, blocking: bool = True, timeout: float = -1) -> bool:
        """Acquire a task-reentrant lease backed by the database lock flag."""
        task = self._locks.current_task()
        if self._locks.try_reenter(build_id, task):
            return True
        if not blocking:
            return await self._try_lock(build_id, task)

        sleep_time = 0.01
        started_at = time.monotonic()
        while True:
            if await self._try_lock(build_id, task):
                return True
            if timeout >= 0 and time.monotonic() - started_at >= timeout:
                return False
            await asyncio.sleep(sleep_time)
            sleep_time = min(sleep_time * 1.5, 0.5)

    async def _try_lock(self, build_id: int, task: asyncio.Task[object]) -> bool:
        if self._locks.is_held_locally(build_id):
            return False
        async with self._session_factory() as session:
            statement = (
                update(SQLBuild).where(SQLBuild.id == build_id, SQLBuild.is_locked.is_(False)).values(is_locked=True)
            )
            result = cast(CursorResult[Any], await session.execute(statement))
            await session.commit()
        if result.rowcount == 1:
            self._locks.record_acquired(build_id, task)
            return True
        return False

    async def release_lock(self, build_id: int) -> None:
        """Release one level of a process-local database-backed lease."""
        if not self._locks.release(build_id):
            return
        async with self._session_factory() as session:
            await session.execute(update(SQLBuild).where(SQLBuild.id == build_id).values(is_locked=False))
            await session.commit()

    @asynccontextmanager
    async def locked(self, build_id: int, *, timeout: float = 30) -> AsyncIterator[None]:
        """Hold a database-backed build lease for one operation."""
        if not await self.acquire_lock(build_id, timeout=timeout):
            msg = f"Timed out waiting for build {build_id} lock."
            raise TimeoutError(msg)
        try:
            yield
        finally:
            await self.release_lock(build_id)

    async def clean_stale_locks(self, *, older_than: datetime) -> None:
        """Release persisted locks older than a cutoff and forget local lease state."""
        async with self._session_factory() as session:
            await session.execute(update(SQLBuild).where(SQLBuild.locked_at < older_than).values(is_locked=False))
            await session.commit()
        self._locks.clear()

    async def get_by_id(self, build_id: int) -> Build | None:
        """Creates a new Build object from a database ID.

        Args:
            build_id: The ID of the build to retrieve.

        Returns:
            The Build object with the specified ID, or None if the build was not found.
        """
        async with self._session_factory() as session:
            stmt = select(SQLBuild).where(SQLBuild.id == build_id)
            result = await session.execute(stmt)
            sql_build = result.unique().scalar_one_or_none()
            if sql_build is None:
                return None
            return self.from_sql_build(sql_build)

    async def get_by_message_id(self, message_id: int) -> Build | None:
        """
        Get the build by a message id.

        Args:
            message_id: The message id to get the build from.

        Returns:
            The Build object with the specified message id, or None if the build was not found.
        """
        async with self._session_factory() as session:
            stmt = select(Message).where(Message.id == message_id)
            result = await session.execute(stmt)
            message = result.scalar_one_or_none()

            if message and message.build_id is not None:
                return await self.get_by_id(message.build_id)
            return None

    @staticmethod
    def _from_json(data: JoinedBuildRecord) -> Build:
        """
        Converts a JSON object to a Build object.

        Args:
            data: A legacy joined build record.

        Returns:
            A Build object.
        """
        id = data["id"]
        submission_status = data["submission_status"]
        record_category = data["record_category"]
        category = data["category"]

        width = data["width"]
        height = data["height"]
        depth = data["depth"]

        match data["category"]:
            case "Door":
                assert "doors" in data and data["doors"] is not None
                door_orientation_type = data["doors"]["orientation"]
                door_width = data["doors"]["door_width"]
                door_height = data["doors"]["door_height"]
                door_depth = data["doors"]["door_depth"]
                normal_closing_time = data["doors"]["normal_closing_time"]
                normal_opening_time = data["doors"]["normal_opening_time"]
                visible_closing_time = data["doors"]["visible_closing_time"]
                visible_opening_time = data["doors"]["visible_opening_time"]
            case "Extender":
                raise NotImplementedError
            case "Utility":
                raise NotImplementedError
            case "Entrance":
                raise NotImplementedError
            case _:
                msg = "Invalid category"
                raise ValueError(msg)

        # FIXME: This is hardcoded for now
        if types := data.get("types"):
            door_type = [type_["name"] for type_ in types]
        else:
            door_type = ["Regular"]

        restrictions: list[RestrictionRecord] = data.get("restrictions", [])
        wiring_placement_restrictions = [r["name"] for r in restrictions if r["type"] == "wiring-placement"]
        component_restrictions = [r["name"] for r in restrictions if r["type"] == "component"]
        miscellaneous_restrictions = [r["name"] for r in restrictions if r["type"] == "miscellaneous"]

        extra_info = data["extra_info"]

        creators = data.get("users", [])
        creators_ign = [creator["ign"] for creator in creators]

        version_spec = data["version_spec"]
        version_records: list[VersionRecord] = data.get("versions", [])
        versions = []
        for r in version_records:
            version = Version(r["edition"], r["major_version"], r["minor_version"], r["patch_number"])
            versions.append(get_version_string(version))

        links: list[LinkRecord] = data.get("build_links", [])
        image_urls = [link["url"] for link in links if link["media_type"] == "image"]
        video_urls = [link["url"] for link in links if link["media_type"] == "video"]
        world_download_urls = [link["url"] for link in links if link["media_type"] == "world-download"]

        submitter_id = data["submitter_id"]
        completion_time = data["completion_time"]
        edited_time = data["edited_time"]

        message_record: MessageRecord | None = data["messages"]
        if message_record is None:
            original_server_id = original_channel_id = original_message_id = original_message_author_id = None
            original_message = None
        else:
            original_server_id = message_record["server_id"]
            original_channel_id = message_record["channel_id"]
            original_message_id = data["original_message_id"]
            original_message_author_id = message_record["author_id"]
            original_message = message_record["content"]

        ai_generated = data["ai_generated"]
        embedding = data["embedding"]

        return Build(
            id=id,
            submission_status=Status(submission_status),
            record_category=record_category,
            category=BuildCategory(category),
            versions=versions,
            version_spec=version_spec,
            width=width,
            height=height,
            depth=depth,
            door_width=door_width,
            door_height=door_height,
            door_depth=door_depth,
            door_type=door_type,
            door_orientation_type=door_orientation_type,
            wiring_placement_restrictions=wiring_placement_restrictions,
            component_restrictions=component_restrictions,
            miscellaneous_restrictions=miscellaneous_restrictions,
            normal_closing_time=normal_closing_time,
            normal_opening_time=normal_opening_time,
            visible_closing_time=visible_closing_time,
            visible_opening_time=visible_opening_time,
            extra_info=extra_info,
            creators_ign=creators_ign,
            image_urls=image_urls,
            video_urls=video_urls,
            world_download_urls=world_download_urls,
            submitter_id=submitter_id,
            completion_time=completion_time,
            edited_time=datetime.strptime(edited_time, "%Y-%m-%dT%H:%M:%S%z"),
            original_server_id=original_server_id,
            original_channel_id=original_channel_id,
            original_message_id=original_message_id,
            original_message_author_id=original_message_author_id,
            original_message=original_message,
            ai_generated=ai_generated,
            embedding=embedding,
        )

    @staticmethod
    def from_sql_build(sql_build: SQLBuild) -> Build:
        """Converts a SQLBuild to a Build object."""
        if not isinstance(sql_build, Door):
            msg = "Can only handle doors right now."
            raise TypeError(msg)
        door = sql_build
        return Build(
            id=door.id,
            submission_status=door.submission_status,  # type: ignore
            category=BuildCategory(door.category),
            record_category=door.record_category,
            width=door.width,
            height=door.height,
            depth=door.depth,
            door_width=door.door_width,
            door_height=door.door_height,
            door_depth=door.door_depth,
            door_type=[type.name for type in door.types],
            door_orientation_type=door.orientation,
            wiring_placement_restrictions=[r.name for r in door.restrictions if r.type == "wiring-placement"],
            component_restrictions=[r.name for r in door.restrictions if r.type == "component"],
            miscellaneous_restrictions=[r.name for r in door.restrictions if r.type == "miscellaneous"],
            normal_closing_time=door.normal_closing_time,
            normal_opening_time=door.normal_opening_time,
            visible_closing_time=door.visible_closing_time,
            visible_opening_time=door.visible_opening_time,
            extra_info=door.extra_info,  # type: ignore
            creators_ign=[creator.ign for creator in door.creators],
            image_urls=[link.url for link in door.links if link.media_type == "image"],
            video_urls=[link.url for link in door.links if link.media_type == "video"],
            world_download_urls=[link.url for link in door.links if link.media_type == "world-download"],
            submitter_id=door.submitter_id,
            completion_time=door.completion_time,
            edited_time=door.edited_time,
            original_server_id=door.original_message.server_id if door.original_message else None,
            original_channel_id=door.original_message.channel_id if door.original_message else None,
            original_message_id=door.original_message_id,
            original_message_author_id=door.original_message.author_id if door.original_message else None,
            original_message=door.original_message.content if door.original_message else None,
            ai_generated=door.ai_generated,
            embedding=door.embedding,
        )

    async def save(self, build: Build) -> None:
        """
        Updates the build in the database with the given data.

        If the build does not exist in the database, it will be inserted instead.
        """
        build.edited_time = datetime.now(tz=UTC)

        if build.id is None:
            if build.submitter_id is None:
                msg = "Submitter ID must be set for new builds."
                raise ValueError(msg)

            # Create new build - determine the right subclass
            if build.category == BuildCategory.DOOR:
                sql_build = Door(
                    submission_status=build.submission_status or Status.PENDING,
                    record_category=build.record_category,
                    width=build.width,
                    height=build.height,
                    depth=build.depth,
                    completion_time=build.completion_time,
                    category=build.category,
                    submitter_id=build.submitter_id,
                    version_spec=build.version_spec,
                    ai_generated=build.ai_generated or False,
                    embedding=build.embedding,
                    extra_info=build.extra_info,
                    edited_time=build.edited_time,
                    is_locked=False,
                    orientation=build.door_orientation_type or "Door",
                    door_width=build.door_width or 1,
                    door_height=build.door_height or 2,
                    door_depth=build.door_depth,
                    normal_opening_time=build.normal_opening_time,
                    normal_closing_time=build.normal_closing_time,
                    visible_opening_time=build.visible_opening_time,
                    visible_closing_time=build.visible_closing_time,
                )
            else:
                msg = f"Only doors are supported for now, got {build.category}."
                raise ValueError(msg)

            async with self._session_factory() as session:
                await self._setup_relationships(build, session, sql_build)
                session.add(sql_build)
                await session.flush()
                build.id = sql_build.id
                if build.original_message_id is not None:
                    await self._create_or_update_message(build, session)
                sql_build.original_message_id = build.original_message_id
                await session.commit()
        else:
            async with self.locked(build.id):
                await self._update_existing(build)

    async def _update_existing(self, build: Build) -> None:
        """Persist an existing build while its repository lease is held."""
        assert build.id is not None
        if build.submission_status is None:
            msg = "Submission status must be set for existing builds."
            raise ValueError(msg)
        if build.submitter_id is None:
            msg = "Submitter ID must be set for existing builds."
            raise ValueError(msg)

        async with self._session_factory() as session:
            statement = (
                select(SQLBuild)
                .where(SQLBuild.id == build.id)
                .options(
                    selectinload(SQLBuild.build_creators).selectinload(BuildCreator.user),
                    selectinload(SQLBuild.build_restrictions).selectinload(BuildRestriction.restriction),
                    selectinload(SQLBuild.build_versions).selectinload(BuildVersion.version),
                    selectinload(SQLBuild.build_types).selectinload(BuildType.type),
                    selectinload(SQLBuild.links),
                    selectinload(SQLBuild.messages),
                )
            )
            sql_build = (await session.execute(statement)).scalar_one()
            sql_build.submission_status = build.submission_status
            sql_build.record_category = build.record_category
            sql_build.width = build.width
            sql_build.height = build.height
            sql_build.depth = build.depth
            sql_build.completion_time = build.completion_time
            sql_build.submitter_id = build.submitter_id
            sql_build.version_spec = build.version_spec
            sql_build.ai_generated = build.ai_generated or False
            sql_build.embedding = build.embedding
            sql_build.edited_time = build.edited_time

            if not isinstance(sql_build, Door):
                msg = f"Only doors are supported for now, got {sql_build.category}."
                raise TypeError(msg)
            sql_build.orientation = build.door_orientation_type or "Door"
            sql_build.door_width = build.door_width or 1
            sql_build.door_height = build.door_height or 2
            sql_build.door_depth = build.door_depth
            sql_build.normal_opening_time = build.normal_opening_time
            sql_build.normal_closing_time = build.normal_closing_time
            sql_build.visible_opening_time = build.visible_opening_time
            sql_build.visible_closing_time = build.visible_closing_time

            sql_build.build_creators.clear()
            sql_build.build_restrictions.clear()
            sql_build.build_versions.clear()
            sql_build.build_types.clear()
            sql_build.links.clear()
            await self._setup_relationships(build, session, sql_build)
            if build.original_message_id is not None:
                await self._create_or_update_message(build, session)
            sql_build.original_message_id = build.original_message_id
            await session.commit()

    async def _setup_relationships(self, build: Build, session: AsyncSession, sql_build: SQLBuild) -> None:
        """Set up all relationships for the build using SQLAlchemy's relationship handling."""
        # Handle creators
        if build.creators_ign:
            creators = await self._get_or_create_users(session, build.creators_ign)
            sql_build.creators = creators

        # Handle restrictions
        all_restrictions = (
            build.wiring_placement_restrictions + build.component_restrictions + build.miscellaneous_restrictions
        )
        if all_restrictions:
            restriction_objects, unknown_restrictions = await self._get_restrictions(build, session, all_restrictions)
            sql_build.restrictions = restriction_objects
            # Update extra_info with unknown restrictions
            if unknown_restrictions:
                build.extra_info["unknown_restrictions"] = (
                    build.extra_info.get("unknown_restrictions", {}) | unknown_restrictions
                )

        # Handle types
        if not build.door_type:
            build.door_type = ["Regular"]
        type_objects, unknown_types = await self._get_types(build, session, build.door_type)
        sql_build.types = type_objects
        # Update extra_info with unknown types
        if unknown_types:
            build.extra_info["unknown_patterns"] = build.extra_info.get("unknown_patterns", []) + unknown_types

        # Handle versions
        version_objects = await self._get_versions(session, build.versions)
        sql_build.versions = version_objects

        # Handle links
        all_links: list[tuple[str, MediaTypeLiteral]] = []
        if build.image_urls:
            all_links.extend([(url, "image") for url in build.image_urls])
        if build.video_urls:
            all_links.extend([(url, "video") for url in build.video_urls])
        if build.world_download_urls:
            all_links.extend([(url, "world-download") for url in build.world_download_urls])

        for url, media_type in all_links:
            build_link = BuildLink(url=url, media_type=media_type)
            sql_build.links.append(build_link)

    @staticmethod
    async def _get_or_create_users(session: AsyncSession, igns: list[str]) -> list[User]:
        """Get or create User objects for the given IGNs."""
        users: list[User] = []
        for ign in igns:
            stmt = select(User).where(User.ign == ign)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()

            if user is None:
                user = User(ign=ign)
                session.add(user)
                await session.flush()  # Get the ID

            users.append(user)

        return users

    @staticmethod
    async def _get_restrictions(
        build: Build, session: AsyncSession, restrictions: list[str]
    ) -> tuple[list[Restriction], UnknownRestrictions]:
        """Get Restriction objects and identify unknown restrictions."""
        restrictions_titled = [r.title() for r in restrictions]

        stmt = select(Restriction).where(Restriction.name.in_(restrictions_titled))
        result = await session.execute(stmt)
        found_restrictions = result.scalars().all()

        # Identify unknown restrictions by type
        unknown_restrictions: UnknownRestrictions = {}
        found_names = {r.name for r in found_restrictions}

        unknown_wiring = [r for r in build.wiring_placement_restrictions if r.title() not in found_names]
        unknown_component = [r for r in build.component_restrictions if r.title() not in found_names]
        unknown_misc = [r for r in build.miscellaneous_restrictions if r.title() not in found_names]

        if unknown_wiring:
            unknown_restrictions["wiring_placement_restrictions"] = unknown_wiring
        if unknown_component:
            unknown_restrictions["component_restrictions"] = unknown_component
        if unknown_misc:
            unknown_restrictions["miscellaneous_restrictions"] = unknown_misc

        return list(found_restrictions), unknown_restrictions

    @staticmethod
    async def _get_types(build: Build, session: AsyncSession, type_names: list[str]) -> tuple[list[Type], list[str]]:
        """Get Type objects and identify unknown types."""
        type_names_titled = [t.title() for t in type_names]

        stmt = select(Type).where(Type.build_category == build.category).where(Type.name.in_(type_names_titled))
        result = await session.execute(stmt)
        found_types = result.scalars().all()

        found_names = {t.name for t in found_types}
        unknown_types = [t for t in type_names if t.title() not in found_names]

        return list(found_types), unknown_types

    @staticmethod
    async def _get_versions(session: AsyncSession, version_strings: list[str]) -> list[Version]:
        """Get Version objects for the given version strings."""
        qvn = func.get_quantified_version_names().table_valued("id", "quantified_name").alias("qvn")

        stmt = select(qvn.c.id).where(qvn.c.quantified_name.in_(version_strings))
        result = await session.execute(stmt)
        version_ids = [tup[0] for tup in result.all()]  # result is a list of 1-tuples

        stmt = select(Version).where(Version.id.in_(version_ids))
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def _create_or_update_message(build: Build, session: AsyncSession) -> None:
        """Create or update the original message record."""
        if build.original_message_id is None:
            return
        assert build.original_server_id is not None, "Original server ID must be set for original message."
        # Channel ID may be None if the message is from DMs
        assert build.original_message_author_id is not None, (
            "Original message author ID must be set for original message."
        )

        stmt = select(Message).where(Message.id == build.original_message_id)
        result = await session.execute(stmt)
        message = result.scalar_one_or_none()

        if message is None:
            message = Message(
                id=build.original_message_id,
                server_id=build.original_server_id,
                channel_id=build.original_channel_id,
                author_id=build.original_message_author_id,
                purpose="build_original_message",
                content=build.original_message,
                build_id=build.id,
            )
            session.add(message)
        else:
            message.server_id = build.original_server_id
            message.channel_id = build.original_channel_id
            message.author_id = build.original_message_author_id
            message.purpose = "build_original_message"
            message.content = build.original_message
            message.build_id = build.id
            message.updated_at = datetime.now(tz=UTC)
        await session.flush()

    async def confirm(self, build: Build) -> None:
        """Marks the build as confirmed.

        Raises:
            ValueError: If the build could not be confirmed.
        """
        if build.id is None:
            msg = "Build ID is missing."
            raise ValueError(msg)

        async with self.locked(build.id):
            build.submission_status = Status.CONFIRMED
            async with self._session_factory() as session:
                stmt = update(SQLBuild).where(SQLBuild.id == build.id).values(submission_status=Status.CONFIRMED)
                result = cast(CursorResult[Any], await session.execute(stmt))
                await session.commit()
                if result.rowcount != 1:
                    msg = "Failed to confirm submission in the database."
                    raise ValueError(msg)

    async def deny(self, build: Build) -> None:
        """Marks the build as denied.

        Raises:
            ValueError: If the build could not be denied.
        """
        if build.id is None:
            msg = "Build ID is missing."
            raise ValueError(msg)

        async with self.locked(build.id):
            build.submission_status = Status.DENIED
            async with self._session_factory() as session:
                stmt = update(SQLBuild).where(SQLBuild.id == build.id).values(submission_status=Status.DENIED)
                result = cast(CursorResult[Any], await session.execute(stmt))
                await session.commit()
                if result.rowcount != 1:
                    msg = "Failed to deny submission in the database."
                    raise ValueError(msg)

    async def get_pending(self) -> list[Build]:
        """Return pending builds with the relationships required by the domain mapper."""
        async with self._session_factory() as session:
            statement = (
                select(Door)
                .where(Door.submission_status == Status.PENDING)
                .options(
                    selectinload(Door.build_creators).selectinload(BuildCreator.user),
                    selectinload(Door.build_restrictions).selectinload(BuildRestriction.restriction),
                    selectinload(Door.build_versions).selectinload(BuildVersion.version),
                    selectinload(Door.build_types).selectinload(BuildType.type),
                    selectinload(Door.links),
                    selectinload(Door.messages),
                )
            )
            result = await session.execute(statement)
            return [self.from_sql_build(build) for build in result.unique().scalars().all()]

    async def get_builds_by_id(self, build_ids: list[int]) -> list[Build | None]:
        """Fetches builds from the database with the given IDs."""
        if len(build_ids) == 0:
            return []

        async with self._session_factory() as session:
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
                builds[idx] = self.from_sql_build(sql_build)
            return builds

    async def get_unsent_builds(self, server_id: int) -> list[Build] | None:
        """Get all the builds that have not been posted on the server"""
        raise NotImplementedError

    async def _get_smallest_door_records_without_title_in_db(self) -> Sequence[SmallestDoor]:
        """Get all the smallest door records that do not have a title in the database."""
        stmt = select(SmallestDoor).where(SmallestDoor.title.is_(None))
        async with self._session_factory() as session:
            result = await session.execute(stmt)
            return result.scalars().all()

    async def update_smallest_door_records_without_title(self) -> None:
        """Update the titles of all records in the database."""
        smallest_door_records_without_title = await self._get_smallest_door_records_without_title_in_db()
        async with self._session_factory() as session:
            restriction_definitions: dict[str, RestrictionTypeLiteral | None] = {
                restriction.name: restriction.type for restriction in (await session.scalars(select(Restriction))).all()
            }
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
                build.classify_restrictions(door.restriction_subset, restriction_definitions)
                door.title = build.title
                session.add(door)
            await session.commit()

    @alru_cache(ttl=3600)  # 1 hour
    async def fetch_all_smallest_door_records(self) -> Sequence[SmallestDoor]:
        stmt = select(SmallestDoor)
        async with self._session_factory() as session:
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
