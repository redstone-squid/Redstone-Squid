"""Persistence and high-level operations for the Build domain object."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload
from whenever import Instant

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
from squid.core.errors import InvalidStateError, PersistenceError
from squid.messages.infrastructure.models import Message
from squid.tags.domain import TagAssignment as DomainTagAssignment
from squid.tags.domain import TagSemanticKind, TagValueType
from squid.tags.infrastructure.models import (
    BuildTagAssignment as SQLTagAssignment,
)
from squid.tags.infrastructure.models import (
    TagAlias,
    TagApplicability,
)
from squid.tags.infrastructure.models import (
    TagDefinition as SQLTagDefinition,
)
from squid.users.domain import normalize_ign
from squid.users.infrastructure.models import CreatorAlias, User
from squid.versions.infrastructure.models import Version

logger = logging.getLogger(__name__)


class BuildRepository:
    """Persistence and high-level operations on the Build domain object."""

    __slots__ = ("_mapper", "_session_factory")

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._mapper = BuildMapper()

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

            if build.category != BuildCategory.DOOR:
                msg = f"Only doors are supported for now, got {build.category}."
                raise InvalidBuildError(msg, context={"category": build.category})

            async with self._session_factory() as session:
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
                    submitter_user_id=await self._get_or_create_account(session, build.submitter_id),
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
                    selectinload(SQLBuild.tag_assignments).selectinload(SQLTagAssignment.definition),
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
            sql_build.submitter_user_id = await self._get_or_create_account(session, build.submitter_id)
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
            sql_build.tag_assignments.clear()
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
            alias_ids = await self._get_or_create_aliases(session, build.creators_ign)
            sql_build.build_creators.extend(BuildCreator(alias_id=alias_id) for alias_id in alias_ids)

        # Handle restrictions
        all_restrictions = (
            build.wiring_placement_restrictions
            + build.animated_restrictions
            + build.component_restrictions
            + build.miscellaneous_restrictions
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

        await self._setup_tag_assignments(build, session, sql_build)

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
        if build.schematic_urls:
            all_links.extend([(url, "schematic") for url in build.schematic_urls])
        if build.render_urls:
            all_links.extend([(url, "render") for url in build.render_urls])

        for url, media_type in all_links:
            build_link = BuildLink(url=url, media_type=media_type)
            sql_build.links.append(build_link)

    async def _setup_tag_assignments(
        self,
        build: Build,
        session: AsyncSession,
        sql_build: SQLBuild,
    ) -> None:
        assignments = build.tags
        if not assignments:
            assignments = await self._legacy_tag_assignments(build, session)
            build.tags = assignments
        if not assignments:
            return

        definitions = {
            definition.id: definition
            for definition in (
                await session.scalars(
                    select(SQLTagDefinition).where(
                        SQLTagDefinition.id.in_({assignment.definition.id for assignment in assignments})
                    )
                )
            ).all()
        }
        missing = sorted({assignment.definition.id for assignment in assignments} - definitions.keys())
        if missing:
            msg = f"Unknown tag definition IDs: {missing}"
            raise InvalidBuildError(msg, context={"build_id": build.id, "tag_ids": missing})

        for assignment in assignments:
            definition = definitions[assignment.definition.id]
            if definition.value_type != assignment.definition.value_type:
                msg = f"Tag {definition.stable_key} value type changed while the build was being edited."
                raise InvalidBuildError(msg, context={"tag_id": definition.id})
            numeric_value, text_value, boolean_value = _split_tag_value(assignment)
            sql_build.tag_assignments.append(
                SQLTagAssignment(
                    definition=definition,
                    value_type=assignment.definition.value_type,
                    numeric_value=numeric_value,
                    text_value=text_value,
                    boolean_value=boolean_value,
                    display_unit_key=assignment.display_unit,
                    display_order=assignment.display_order,
                    evidence=assignment.evidence,
                    provenance=assignment.provenance,
                    created_by_discord_id=build.submitter_id,
                )
            )

    async def _legacy_tag_assignments(self, build: Build, session: AsyncSession) -> list[DomainTagAssignment]:
        restrictions = (
            build.wiring_placement_restrictions
            + build.animated_restrictions
            + build.component_restrictions
            + build.miscellaneous_restrictions
        )
        patterns = build.door_type or ["Regular"]
        rows = await _resolve_official_tag_rows(
            session,
            build_kind=build.category.value if build.category is not None else None,
            restrictions=restrictions,
            patterns=patterns,
        )
        return [
            DomainTagAssignment(
                definition=self._mapper.tag_definition_to_domain(row),
                provenance="submitted",
            )
            for row in rows
        ]

    @staticmethod
    async def _get_or_create_account(session: AsyncSession, discord_id: int) -> int:
        """Return the ``users.id`` for *discord_id*, creating a bare row if needed.

        A submitter-only row holds nothing beyond the Discord snowflake the bot
        already needs for ownership checks, so it carries no consent receipt;
        the receipt covers the Minecraft link, which such a row does not have.
        """
        user_id = await session.scalar(select(User.id).where(User.discord_id == discord_id))
        if user_id is not None:
            return user_id
        result = await session.execute(
            pg_insert(User)
            .values(discord_id=discord_id)
            .on_conflict_do_nothing(index_elements=[User.discord_id])
            .returning(User.id)
        )
        user_id = result.scalar_one_or_none()
        if user_id is not None:
            return user_id
        # A concurrent submission inserted the same submitter first.
        return (await session.execute(select(User.id).where(User.discord_id == discord_id))).scalar_one()

    @staticmethod
    async def _get_or_create_aliases(session: AsyncSession, igns: list[str]) -> list[int]:
        """Return the creator alias IDs for *igns*, creating missing names.

        Names are matched case-insensitively via the ``normalized_name``
        generated column, so ``Foo`` and ``foo`` share one credit. The insert
        relies on that column's unique constraint rather than a read-then-write,
        so two submissions naming the same creator cannot race.
        """
        alias_ids: list[int] = []
        seen: set[str] = set()
        for ign in igns:
            name = ign.strip()
            normalized = normalize_ign(name)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)

            alias_id = await session.scalar(select(CreatorAlias.id).where(CreatorAlias.normalized_name == normalized))
            if alias_id is None:
                result = await session.execute(
                    pg_insert(CreatorAlias)
                    .values(name=name)
                    .on_conflict_do_nothing(index_elements=[CreatorAlias.normalized_name])
                    .returning(CreatorAlias.id)
                )
                alias_id = result.scalar_one_or_none()
            if alias_id is None:
                alias_id = (
                    await session.execute(select(CreatorAlias.id).where(CreatorAlias.normalized_name == normalized))
                ).scalar_one()
            alias_ids.append(alias_id)

        return alias_ids

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
        unknown_animated = [r for r in build.animated_restrictions if r.title() not in found_names]
        unknown_component = [r for r in build.component_restrictions if r.title() not in found_names]
        unknown_misc = [r for r in build.miscellaneous_restrictions if r.title() not in found_names]

        if unknown_wiring:
            unknown_restrictions["wiring_placement_restrictions"] = unknown_wiring
        if unknown_animated:
            unknown_restrictions["animated_restrictions"] = unknown_animated
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
                    selectinload(Door.tag_assignments).selectinload(SQLTagAssignment.definition),
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
                    selectinload(SQLBuild.tag_assignments).selectinload(SQLTagAssignment.definition),
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


def _split_tag_value(
    assignment: DomainTagAssignment,
) -> tuple[Decimal | None, str | None, bool | None]:
    value_type = assignment.definition.value_type
    value = assignment.value
    if value_type is TagValueType.NONE and value is None:
        return None, None, None
    if value_type is TagValueType.NUMERIC and isinstance(value, Decimal):
        return value, None, None
    if value_type is TagValueType.TEXT and isinstance(value, str):
        return None, value, None
    if value_type is TagValueType.BOOLEAN and isinstance(value, bool):
        return None, None, value
    msg = f"Tag {assignment.definition.stable_key} expects a {value_type.value} value."
    raise InvalidBuildError(msg, context={"tag_id": assignment.definition.id})


async def _resolve_official_tag_rows(
    session: AsyncSession,
    *,
    build_kind: str | None,
    restrictions: list[str],
    patterns: list[str],
) -> list[SQLTagDefinition]:
    restriction_names = {_normalize_tag_name(name) for name in restrictions}
    pattern_names = {_normalize_tag_name(name) for name in patterns}
    if not restriction_names and not pattern_names:
        return []

    matched_name = or_(
        SQLTagDefinition.normalized_name.in_(restriction_names | pattern_names),
        TagAlias.normalized_alias.in_(restriction_names | pattern_names),
    )
    matched_kind = or_(
        (SQLTagDefinition.semantic_kind == TagSemanticKind.RESTRICTION)
        & matched_name
        & or_(
            SQLTagDefinition.normalized_name.in_(restriction_names),
            TagAlias.normalized_alias.in_(restriction_names),
        ),
        (SQLTagDefinition.semantic_kind == TagSemanticKind.PATTERN)
        & matched_name
        & or_(
            SQLTagDefinition.normalized_name.in_(pattern_names),
            TagAlias.normalized_alias.in_(pattern_names),
        ),
    )
    statement = (
        select(SQLTagDefinition)
        .outerjoin(TagAlias, TagAlias.tag_id == SQLTagDefinition.id)
        .where(
            SQLTagDefinition.authority == "official",
            SQLTagDefinition.moderation_status == "approved",
            matched_kind,
        )
        .order_by(SQLTagDefinition.default_display_order, SQLTagDefinition.id)
    )
    if build_kind is not None:
        statement = statement.join(
            TagApplicability,
            TagApplicability.tag_id == SQLTagDefinition.id,
        ).where(TagApplicability.build_kind == build_kind)
    return list((await session.scalars(statement)).unique().all())


def _normalize_tag_name(value: str) -> str:
    return " ".join(value.casefold().split())
