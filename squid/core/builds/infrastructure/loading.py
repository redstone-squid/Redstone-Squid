"""Batch loading of creator, version, and source-message details for builds."""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import and_, case, false, func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from squid.core.accounts.domain import IdentityProvider
from squid.core.accounts.infrastructure.attribution import attributed_credits
from squid.core.accounts.models import Account, AccountIdentity
from squid.core.builds.domain import BuildCategory, BuildStatus, Credit
from squid.core.builds.domain.credits import CreditRole
from squid.core.builds.models import (
    BUILD_CLASS_BY_CATEGORY,
    Build,
    BuildCredit,
    BuildSourceMessage,
    BuildVersion,
)
from squid.core.errors import DataIntegrityError
from squid.core.messages.domain import MessageRef
from squid.core.messages.models import Message
from squid.core.records.domain.build_records import BuildRecord, BuildRecords
from squid.core.records.models import (
    Record,
    RecordDefinition,
    RecordHolder,
    RecordHolderHistory,
    RecordResult,
)
from squid.core.sponsors import PublicSponsor
from squid.core.tags.domain import TagSemanticKind
from squid.core.versions.models import Version


@dataclass(frozen=True, slots=True)
class _CrossContextValues:
    """Creator, account, version, and source-message details indexed for a batch of builds."""

    credits: Mapping[int, list[Credit]]
    credited_creator_accounts: Mapping[int, set[int]]
    submitter_discord_ids: Mapping[int, int]
    versions: Mapping[int, list[str]]
    source_messages: Mapping[int, list[MessageRef]]
    source_message_contents: Mapping[int, str | None]


