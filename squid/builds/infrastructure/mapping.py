"""Map build persistence rows to domain entities.

The domain category subclasses name their fields identically to the joined ORM
subclasses, so both mapping directions share :data:`CATEGORY_FIELD_NAMES` — the
single authoritative list of category-specific fields. Adding a column means
adding it to the ORM model, the domain subclass, and that one tuple.
"""

from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from squid.accounts.domain import IdentityProvider
from squid.accounts.infrastructure.models import AccountIdentity, CreatorAlias
from squid.builds.domain import BUILD_CLASS_BY_CATEGORY, Build, BuildCategory, BuildLink, OriginalMessage
from squid.builds.infrastructure.models import (
    Build as SQLBuild,
)
from squid.builds.infrastructure.models import BuildCreator, BuildVersion, Door, Entrance, Extender, Other, Utility
from squid.core.errors import DataIntegrityError
from squid.messages.infrastructure.models import Message
from squid.sponsors import PublicSponsor
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
from squid.versions.infrastructure.models import Version

CATEGORY_FIELD_NAMES: Mapping[BuildCategory, tuple[str, ...]] = {
    BuildCategory.DOOR: (
        "orientation",
        "door_width",
        "door_height",
        "door_depth",
        "normal_opening_time",
        "normal_closing_time",
        "visible_opening_time",
        "visible_closing_time",
    ),
    BuildCategory.EXTENDER: (
        "orientation",
        "extension_length",
        "extender_type",
    ),
    BuildCategory.UTILITY: (),
    BuildCategory.ENTRANCE: (),
    BuildCategory.OTHER: (),
}
"""Category-specific fields, named identically on the ORM and domain subclasses."""

SQL_CLASS_BY_CATEGORY: Mapping[BuildCategory, type[SQLBuild]] = {
    BuildCategory.DOOR: Door,
    BuildCategory.EXTENDER: Extender,
    BuildCategory.UTILITY: Utility,
    BuildCategory.ENTRANCE: Entrance,
    BuildCategory.OTHER: Other,
}


def category_values_from_row(sql_build: SQLBuild) -> dict[str, Any]:
    """Read the category-specific fields off a joined row."""
    return {name: getattr(sql_build, name) for name in CATEGORY_FIELD_NAMES[BuildCategory(sql_build.category)]}


def category_values_from_domain(build: Build) -> dict[str, Any]:
    """Read the category-specific fields off a domain entity."""
    return {name: getattr(build, name) for name in CATEGORY_FIELD_NAMES[build.category]}


