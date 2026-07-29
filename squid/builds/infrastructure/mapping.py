"""Map build persistence rows to domain entities."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from whenever import Instant

from squid.builds.domain import Build, BuildCategory
from squid.builds.infrastructure.models import (
    Build as SQLBuild,
)
from squid.builds.infrastructure.models import BuildCreator, BuildVersion, Door
from squid.messages.infrastructure.models import Message
from squid.users.infrastructure.models import User
from squid.versions.infrastructure.models import Version


class BuildMapper:
    """Load cross-context values explicitly while mapping a build."""

    async def to_domain(self, session: AsyncSession, sql_build: SQLBuild) -> Build:
        if not isinstance(sql_build, Door):
            msg = "Can only handle doors right now."
            raise TypeError(msg)

        creator_names = list(
            (
                await session.scalars(
                    select(User.ign)
                    .join(BuildCreator, BuildCreator.user_id == User.id)
                    .where(BuildCreator.build_id == sql_build.id)
                )
            ).all()
        )
        version_rows = (
            await session.scalars(
                select(Version)
                .join(BuildVersion, BuildVersion.version_id == Version.id)
                .where(BuildVersion.build_id == sql_build.id)
            )
        ).all()
        original_message = (
            None
            if sql_build.original_message_id is None
            else await session.scalar(select(Message).where(Message.id == sql_build.original_message_id))
        )

        restrictions = [association.restriction for association in sql_build.build_restrictions]
        return Build(
            id=sql_build.id,
            submission_status=sql_build.submission_status,
            category=BuildCategory(sql_build.category),
            record_category=sql_build.record_category,
            versions=[
                f"{version.edition} {version.major_version}.{version.minor_version}.{version.patch_number}"
                for version in version_rows
            ],
            version_spec=sql_build.version_spec,
            width=sql_build.width,
            height=sql_build.height,
            depth=sql_build.depth,
            door_width=sql_build.door_width,
            door_height=sql_build.door_height,
            door_depth=sql_build.door_depth,
            door_type=[association.type.name for association in sql_build.build_types],
            door_orientation_type=sql_build.orientation,
            wiring_placement_restrictions=[
                restriction.name for restriction in restrictions if restriction.type == "wiring-placement"
            ],
            component_restrictions=[
                restriction.name for restriction in restrictions if restriction.type == "component"
            ],
            miscellaneous_restrictions=[
                restriction.name for restriction in restrictions if restriction.type == "miscellaneous"
            ],
            normal_closing_time=sql_build.normal_closing_time,
            normal_opening_time=sql_build.normal_opening_time,
            visible_closing_time=sql_build.visible_closing_time,
            visible_opening_time=sql_build.visible_opening_time,
            extra_info=sql_build.extra_info,
            creators_ign=creator_names,
            image_urls=[link.url for link in sql_build.links if link.media_type == "image"],
            video_urls=[link.url for link in sql_build.links if link.media_type == "video"],
            world_download_urls=[link.url for link in sql_build.links if link.media_type == "world-download"],
            submitter_id=sql_build.submitter_id,
            completion_time=sql_build.completion_time,
            edited_time=Instant(sql_build.edited_time) if sql_build.edited_time is not None else None,
            original_server_id=original_message.server_id if original_message else None,
            original_channel_id=original_message.channel_id if original_message else None,
            original_message_id=sql_build.original_message_id,
            original_message_author_id=original_message.author_id if original_message else None,
            original_message=original_message.content if original_message else None,
            ai_generated=sql_build.ai_generated,
            embedding=sql_build.embedding,
        )
