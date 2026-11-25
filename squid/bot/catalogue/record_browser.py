"""Published records and links to their holder builds."""

from collections.abc import Awaitable, Callable

from discord.utils import format_dt

import squid_ui as sl
import squid_ui.ext.discord as sd
import squid_ui_widgets as sp
from squid.bot.catalogue.record_display import record_row, record_standings
from squid.bot.follows import follow_control
from squid.bot.ui import tr, truncate_display_text
from squid.core.actors import ActingContext
from squid.core.notifications.domain import SubscriptionKind
from squid.core.pagination import PageSelector
from squid.core.records.application import RecordService
from squid.core.records.application.models import PublishedRecordResult
from squid.core.records.domain.models import VersionScope
from squid_i18n import md_tr
from squid_ui.ext.discord.target_types import ComponentsV2Target
from squid_ui.sources import batch_fingerprint

CATALOGUE_PAGE_SIZE = 15
"""Rows per page in the catalogue's build, record, and search lists; viewers can change it."""


class RecordSource:
    capabilities = sl.sources.SourceCapabilities(
        backward=True,
        offsets=True,
        jumpable=True,
        count=sl.sources.CountPrecision.EXACT,
    )

    def __init__(self, records: RecordService, version_scope: VersionScope) -> None:
        self._records = records
        self._version_scope = version_scope

    async def fetch(self, position: sl.sources.Position, fetch_size: int) -> sl.sources.Batch[PublishedRecordResult]:
        page = await self._records.list_page(
            selector=PageSelector(offset=position.offset),
            page_size=fetch_size,
            version_scope=self._version_scope,
            context=ActingContext.current(),
        )
        # This source exposes offset jumps; the service may return ID links at offset zero.
        return sl.sources.Batch(
            sl.sources.Position(str(page.items[0].result_id) if page.items else None, position.offset),
            page.items,
            previous=sl.sources.Position(offset=max(0, position.offset - fetch_size))
            if page.prev is not None
            else None,
            next=sl.sources.Position(offset=position.offset + len(page.items)) if page.next is not None else None,
            item_count=page.total,
        )

    def fingerprint(self, batch: sl.sources.Batch[PublishedRecordResult]) -> str:
        return batch_fingerprint(batch.items, lambda record: str(record.result_id))


def _record_label(record: PublishedRecordResult) -> str:
    return truncate_display_text(record.one_line_title, 100)


def record_browser(
    records: RecordService,
    open_build: Callable[[int, sl.PressEvent], Awaitable[None]],
    *,
    version_scope: VersionScope,
) -> sp.Browser[PublishedRecordResult, ComponentsV2Target, sd.Request]:
    """Keep the selected record available while its holder is opened in the catalogue."""

    def detail(record: PublishedRecordResult) -> sl.LayoutNode[ComponentsV2Target, sd.Request]:
        computed_at = format_dt(record.computed_at, style="R")
        checked_at = format_dt(record.last_checked_at, style="R") if record.last_checked_at is not None else None
        details = [sl.note(md_tr(t"record-browse-computed = Computed {computed_at}"))]
        if checked_at is not None:
            details.append(sl.note(md_tr(t"record-browse-checked = Last checked {checked_at}")))
        if record.refresh_pending:
            details.append(sl.note(tr(t"record-browse-pending = Newer inputs are waiting to be checked.")))
        return record_standings(
            record.title,
            record.subtitle,
            record.criterion,
            record.version_scope,
            record.holder_build_ids,
            open_build,
            build_kind=record.build_kind,
            details=(
                *details,
                sl.action_controls(follow_control(SubscriptionKind.RECORD, record.record_id), key="record-follow"),
            ),
        )

    return sp.Browser(
        RecordSource(records, version_scope),
        key="records",
        identity=lambda record: str(record.result_id),
        label=_record_label,
        summary=lambda record: record_row(record.title, record.subtitle),
        detail=detail,
        fetch_size=CATALOGUE_PAGE_SIZE,
        show_fetch_size=True,
        title=tr(t"record-browse-title = Records"),
        empty=tr(t"record-browse-empty = No published records are available."),
    )
