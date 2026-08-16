"""Map build persistence rows to domain entities.

The domain category subclasses name their fields identically to the joined ORM
subclasses, so both mapping directions share :data:`CATEGORY_FIELD_NAMES` — the
single authoritative list of category-specific fields. Adding a column means
adding it to the ORM model, the domain subclass, and that one tuple.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from squid.accounts.domain import IdentityProvider
from squid.accounts.infrastructure.models import AccountIdentity, CreatorAlias
from squid.builds.domain import BUILD_CLASS_BY_CATEGORY, Build, BuildCategory, BuildLink, SourceMessage
from squid.builds.infrastructure.models import (
    Build as SQLBuild,
)
from squid.builds.infrastructure.models import (
    BuildCreator,
    BuildSourceMessage,
    BuildVersion,
    Door,
    Entrance,
    Extender,
    Other,
    Utility,
)
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


@dataclass(frozen=True, slots=True)
class _CrossContextValues:
    """Cross-context rows for a batch of builds, loaded with four queries total."""

    creators: Mapping[int, list[str]]
    submitter_discord_ids: Mapping[int, int]
    versions: Mapping[int, list[str]]
    source_messages: Mapping[int, list[SourceMessage]]


class BuildMapper:
    """Load cross-context values explicitly while mapping a build.

    Cross-context relationships were deliberately removed from the ORM models,
    so the values other contexts own are fetched here instead of traversed.
    Batch them per page: `to_domain` is the single-row delegate of
    `to_domain_many`, which issues a fixed four queries regardless of page size.
    """

    async def to_domain(self, session: AsyncSession, sql_build: SQLBuild) -> Build:
        mapped = await self.to_domain_many(session, [sql_build])
        return mapped[0]

    async def to_domain_many(self, session: AsyncSession, sql_builds: Sequence[SQLBuild]) -> list[Build]:
        """Map several rows, loading their cross-context values in one batch."""
        if not sql_builds:
            return []
        values = await self._load_cross_context(session, sql_builds)
        return [self._to_domain(sql_build, values) for sql_build in sql_builds]

    @staticmethod
    async def _load_cross_context(session: AsyncSession, sql_builds: Sequence[SQLBuild]) -> _CrossContextValues:
        build_ids = [sql_build.id for sql_build in sql_builds]
        account_ids = {
            sql_build.submitter_account_id for sql_build in sql_builds if sql_build.submitter_account_id is not None
        }

        creators: dict[int, list[str]] = {build_id: [] for build_id in build_ids}
        for build_id, name in await session.execute(
            select(BuildCreator.build_id, CreatorAlias.name)
            .join(CreatorAlias, BuildCreator.alias_id == CreatorAlias.id)
            .where(BuildCreator.build_id.in_(build_ids))
        ):
            creators[build_id].append(name)

        submitter_discord_ids: dict[int, int] = {}
        if account_ids:
            for account_id, subject in await session.execute(
                select(AccountIdentity.account_id, AccountIdentity.subject).where(
                    AccountIdentity.account_id.in_(account_ids),
                    AccountIdentity.provider == IdentityProvider.DISCORD,
                )
            ):
                submitter_discord_ids[account_id] = int(subject)

        versions: dict[int, list[str]] = {build_id: [] for build_id in build_ids}
        for build_id, version in await session.execute(
            select(BuildVersion.build_id, Version)
            .join(Version, BuildVersion.version_id == Version.id)
            .where(BuildVersion.build_id.in_(build_ids))
        ):
            versions[build_id].append(
                f"{version.edition} {version.major_version}.{version.minor_version}.{version.patch_number}"
            )

        # Links and message facts in one statement: two tables, but one round trip, so
        # provenance costs the same whether or not the page has any.
        source_messages: dict[int, list[SourceMessage]] = {build_id: [] for build_id in build_ids}
        for build_id, message in await session.execute(
            select(BuildSourceMessage.build_id, Message)
            .join(Message, BuildSourceMessage.message_id == Message.id)
            .where(BuildSourceMessage.build_id.in_(build_ids))
            .order_by(BuildSourceMessage.build_id, BuildSourceMessage.position)
        ):
            source_messages[build_id].append(_source_message(message))

        return _CrossContextValues(creators, submitter_discord_ids, versions, source_messages)

    @staticmethod
    def _to_domain(sql_build: SQLBuild, values: _CrossContextValues) -> Build:
        category = BuildCategory(sql_build.category)
        if not isinstance(sql_build, SQL_CLASS_BY_CATEGORY[category]):
            msg = f"Unsupported persisted build category: {sql_build.category}."
            raise TypeError(msg)

        submitter_discord_id = (
            None
            if sql_build.submitter_account_id is None
            else values.submitter_discord_ids.get(sql_build.submitter_account_id)
        )
        source_messages = tuple(values.source_messages.get(sql_build.id, ()))

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
            versions=list(values.versions.get(sql_build.id, ())),
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
            creators_ign=list(values.creators.get(sql_build.id, ())),
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
            source_messages=source_messages,
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
            numeric_step=definition.numeric_step,
            render_template=definition.render_template,
            default_display_order=definition.default_display_order,
            moderation_status=TagModerationStatus(definition.moderation_status),
        )


def _source_message(message: Message) -> SourceMessage:
    """Map one message fact reached through a build's source-message link."""
    return SourceMessage(
        message_id=message.id,
        guild_id=message.guild_id,
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