class BuildLoader:
    """Enrich loaded builds with facts owned by other contexts."""

    @staticmethod
    def credit_rows(build_ids: Sequence[int]):
        """Resolve each credit to its current account and public creator identifier."""
        linked = func.build_credit_accounts().table_valued("build_id", "position", "account_id")
        public = attributed_credits()
        public_id = (
            select(Account.public_account_id)
            .join(public, Account.id == public.c.account_id)
            .where(public.c.build_id == BuildCredit.build_id, public.c.position == BuildCredit.position)
            .order_by(Account.id)
            .limit(1)
            .correlate(BuildCredit)
            .scalar_subquery()
        )
        return (
            select(BuildCredit, linked.c.account_id, public_id)
            .outerjoin(
                linked,
                and_(linked.c.build_id == BuildCredit.build_id, linked.c.position == BuildCredit.position),
            )
            .where(BuildCredit.build_id.in_(build_ids))
            .order_by(BuildCredit.build_id, BuildCredit.position)
        )

    @staticmethod
    async def records(session: AsyncSession, build_ids: Sequence[int]) -> dict[int, BuildRecords]:
        """Collect the records each build holds now and the ones its holder history names.

        A retired record stays among a former holder's records, marked retired.
        """
        if not build_ids:
            return {}
        current = (
            select(
                RecordHolder.build_id,
                RecordResult.record_id,
                RecordHolder.completion_at.label("since"),
                true().label("held"),
            )
            .join(RecordResult, RecordResult.id == RecordHolder.result_id)
            .where(RecordHolder.build_id.in_(build_ids))
        )
        intervals = select(
            RecordHolderHistory.build_id,
            RecordHolderHistory.record_id,
            RecordHolderHistory.held_from,
            false(),
        ).where(RecordHolderHistory.build_id.in_(build_ids))
        holdings = current.union_all(intervals).subquery()
        build_id, record_id, since, held = holdings.c
        # A build can hold one record under several definitions; name it by the newest.
        title = (
            select(RecordDefinition.title)
            .where(RecordDefinition.record_id == Record.public_id)
            .order_by(RecordDefinition.id.desc())
            .limit(1)
            .correlate(Record)
            .scalar_subquery()
        )
        rows = await session.execute(
            select(
                build_id,
                Record.public_id,
                title,
                Record.criterion,
                Record.version_scope,
                Record.retired_at.is_not(None),
                func.max(since),
                func.bool_or(held),
            )
            .select_from(holdings)
            .join(Record, Record.public_id == record_id)
            .group_by(build_id, Record.public_id)
            .order_by(title, Record.public_id)
        )
        held_records: dict[int, list[BuildRecord]] = defaultdict(list)
        former_records: dict[int, list[BuildRecord]] = defaultdict(list)
        for identifier, public_id, record_title, criterion, scope, retired, start, is_held in rows:
            target = held_records if is_held else former_records
            target[identifier].append(
                BuildRecord(
                    public_id,
                    record_title,
                    criterion,
                    scope,
                    start.to_stdlib() if start else None,
                    retired=bool(retired),
                )
            )
        return {
            identifier: BuildRecords(tuple(held_records[identifier]), tuple(former_records[identifier]))
            for identifier in held_records.keys() | former_records.keys()
        }

    async def load(self, session: AsyncSession, sql_build: Build) -> Build:
        mapped = await self.load_many(session, [sql_build])
        return mapped[0]

    async def load_many(self, session: AsyncSession, sql_builds: Sequence[Build]) -> list[Build]:
        """Populate related facts in place and return builds in the supplied order.

        Cross-context queries are batched for the page. Tags and links must already be
        loaded; this loader does not fetch them.
        """
        if not sql_builds:
            return []
        values = await self._load_cross_context(session, sql_builds)
        records = await self.records(
            session, [build.id for build in sql_builds if build.submission_status is BuildStatus.APPROVED]
        )
        for build in sql_builds:
            build.published_records = records.get(build.id, BuildRecords())
        return [self._populate(sql_build, values) for sql_build in sql_builds]

    @staticmethod
    async def _load_cross_context(session: AsyncSession, sql_builds: Sequence[Build]) -> _CrossContextValues:
        build_ids = [sql_build.id for sql_build in sql_builds]
        account_ids = {
            sql_build.submitter_account_id
            for sql_build in sql_builds
            # The database requires an account, but an unsaved build may not have one yet.
            if sql_build.submitter_account_id is not None  # pyright: ignore[reportUnnecessaryComparison]
        }

        credits: dict[int, list[Credit]] = {build_id: [] for build_id in build_ids}
        creator_accounts: dict[int, set[int]] = {build_id: set() for build_id in build_ids}
        for row, linked_account, public_account_id in await session.execute(BuildLoader.credit_rows(build_ids)):
            credits[row.build_id].append(row.to_credit(public_account_id))
            if row.role is CreditRole.CREATOR and linked_account is not None:
                creator_accounts[row.build_id].add(linked_account)

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
            versions[build_id].append(version.name)

        # Join source links to messages so the whole page needs one origin query.
        source_messages: dict[int, list[MessageRef]] = {build_id: [] for build_id in build_ids}
        source_message_contents: dict[int, str | None] = {}
        for build_id, message_id, channel_id, guild_id, content in await session.execute(
            select(
                BuildSourceMessage.build_id,
                Message.id,
                Message.channel_id,
                Message.guild_id,
                case((Message.deleted_at.is_(None), Message.content)),
            )
            .join(Message, BuildSourceMessage.message_id == Message.id)
            .where(BuildSourceMessage.build_id.in_(build_ids))
            .order_by(BuildSourceMessage.build_id, BuildSourceMessage.position)
        ):
            source_messages[build_id].append(MessageRef(message_id, channel_id=channel_id, guild_id=guild_id))

            if channel_id is not None and guild_id is not None:
                source_message_contents.setdefault(build_id, content)

        return _CrossContextValues(
            credits, creator_accounts, submitter_discord_ids, versions, source_messages, source_message_contents
        )

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
        restrictions = [
            assignment.definition
            for assignment in tags
            if assignment.definition.semantic_kind is TagSemanticKind.RESTRICTION
        ]
        patterns = [
            assignment.definition.display_name
            for assignment in tags
            if assignment.definition.semantic_kind is TagSemanticKind.PATTERN
        ]
        sql_build.versions = list(values.versions.get(sql_build.id, ()))
        sql_build.patterns = patterns
        sql_build.wiring_placement_restrictions = [
            r.display_name for r in restrictions if r.restriction_type == "wiring-placement"
        ]
        sql_build.animated_restrictions = [r.display_name for r in restrictions if r.restriction_type == "animated"]
        sql_build.component_restrictions = [r.display_name for r in restrictions if r.restriction_type == "component"]
        sql_build.miscellaneous_restrictions = [
            r.display_name for r in restrictions if r.restriction_type == "miscellaneous"
        ]
        sql_build.credits = list(values.credits.get(sql_build.id, ()))
        sql_build.credited_creator_account_ids = frozenset(values.credited_creator_accounts.get(sql_build.id, ()))
        sql_build.submitter_discord_id = submitter_discord_id
        # These facts are immutable to callers; loading supplies their initial values.
        object.__setattr__(sql_build, "__frozen_source_messages", source_messages)
        sql_build.source_message_content = values.source_message_contents.get(sql_build.id)
        object.__setattr__(sql_build, "__frozen_sponsor", _sponsor(sql_build))
        return sql_build


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
