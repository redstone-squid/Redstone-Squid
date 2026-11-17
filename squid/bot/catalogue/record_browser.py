"""Published record standings and links to their holder builds."""

from collections.abc import Awaitable, Callable
from functools import partial

from discord.utils import format_dt

import squid_ui as sl
import squid_ui.ext.discord as sd
import squid_ui_widgets as sp
from squid.bot.catalogue.rows import record_row
from squid.bot.follows import follow_control
from squid.bot.ui import tr, truncate_display_text
from squid.core.pagination import PageSelector
from squid.notifications.domain import SubscriptionKind
from squid.records.application import RecordService
from squid.records.application.models import PublishedTitle
from squid.records.domain.models import BaseTitle
from squid_i18n import md_tr
from squid_ui.sources import window_fingerprint

CATALOGUE_PAGE_SIZE = 15
"""Rows per page in the catalogue's build, record, and search lists; viewers can change it."""


class RecordSource:
    capabilities = sl.sources.SourceCapabilities(
        backward=True,
        offsets=True,
        jumpable=True,
        count=sl.sources.CountPrecision.EXACT,
    )

    def __init__(self, records: RecordService) -> None:
        self._records = records

    async def fetch(self, position: sl.sources.Position, extent: int) -> sl.sources.Window[PublishedTitle]:
        page = await self._records.list_page(
            selector=PageSelector(offset=position.offset), page_size=extent, resolved_only=True
        )
        # This source exposes offset jumps; the service may return ID links at offset zero.
        return sl.sources.Window(
            sl.sources.Position(str(page.items[0].id) if page.items else None, position.offset),
            page.items,
            previous=sl.sources.Position(offset=max(0, position.offset - extent)) if page.prev is not None else None,
            next=sl.sources.Position(offset=position.offset + len(page.items)) if page.next is not None else None,
            total=page.total,
        )

    def fingerprint(self, window: sl.sources.Window[PublishedTitle]) -> str:
        return window_fingerprint(window.items, lambda record: str(record.id))


def _record_label(record: PublishedTitle) -> str:
    scope = record.version_scope.replace("_", " ")
    return truncate_display_text(f"{record.one_line_title} · {scope}", 100)


def _record_summary(record: PublishedTitle) -> sl.TextLike:
    return record_row(record.one_line_title, record.version_scope)


def _comparison_rule(base_title: BaseTitle) -> sl.TextLike:
    match base_title:
        case BaseTitle.FIRST:
            return tr(t"record-rule-first = Earliest known completion date.")
        case BaseTitle.SMALLEST:
            return tr(t"record-rule-smallest = Lowest volume; earliest completion breaks a tie.")
        case BaseTitle.FASTEST:
            return tr(t"record-rule-fastest = Fastest worst-case timing; earliest completion breaks a tie.")
        case BaseTitle.FASTEST_SMALLEST:
            return tr(t"record-rule-fastest-smallest = Lowest volume, then fastest timing, then earliest completion.")
        case BaseTitle.SMALLEST_FASTEST:
            return tr(t"record-rule-smallest-fastest = Fastest timing, then lowest volume, then earliest completion.")


def record_browser(
    records: RecordService,
    open_build: Callable[[int, sl.PressEvent], Awaitable[None]],
) -> sp.Browser[PublishedTitle, sl.ComponentsV2Target, sd.Request]:
    """Keep the selected record available while its holder is opened in the catalogue."""

    def detail(record: PublishedTitle) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request]:
        computed_at = format_dt(record.computed_at, style="R")
        return sl.section(
            sl.heading(record.title),
            sl.paragraph(_comparison_rule(record.base_title)),
            sl.note(md_tr(t"record-browse-computed = Computed {computed_at}")),
            record.subtitle and sl.paragraph(record.subtitle),
            sl.note(f"{record.build_kind.value} · {record.version_scope.replace('_', ' ')}"),
            sl.action_controls(follow_control(SubscriptionKind.RECORD, record.title_id), key="record-follow"),
            sl.action_controls(
                *(
                    sl.action_control(
                        md_tr(
                            t"record-browse-open-holder = Open build #{build_id!s}",
                        ),
                        partial(open_build, build_id),
                        key=f"holder-{build_id}",
                    )
                    for build_id in record.holder_build_ids
                ),
                key="record-holders",
            )
            if record.holder_build_ids
            else sl.note(tr(t"record-browse-no-holders = No confirmed holders yet.")),
        )

    return sp.Browser(
        RecordSource(records),
        key="records",
        identity=lambda record: str(record.id),
        label=_record_label,
        summary=_record_summary,
        detail=detail,
        page_size=CATALOGUE_PAGE_SIZE,
        show_page_size=True,
        title=tr(t"record-browse-title = Records"),
        empty=tr(t"record-browse-empty = No published records are available."),
    )
