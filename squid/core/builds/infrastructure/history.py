"""Capture persisted build aggregates in the same transaction as their changes."""

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import replace
from typing import cast

from pydantic import JsonValue
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from squid.core.accounts.models import Account, AccountProfile
from squid.core.builds.domain import BuildStatus
from squid.core.builds.history import (
    BuildEditContext,
    BuildHistoryDetail,
    BuildHistorySummary,
    BuildLastEdit,
    BuildPublication,
    BuildStateSnapshot,
    HistoryActor,
    HistoryChange,
    HistoryOperation,
    PublicBuildRevision,
)
from squid.core.builds.history_models import BuildHistoryEntry, BuildHistorySnapshot
from squid.core.builds.models import Build
from squid.core.pagination import FIRST_PAGE, MAX_PAGE_OFFSET, Page, PageSelector, offset_page
from squid.core.persistence.paging import KeysetOrder
from squid.core.voting.domain import VoteStatus
from squid.core.voting.models import BuildVoteSession, VoteSession


class BuildHistoryQueries:
    """Trusted transaction-bound queries; snapshots are loaded only for entry detail."""

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session

    async def publication(self, build: Build) -> BuildPublication:
        session = self._session()
        subject = (
            await session.execute(
                select(Account.public_account_id, AccountProfile.display_name)
                .outerjoin(AccountProfile, (AccountProfile.account_id == Account.id) & AccountProfile.hidden.is_(False))
                .where(Account.id == build.submitter_account_id)
            )
        ).one_or_none()
        old = aliased(BuildHistorySnapshot)
        published = (
            BuildHistorySnapshot.snapshot["build"]["submission_status"].as_integer() == BuildStatus.APPROVED.value
        )
        previously = old.snapshot["build"]["submission_status"].as_integer()
        # A migrated baseline observes an already approved build but cannot date its approval.
        approval = (
            func.max(BuildHistoryEntry.recorded_at)
            .filter(previously.is_distinct_from(BuildStatus.APPROVED.value), BuildHistoryEntry.operation != "baseline")
            .over()
        )
        row = (
            await session.execute(
                select(
                    BuildHistoryEntry.recorded_at,
                    BuildHistoryEntry.operation,
                    approval,
                    func.count().over(),
                    Account.public_account_id,
                    AccountProfile.display_name,
                )
                .join(BuildHistorySnapshot, BuildHistorySnapshot.id == BuildHistoryEntry.after_state_id)
                .outerjoin(old, old.id == BuildHistoryEntry.before_state_id)
                .outerjoin(Account, Account.id == BuildHistoryEntry.actor_account_id)
                .outerjoin(AccountProfile, (AccountProfile.account_id == Account.id) & AccountProfile.hidden.is_(False))
                .where(BuildHistoryEntry.build_id == build.id, published)
                .order_by(BuildHistoryEntry.id.desc())
                .limit(1)
            )
        ).one_or_none()
        vote = None
        if build.submission_status is BuildStatus.PENDING:
            vote = await session.scalar(
                select(BuildVoteSession.vote_session_id)
                .join(VoteSession, VoteSession.id == BuildVoteSession.vote_session_id)
                .where(BuildVoteSession.build_id == build.id, VoteSession.status == VoteStatus.OPEN)
                .order_by(BuildVoteSession.vote_session_id.desc())
                .limit(1)
            )
        submitter = HistoryActor(subject[1], subject[0]) if subject else None
        if row is None:
            return BuildPublication(submitted_by=submitter, vote_session_id=vote)
        at, operation, approved, count, creator, name = row
        edit = (
            None
            if operation in {"baseline", "create"}
            else BuildLastEdit(at.to_stdlib(), HistoryActor(name, creator) if creator else None)
        )
        return BuildPublication(approved.to_stdlib() if approved else None, submitter, edit, count, vote)

    async def submitters(self, builds: Sequence[Build]) -> dict[int, HistoryActor]:
        rows = await self._session().execute(
            select(Build.id, Account.public_account_id, AccountProfile.display_name)
            .join(Account, Account.id == Build.submitter_account_id)
            .outerjoin(AccountProfile, (AccountProfile.account_id == Account.id) & AccountProfile.hidden.is_(False))
            .where(Build.id.in_([build.id for build in builds]))
        )
        return {build_id: HistoryActor(name, public_id) for build_id, public_id, name in rows}

    async def public_page(
        self, build_id: int, labels: Mapping[str, str], *, offset: int, page_size: int
    ) -> Page[PublicBuildRevision]:
        before = aliased(BuildHistorySnapshot)
        after = aliased(BuildHistorySnapshot)
        statement = (
            select(BuildHistoryEntry, before, after, Account.public_account_id, AccountProfile.display_name)
            .select_from(BuildHistoryEntry)
            .join(after, after.id == BuildHistoryEntry.after_state_id)
            .outerjoin(before, before.id == BuildHistoryEntry.before_state_id)
            .outerjoin(Account, Account.id == BuildHistoryEntry.actor_account_id)
            .outerjoin(AccountProfile, (AccountProfile.account_id == Account.id) & AccountProfile.hidden.is_(False))
            .where(
                BuildHistoryEntry.build_id == build_id,
                after.snapshot["build"]["submission_status"].as_integer() == BuildStatus.APPROVED.value,
            )
        )
        session = self._session()
        total = await session.scalar(select(func.count()).select_from(statement.subquery()))
        rows = await session.execute(statement.order_by(BuildHistoryEntry.id.desc()).limit(page_size).offset(offset))
        items: list[PublicBuildRevision] = []
        for entry, old, new, public_account_id, name in rows:
            previous = self.public_fields(old)
            current = self.public_fields(new)
            changes = tuple(
                HistoryChange(key, labels.get(key, key), previous.get(key), current.get(key))
                for key in sorted(previous.keys() | current.keys())
                if previous.get(key) != current.get(key)
            )
            items.append(
                PublicBuildRevision(
                    new.build_revision,
                    entry.recorded_at.to_stdlib(),
                    HistoryActor(name, public_account_id) if public_account_id is not None else None,
                    cast(HistoryOperation, entry.operation),
                    changes,
                )
            )
        return offset_page(items, offset=offset, page_size=page_size, total=total or 0)

    @staticmethod
    def public_fields(state: BuildHistorySnapshot | None) -> dict[str, JsonValue]:
        """Project public edit fields, omitting the private side of an approval transition."""
        if state is None:
            return {}
        build = state.snapshot.get("build")
        if not isinstance(build, dict) or build.get("submission_status") != BuildStatus.APPROVED.value:
            return {}
        fields: dict[str, JsonValue] = {}
        for section, names in (
            (
                "build",
                {
                    "display_name": "display_name",
                    "description": "description",
                    "width": "capture_width",
                    "height": "capture_height",
                    "depth": "capture_depth",
                    "version_spec": "version_compatibility",
                    "category": "category",
                },
            ),
            (
                "doors",
                {
                    "orientation": "door_orientation",
                    "door_width": "opening_width",
                    "door_height": "opening_height",
                    "door_depth": "opening_depth",
                    "normal_opening_time": "opening_time",
                    "normal_closing_time": "closing_time",
                    "visible_opening_time": "visible_opening_time",
                    "visible_closing_time": "visible_closing_time",
                },
            ),
            (
                "extenders",
                {
                    "orientation": "movement_orientation",
                    "extension_length": "extension_length",
                    "extension_time": "extension_time",
                    "retraction_time": "retraction_time",
                },
            ),
        ):
            values = state.snapshot.get(section)
            if isinstance(values, dict):
                fields.update({field: values.get(column) for column, field in names.items()})
        credits = state.snapshot.get("credits", state.snapshot.get("creators"))
        if isinstance(credits, list):
            fields["credits"] = [credit.get("name") for credit in credits if isinstance(credit, dict)]
        return fields

    async def list_history(
        self, build_id: int, *, selector: PageSelector = FIRST_PAGE, page_size: int = 20
    ) -> Page[BuildHistorySummary]:
        if not 1 <= page_size <= 100:
            msg = "page_size must be between 1 and 100"
            raise ValueError(msg)
        if not 0 <= selector.offset <= MAX_PAGE_OFFSET:
            msg = "offset must be between 0 and MAX_PAGE_OFFSET"
            raise ValueError(msg)
        before = aliased(BuildHistorySnapshot)
        after = aliased(BuildHistorySnapshot)
        statement = (
            select(BuildHistoryEntry, before.build_revision, after.build_revision)
            .outerjoin(before, before.id == BuildHistoryEntry.before_state_id)
            .outerjoin(after, after.id == BuildHistoryEntry.after_state_id)
            .where(BuildHistoryEntry.build_id == build_id)
        )
        session = self._session()
        total = await session.scalar(
            select(func.count()).select_from(BuildHistoryEntry).where(BuildHistoryEntry.build_id == build_id)
        )
        order = KeysetOrder("newest", BuildHistoryEntry.id, BuildHistoryEntry.id, descending=True)
        page = await order.page(session, statement, selector, page_size=page_size, total=total or 0)
        return replace(page, items=tuple(_summary(record, old, new) for record, old, new in page.items))

    async def get_history_entry(self, build_id: int, entry_id: int) -> BuildHistoryDetail | None:
        session = self._session()
        record = await session.scalar(
            select(BuildHistoryEntry).where(BuildHistoryEntry.id == entry_id, BuildHistoryEntry.build_id == build_id)
        )
        if record is None:
            return None
        before = await session.get(BuildHistorySnapshot, record.before_state_id) if record.before_state_id else None
        after = await session.get(BuildHistorySnapshot, record.after_state_id) if record.after_state_id else None
        summary = _summary(record, before.build_revision if before else None, after.build_revision if after else None)
        return BuildHistoryDetail(
            id=summary.id,
            build_id=summary.build_id,
            recorded_at=summary.recorded_at,
            context=summary.context,
            before_revision=summary.before_revision,
            after_revision=summary.after_revision,
            before=_decode(before),
            after=_decode(after),
        )


def _decode(state: BuildHistorySnapshot | None) -> BuildStateSnapshot | None:
    return (
        None
        if state is None
        else BuildStateSnapshot(
            schema_version=state.schema_version, revision=state.build_revision, payload=deepcopy(state.snapshot)
        )
    )


def _summary(record: BuildHistoryEntry, before: int | None, after: int | None) -> BuildHistorySummary:
    return BuildHistorySummary(
        id=record.id,
        build_id=record.build_id,
        recorded_at=record.recorded_at,
        before_revision=before,
        after_revision=after,
        context=BuildEditContext(
            operation=cast(HistoryOperation, record.operation),
            source=record.source,
            actor_account_id=record.actor_account_id,
            actor_system=record.actor_system,
            correlation_kind=record.correlation_kind,
            correlation_id=record.correlation_id,
            operation_id=record.operation_id,
        ),
    )
