from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

import vecs
from async_lru import alru_cache
from postgrest.base_request_builder import APIResponse
from rapidfuzz import process
from sqlalchemy import select, update, func, delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from squid.db.builds import Build, all_build_columns, JoinedBuildRecord
from squid.db.schema import (
    Build as SQLBuild,
    RestrictionRecord,
    VersionRecord,
    Version,
    LinkRecord,
    MessageRecord,
    Door,
    Type,
    Restriction,
    UnknownRestrictions,
    User,
    BuildLink,
    MediaTypeLiteral,
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
from squid.utils import get_version_string

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
            return self.from_sql_build(sql_build)

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

    @staticmethod
    def _from_json(data: JoinedBuildRecord) -> "Build":
        """
        Converts a JSON object to a Build object.

        Args:
            data: the exact JSON object returned by
                `DatabaseManager().table('builds').select(all_build_columns).eq('id', build_id).execute().data[0]`

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
                raise ValueError("Invalid category")

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
    def from_sql_build(sql_build: SQLBuild) -> "Build":
        """Converts a SQLBuild to a Build object."""
        if not isinstance(sql_build, Door):
            raise ValueError("Can only handle doors right now.")
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
        build.edited_time = datetime.now(tz=timezone.utc)

        if build.id is None:
            delete_build_on_error = True
            if build.submitter_id is None:
                raise ValueError("Submitter ID must be set for new builds.")

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
                    is_locked=True,  # Lock immediately on creation
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
                raise ValueError(f"Only doors are supported for now, got {build.category}.")

            async with self.session() as session:
                await self._setup_relationships(session, sql_build)
                session.add(sql_build)
                await session.commit()
                build.id = sql_build.id
            build.lock._lock_count = 1  # pyright: ignore[reportPrivateUsage]
        else:
            delete_build_on_error = False
            await build.lock.acquire(timeout=30)

            async with self.session() as session:
                # Load existing build with all relationships
                stmt = (
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
                result = await session.execute(stmt)
                sql_build = result.scalar_one()

                # Update basic attributes
                if build.submission_status is None:
                    raise ValueError("Submission status must be set for existing builds.")
                if build.submitter_id is None:
                    raise ValueError("Submitter ID must be set for existing builds.")
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

                # Update category-specific attributes
                if isinstance(sql_build, Door):
                    sql_build.orientation = build.door_orientation_type or "Door"
                    sql_build.door_width = build.door_width or 1
                    sql_build.door_height = build.door_height or 2
                    sql_build.door_depth = build.door_depth
                    sql_build.normal_opening_time = build.normal_opening_time
                    sql_build.normal_closing_time = build.normal_closing_time
                    sql_build.visible_opening_time = build.visible_opening_time
                    sql_build.visible_closing_time = build.visible_closing_time
                else:
                    raise ValueError(f"Only doors are supported for now, got {sql_build.category}.")

                # Clear existing relationships and set up new ones
                sql_build.build_creators.clear()
                sql_build.build_restrictions.clear()
                sql_build.build_versions.clear()
                sql_build.build_types.clear()
                sql_build.links.clear()

                await self._setup_relationships(session, sql_build)
                await session.commit()

        # Handle embedding and vector storage
        try:
            embedding_task = asyncio.create_task(build.generate_embedding())

            # Handle message separately since it might update extra_info
            if build.original_message_id is not None:
                async with self.session() as session:
                    await self._create_or_update_message(build, session)

            # Update embedding
            build.embedding = await embedding_task
            if build.embedding is not None:
                vx = vecs.create_client(os.environ["DB_CONNECTION"])
                try:
                    build_vecs = vx.get_or_create_collection(
                        name="builds", dimension=int(os.getenv("EMBEDDING_DIMENSION", "1536"))
                    )
                    build_vecs.upsert(records=[(str(build.id), build.embedding, {})])
                finally:
                    vx.disconnect()

                # Update embedding in database
                async with self.session() as session:
                    stmt = (
                        update(SQLBuild)
                        .where(SQLBuild.id == build.id)
                        .values(embedding=build.embedding, extra_info=build.extra_info)
                    )
                    await session.execute(stmt)
                    await session.commit()

        except Exception:
            if delete_build_on_error:
                logger.warning("Failed to save build %s, deleting it", repr(build))
                async with self.session() as session:
                    stmt = delete(SQLBuild).where(SQLBuild.id == build.id)
                    await session.execute(stmt)
                    await session.commit()
            else:
                logger.error("Failed to update build %s. This means the build is in an inconsistent state.", repr(build))
            raise
        finally:
            build.lock.build_id = build.id
            await build.lock.release()

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
            restriction_objects, unknown_restrictions = await self._get_restrictions(session, all_restrictions)
            sql_build.restrictions = restriction_objects
            # Update extra_info with unknown restrictions
            if unknown_restrictions:
                build.extra_info["unknown_restrictions"] = (
                    build.extra_info.get("unknown_restrictions", {}) | unknown_restrictions
                )

        # Handle types
        if not build.door_type:
            build.door_type = ["Regular"]
        type_objects, unknown_types = await self._get_types(session, build.door_type)
        sql_build.types = type_objects
        # Update extra_info with unknown types
        if unknown_types:
            build.extra_info["unknown_patterns"] = build.extra_info.get("unknown_patterns", []) + unknown_types

        # Handle versions
        from squid.db import DatabaseManager  # FIXME
        functional_versions = build.versions or [await DatabaseManager().get_or_fetch_newest_version(edition="Java")]
        version_objects = await self._get_versions(session, functional_versions)
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
        build, session: AsyncSession, restrictions: list[str]
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
            message.updated_at = datetime.now(tz=timezone.utc)
        await session.flush()

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

        response: APIResponse[JoinedBuildRecord] = await query.execute()
        if not response:
            return []
        else:
            return [self._from_json(build_json) for build_json in response.data]

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
                builds[idx] = self.from_sql_build(sql_build)
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
                await build.set_restrictions_auto(door.restriction_subset)
                door.title = build.title
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
