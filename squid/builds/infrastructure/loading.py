"""Batch loading of creator, version, and source-message facts for builds."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from squid.accounts.domain import IdentityProvider
from squid.accounts.models import AccountIdentity, CreatorAlias
from squid.builds.domain import BuildCategory, SourceMessage
from squid.builds.models import (
    BUILD_CLASS_BY_CATEGORY,
    Build,
    BuildCreator,
    BuildSourceMessage,
    BuildVersion,
)
from squid.core.errors import DataIntegrityError
from squid.messages.models import MessageRecord
from squid.sponsors import PublicSponsor
from squid.tags.domain import TagAuthority, TagSemanticKind
from squid.versions.models import Version


@dataclass(frozen=True, slots=True)
class _CrossContextValues:
    """Cross-context rows for a batch of builds, loaded with four queries total."""

    creators: Mapping[int, list[str]]
    submitter_discord_ids: Mapping[int, int]
    versions: Mapping[int, list[str]]
    source_messages: Mapping[int, list[SourceMessage]]


class BuildLoader:
    """Enrich loaded builds with facts owned by other contexts."""

    async def load(self, session: AsyncSession, sql_build: Build) -> Build:
        mapped = await self.load_many(session, [sql_build])
        return mapped[0]

    async def load_many(self, session: AsyncSession, sql_builds: Sequence[Build]) -> list[Build]:
        """Load related facts for a page in four queries, regardless of page size."""
        if not sql_builds:
            return []
        values = await self._load_cross_context(session, sql_builds)
        return [self._populate(sql_build, values) for sql_build in sql_builds]

    @staticmethod
    async def _load_cross_context(session: AsyncSession, sql_builds: Sequence[Build]) -> _CrossContextValues:
        build_ids = [sql_build.id for sql_build in sql_builds]
        account_ids = {
            sql_build.submitter_account_id
            for sql_build in sql_builds
            # `builds.submitter_account_id` is NOT NULL, but `Mapped[int]` also covers instances that
            # have not been flushed yet, where SQLAlchemy reads an unset attribute back as None.
            if sql_build.submitter_account_id is not None  # pyright: ignore[reportUnnecessaryComparison]
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
            select(BuildSourceMessage.build_id, MessageRecord)
            .join(MessageRecord, BuildSourceMessage.message_id == MessageRecord.id)
            .where(BuildSourceMessage.build_id.in_(build_ids))
            .order_by(BuildSourceMessage.build_id, BuildSourceMessage.position)
        ):
            source_messages[build_id].append(_source_message(message))

        return _CrossContextValues(creators, submitter_discord_ids, versions, source_messages)

    @staticmethod
    def _populate(sql_build: Build, values: _CrossContextValues) -> Build:
        category = BuildCategory(sql_build.category)
        if not isinstance(sql_build, BUILD_CLASS_BY_CATEGORY[category]):
            msg = f"Unsupported persisted build category: {sql_build.category}."
            raise TypeError(msg)

        submitter_discord_id = (
            None
            # See `_load_cross_context`: NOT NULL in the database, but None on an unflushed instance.
            if sql_build.submitter_account_id is None  # pyright: ignore[reportUnnecessaryComparison]
            else values.submitter_discord_ids.get(sql_build.submitter_account_id)
        )
        source_messages = tuple(values.source_messages.get(sql_build.id, ()))

        tags = list(sql_build.tags)
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
        sql_build.versions = list(values.versions.get(sql_build.id, ()))
        sql_build.patterns = official_patterns
        sql_build.wiring_placement_restrictions = [
            r.display_name for r in official_restrictions if r.restriction_type == "wiring-placement"
        ]
        sql_build.animated_restrictions = [
            r.display_name for r in official_restrictions if r.restriction_type == "animated"
        ]
        sql_build.component_restrictions = [
            r.display_name for r in official_restrictions if r.restriction_type == "component"
        ]
        sql_build.miscellaneous_restrictions = [
            r.display_name for r in official_restrictions if r.restriction_type == "miscellaneous"
        ]
        sql_build.creators_ign = list(values.creators.get(sql_build.id, ()))
        sql_build.submitter_discord_id = submitter_discord_id
        # These facts are immutable to callers; loading supplies their initial values.
        object.__setattr__(sql_build, "__frozen_source_messages", source_messages)
        object.__setattr__(sql_build, "__frozen_sponsor", _sponsor(sql_build))
        return sql_build


def _source_message(message: MessageRecord) -> SourceMessage:
    """Map one message fact reached through a build's source-message link."""
    return SourceMessage(
        message_id=message.id,
        guild_id=message.guild_id,
        channel_id=message.channel_id,
        author_id=message.author_id,
        content=message.content,
    )


def _sponsor(build: Build) -> PublicSponsor | None:
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
