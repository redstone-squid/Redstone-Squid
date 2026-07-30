"""Map build persistence rows to domain entities."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from squid.builds.domain import Build, BuildCategory
from squid.builds.infrastructure.models import (
    Build as SQLBuild,
)
from squid.builds.infrastructure.models import BuildCreator, BuildVersion, Door, Extender
from squid.messages.infrastructure.models import Message
from squid.tags.domain import (
    RecordOperator,
    TagAssignment,
    TagAuthority,
    TagDefinition,
    TagModerationStatus,
    TagSemanticKind,
    TagValueType,
)
from squid.tags.infrastructure.models import BuildTagAssignment
from squid.tags.infrastructure.models import TagDefinition as SQLTagDefinition
from squid.users.infrastructure.models import User
from squid.versions.infrastructure.models import Version


class BuildMapper:
    """Load cross-context values explicitly while mapping a build."""

    async def to_domain(self, session: AsyncSession, sql_build: SQLBuild) -> Build:
        if not isinstance(sql_build, (Door, Extender)):
            msg = "Can only handle doors and piston extenders right now."
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
        tags = [_tag_assignment_to_domain(assignment) for assignment in sql_build.tag_assignments]
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
            door_width=sql_build.door_width if isinstance(sql_build, Door) else None,
            door_height=sql_build.door_height if isinstance(sql_build, Door) else None,
            door_depth=sql_build.door_depth if isinstance(sql_build, Door) else None,
            door_type=[association.type.name for association in sql_build.build_types],
            door_orientation_type=sql_build.orientation if isinstance(sql_build, Door) else None,
            wiring_placement_restrictions=[
                restriction.name for restriction in restrictions if restriction.type == "wiring-placement"
            ],
            animated_restrictions=[restriction.name for restriction in restrictions if restriction.type == "animated"],
            component_restrictions=[
                restriction.name for restriction in restrictions if restriction.type == "component"
            ],
            miscellaneous_restrictions=[
                restriction.name for restriction in restrictions if restriction.type == "miscellaneous"
            ],
            tags=tags,
            extender_orientation=sql_build.orientation if isinstance(sql_build, Extender) else None,
            extension_length=sql_build.extension_length if isinstance(sql_build, Extender) else None,
            extender_type=sql_build.extender_type if isinstance(sql_build, Extender) else None,
            normal_closing_time=sql_build.normal_closing_time if isinstance(sql_build, Door) else None,
            normal_opening_time=sql_build.normal_opening_time if isinstance(sql_build, Door) else None,
            visible_closing_time=sql_build.visible_closing_time if isinstance(sql_build, Door) else None,
            visible_opening_time=sql_build.visible_opening_time if isinstance(sql_build, Door) else None,
            extra_info=sql_build.extra_info,
            creators_ign=creator_names,
            image_urls=[link.url for link in sql_build.links if link.media_type == "image"],
            video_urls=[link.url for link in sql_build.links if link.media_type == "video"],
            world_download_urls=[link.url for link in sql_build.links if link.media_type == "world-download"],
            submitter_id=sql_build.submitter_id,
            completion_time=sql_build.completion_time,
            completion_at=sql_build.completion_at,
            completion_evidence=sql_build.completion_evidence,
            description=sql_build.description,
            edited_time=sql_build.edited_time,
            original_server_id=original_message.server_id if original_message else None,
            original_channel_id=original_message.channel_id if original_message else None,
            original_message_id=sql_build.original_message_id,
            original_message_author_id=original_message.author_id if original_message else None,
            original_message=original_message.content if original_message else None,
            ai_generated=sql_build.ai_generated,
            embedding=sql_build.embedding,
        )

    @staticmethod
    def tag_definition_to_domain(definition: SQLTagDefinition) -> TagDefinition:
        """Map persisted tag metadata without exposing infrastructure models."""
        return TagDefinition(
            id=definition.id,
            stable_key=definition.stable_key,
            display_name=definition.display_name,
            query_name=definition.query_name,
            authority=TagAuthority(definition.authority),
            semantic_kind=TagSemanticKind(definition.semantic_kind),
            restriction_type=definition.restriction_type,
            value_type=TagValueType(definition.value_type),
            record_operator=(
                RecordOperator(definition.record_operator) if definition.record_operator is not None else None
            ),
            canonical_unit=definition.canonical_unit_key,
            default_display_unit=definition.default_display_unit_key,
            numeric_quantum=definition.numeric_quantum,
            render_template=definition.render_template,
            default_display_order=definition.default_display_order,
            moderation_status=TagModerationStatus(definition.moderation_status),
        )


def _tag_assignment_to_domain(assignment: BuildTagAssignment) -> TagAssignment:
    definition = assignment.definition
    value = {
        TagValueType.NONE: None,
        TagValueType.NUMERIC: assignment.numeric_value,
        TagValueType.TEXT: assignment.text_value,
        TagValueType.BOOLEAN: assignment.boolean_value,
    }[assignment.value_type]
    return TagAssignment(
        definition=BuildMapper.tag_definition_to_domain(definition),
        value=value,
        display_unit=assignment.display_unit_key,
        display_order=assignment.display_order,
        evidence=assignment.evidence,
        provenance=assignment.provenance,
    )