class BuildMapper:
    """Load cross-context values explicitly while mapping a build."""

    async def to_domain(self, session: AsyncSession, sql_build: SQLBuild) -> Build:
        category = BuildCategory(sql_build.category)
        if not isinstance(sql_build, SQL_CLASS_BY_CATEGORY[category]):
            msg = f"Unsupported persisted build category: {sql_build.category}."
            raise TypeError(msg)

        creator_names = list(
            (
                await session.scalars(
                    select(CreatorAlias.name)
                    .join(BuildCreator, BuildCreator.alias_id == CreatorAlias.id)
                    .where(BuildCreator.build_id == sql_build.id)
                )
            ).all()
        )
        submitter_subject = await session.scalar(
            select(AccountIdentity.subject).where(
                AccountIdentity.account_id == sql_build.submitter_account_id,
                AccountIdentity.provider == IdentityProvider.DISCORD,
            )
        )
        submitter_discord_id = None if submitter_subject is None else int(submitter_subject)
        version_rows = (
            await session.scalars(
                select(Version)
                .join(BuildVersion, BuildVersion.version_id == Version.id)
                .where(BuildVersion.build_id == sql_build.id)
            )
        ).all()
        message_row = (
            None
            if sql_build.original_message_id is None
            else await session.scalar(select(Message).where(Message.id == sql_build.original_message_id))
        )
        original_message = _original_message(sql_build.original_message_id, message_row)

        tags = [_tag_assignment_to_domain(assignment) for assignment in sql_build.tag_assignments]
        official_restrictions = [
            assignment.definition
            for assignment in tags
            if assignment.definition.authority is TagAuthority.OFFICIAL
            and assignment.definition.semantic_kind is TagSemanticKind.RESTRICTION
        ]
        official_patterns = [
            assignment.definition.display_name
            for assignment in tags
            if assignment.definition.authority is TagAuthority.OFFICIAL
            and assignment.definition.semantic_kind is TagSemanticKind.PATTERN
        ]
        return BUILD_CLASS_BY_CATEGORY[category](
            id=sql_build.id,
            revision=sql_build.revision,
            submission_status=sql_build.submission_status,
            record_category=sql_build.record_category,
            versions=[
                f"{version.edition} {version.major_version}.{version.minor_version}.{version.patch_number}"
                for version in version_rows
            ],
            version_spec=sql_build.version_spec,
            width=sql_build.width,
            height=sql_build.height,
            depth=sql_build.depth,
            patterns=official_patterns,
            wiring_placement_restrictions=[
                restriction.display_name
                for restriction in official_restrictions
                if restriction.restriction_type == "wiring-placement"
            ],
            animated_restrictions=[
                restriction.display_name
                for restriction in official_restrictions
                if restriction.restriction_type == "animated"
            ],
            component_restrictions=[
                restriction.display_name
                for restriction in official_restrictions
                if restriction.restriction_type == "component"
            ],
            miscellaneous_restrictions=[
                restriction.display_name
                for restriction in official_restrictions
                if restriction.restriction_type == "miscellaneous"
            ],
            tags=tags,
            extra_info=sql_build.extra_info,
            creators_ign=creator_names,
            # A NULL media_type (the column is legacy-nullable) was invisible to the old
            # per-type filters as well, so such rows stay unmapped rather than guessed at.
            links=[
                BuildLink(url=link.url, media_type=link.media_type)
                for link in sql_build.links
                if link.media_type is not None
            ],
            display_name=sql_build.display_name,
            source_submission_draft_id=sql_build.source_submission_draft_id,
            sponsor=_sponsor(sql_build),
            submitter_account_id=sql_build.submitter_account_id,
            submitter_id=submitter_discord_id,
            completion_time=sql_build.completion_time,
            completion_at=sql_build.completion_at,
            completion_evidence=sql_build.completion_evidence,
            description=sql_build.description,
            submission_time=sql_build.submission_time,
            edited_time=sql_build.edited_time,
            original_message=original_message,
            ai_generated=sql_build.ai_generated,
            embedding=sql_build.embedding,
            **category_values_from_row(sql_build),
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


def _original_message(message_id: int | None, message: Message | None) -> OriginalMessage | None:
    if message_id is None:
        return None
    if message is None:
        return OriginalMessage(message_id=message_id)
    return OriginalMessage(
        message_id=message_id,
        server_id=message.server_id,
        channel_id=message.channel_id,
        author_id=message.author_id,
        content=message.content,
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


def _sponsor(build: SQLBuild) -> PublicSponsor | None:
    values = (
        build.sponsor_display_name,
        build.sponsor_address,
        build.sponsor_description,
        build.sponsor_website_url,
    )
    if build.sponsor_installation_id is None:
        if any(value is not None for value in values):
            msg = "A persisted build has sponsor metadata without its installation ID."
            raise DataIntegrityError(msg)
        return None
    try:
        return PublicSponsor(
            installation_id=build.sponsor_installation_id,
            display_name=build.sponsor_display_name,
            address=build.sponsor_address,
            description=build.sponsor_description,
            website_url=build.sponsor_website_url,
        )
    except ValueError as error:
        msg = "A persisted build has invalid public sponsor metadata."
        raise DataIntegrityError(msg) from error
