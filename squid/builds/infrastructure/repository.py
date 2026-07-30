"""Persistence and high-level operations for the Build domain object."""

from __future__ import annotations

import logging
from typing import Any, cast

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload
from whenever import Instant

from squid.builds.application.queries import SmallestDoorRecord
from squid.builds.domain import (
    Build,
    BuildCategory,
    MediaTypeLiteral,
    Status,
    UnknownRestrictions,
)
from squid.builds.errors import InvalidBuildError
from squid.builds.infrastructure.mapping import BuildMapper
from squid.builds.infrastructure.models import (
    Build as SQLBuild,
)
from squid.builds.infrastructure.models import (
    BuildCreator,
    BuildLink,
    BuildRestriction,
    BuildType,
    BuildVersion,
    Door,
    Restriction,
    Type,
)
from squid.builds.infrastructure.records import SmallestDoorRecordRepository
from squid.core.errors import InvalidStateError, PersistenceError
from squid.messages.infrastructure.models import Message
from squid.users.infrastructure.models import User
from squid.versions.infrastructure.models import Version

logger = logging.getLogger(__name__)


class BuildRepository:
    """Persistence and high-level operations on the Build domain object."""

    __slots__ = ("_mapper", "_records", "_session_factory")

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._mapper = BuildMapper()
        self._records = SmallestDoorRecordRepository(session_factory)

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
            return await self._mapper.to_domain(session, sql_build)

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

    async def save(self, build: Build) -> None:
        """
        Updates the build in the database with the given data.

        If the build does not exist in the database, it will be inserted instead.
        """
        build.edited_time = Instant.now()

        if build.id is None:
            if build.submitter_id is None:
                msg = "Submitter ID must be set for new builds."
                raise InvalidStateError(msg, context={"resource": "build"})

            # Create new build - determine the right subclass
            if build.category == BuildCategory.DOOR:
                sql_build = Door(
                    submission_status=build.submission_status or Status.PENDING,
                    record_category=None,
                    width=build.width,
                    height=build.height,
                    depth=build.depth,
                    completion_time=build.completion_time,
                    completion_at=build.completion_at,
                    completion_evidence=build.completion_evidence,
                    description=build.description,
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
                raise InvalidBuildError(msg, context={"category": build.category})

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
            await self._update_existing(build)

    async def _update_existing(self, build: Build) -> None:
        """Persist an existing build while its repository lease is held."""
        assert build.id is not None
        if build.submission_status is None:
            msg = "Submission status must be set for existing builds."
            raise InvalidStateError(msg, context={"build_id": build.id})
        if build.submitter_id is None:
            msg = "Submitter ID must be set for existing builds."
            raise InvalidStateError(msg, context={"build_id": build.id})

        async with self._session_factory() as session:
            statement = (
                select(SQLBuild)
                .where(SQLBuild.id == build.id)
                .options(
                    selectinload(SQLBuild.build_creators),
                    selectinload(SQLBuild.build_restrictions).selectinload(BuildRestriction.restriction),
                    selectinload(SQLBuild.build_versions),
                    selectinload(SQLBuild.build_types).selectinload(BuildType.type),
                    selectinload(SQLBuild.links),
                )
            )
            sql_build = (await session.execute(statement)).scalar_one()
            sql_build.submission_status = build.submission_status
            sql_build.width = build.width
            sql_build.height = build.height
            sql_build.depth = build.depth
            sql_build.completion_time = build.completion_time
            sql_build.completion_at = build.completion_at
            sql_build.completion_evidence = build.completion_evidence
            sql_build.description = build.description
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
            sql_build.build_creators.extend(BuildCreator(user_id=user.id) for user in creators)

        # Handle restrictions
        all_restrictions = (
            build.wiring_placement_restrictions + build.component_restrictions + build.miscellaneous_restrictions
        )
        if all_restrictions:
            restriction_objects, unknown_restrictions = await self._get_restrictions(build, session, all_restrictions)
            sql_build.build_restrictions.extend(
                BuildRestriction(restriction=restriction) for restriction in restriction_objects
            )
            # Update extra_info with unknown restrictions
            if unknown_restrictions:
                merged_restrictions: UnknownRestrictions = {}
                merged_restrictions.update(build.extra_info.get("unknown_restrictions", {}))
                merged_restrictions.update(unknown_restrictions)
                build.extra_info["unknown_restrictions"] = merged_restrictions

        # Handle types
        if not build.door_type:
            build.door_type = ["Regular"]
        type_objects, unknown_types = await self._get_types(build, session, build.door_type)
        sql_build.build_types.extend(BuildType(type=build_type) for build_type in type_objects)
        # Update extra_info with unknown types
        if unknown_types:
            build.extra_info["unknown_patterns"] = build.extra_info.get("unknown_patterns", []) + unknown_types

        # Handle versions
        version_objects = await self._get_versions(session, build.versions)
        sql_build.build_versions.extend(BuildVersion(version_id=version.id) for version in version_objects)

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
            message.updated_at = Instant.now()
        await session.flush()

    async def confirm(self, build: Build) -> None:
        """Marks the build as confirmed.

        Raises:
            ValueError: If the build could not be confirmed.
        """
        if build.id is None:
            msg = "Build ID is missing."
            raise InvalidStateError(msg, context={"operation": "confirm"})

        build.submission_status = Status.CONFIRMED
        async with self._session_factory() as session:
            stmt = update(SQLBuild).where(SQLBuild.id == build.id).values(submission_status=Status.CONFIRMED)
            result = cast(CursorResult[Any], await session.execute(stmt))
            await session.commit()
            if result.rowcount != 1:
                msg = "Failed to confirm submission in the database."
                raise PersistenceError(msg, context={"build_id": build.id, "operation": "confirm"})

    async def deny(self, build: Build) -> None:
        """Marks the build as denied.

        Raises:
            ValueError: If the build could not be denied.
        """
        if build.id is None:
            msg = "Build ID is missing."
            raise InvalidStateError(msg, context={"operation": "deny"})

        build.submission_status = Status.DENIED
        async with self._session_factory() as session:
            stmt = update(SQLBuild).where(SQLBuild.id == build.id).values(submission_status=Status.DENIED)
            result = cast(CursorResult[Any], await session.execute(stmt))
            await session.commit()
            if result.rowcount != 1:
                msg = "Failed to deny submission in the database."
                raise PersistenceError(msg, context={"build_id": build.id, "operation": "deny"})

    async def get_pending(self) -> list[Build]:
        """Return pending builds with the relationships required by the domain mapper."""
        async with self._session_factory() as session:
            statement = (
                select(Door)
                .where(Door.submission_status == Status.PENDING)
                .options(
                    selectinload(Door.build_restrictions).selectinload(BuildRestriction.restriction),
                    selectinload(Door.build_types).selectinload(BuildType.type),
                    selectinload(Door.links),
                )
            )
            result = await session.execute(statement)
            return [await self._mapper.to_domain(session, build) for build in result.unique().scalars().all()]

    async def get_builds_by_id(self, build_ids: list[int]) -> list[Build | None]:
        """Fetches builds from the database with the given IDs."""
        if len(build_ids) == 0:
            return []

        async with self._session_factory() as session:
            stmt = (
                select(SQLBuild)
                .options(
                    selectinload(SQLBuild.build_restrictions).selectinload(BuildRestriction.restriction),
                    selectinload(SQLBuild.build_types).selectinload(BuildType.type),
                    selectinload(SQLBuild.links),
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
                builds[idx] = await self._mapper.to_domain(session, sql_build)
            return builds

    async def get_unsent_builds(self, server_id: int) -> list[Build] | None:
        """Get all the builds that have not been posted on the server"""
        raise NotImplementedError

    async def update_smallest_door_records_without_title(self) -> None:
        await self._records.update_records_without_title()

    async def search_smallest_door_records(
        self, query: str, limit: int = 25
    ) -> list[tuple[SmallestDoorRecord, float, int]]:
        return await self._records.search(query, limit)
