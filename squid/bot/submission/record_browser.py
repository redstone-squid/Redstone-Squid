"""Published record standings and links to their holder builds."""

from collections.abc import Awaitable, Callable
from functools import partial

import squid_ui as sl
import squid_ui_discord as sd
import squid_ui_widgets as sp
from squid.bot.ui import tr
from squid.core.pagination import PageSelector
from squid.records.application import RecordService
from squid.records.application.models import PublishedTitle
from squid_i18n import md_tr
from squid_ui.sources import window_fingerprint


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
        return sl.sources.Window(
            sl.sources.Position(str(page.items[0].id) if page.items else None, position.offset),
            page.items,
            has_previous=page.prev is not None,
            has_next=page.next is not None,
            total=page.total,
        )

    def fingerprint(self, window: sl.sources.Window[PublishedTitle]) -> str:
        return window_fingerprint(window.items, lambda record: str(record.id))


def record_browser(
    records: RecordService,
    open_build: Callable[[int, sl.PressEvent], Awaitable[None]],
) -> sp.Browser[PublishedTitle, sl.ComponentsV2Target, sd.Request]:
    """Keep the selected record available while its holder is opened in the catalogue."""

    def detail(record: PublishedTitle) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request]:
        return sl.section(
            sl.heading(record.title),
            record.subtitle and sl.paragraph(record.subtitle),
            sl.note(f"{record.build_kind.value} · {record.version_scope.replace('_', ' ')}"),
            sl.action_controls(
                *(
                    sl.action_control(
                        md_tr(
                            t"record-browse-open-holder = Open build #{(build_id_text := str(build_id))}",  # noqa: F841 - captured translation binding
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
        label=lambda record: record.title,
        summary=lambda record: sl.md(t"**{record.title}** — {record.version_scope.replace('_', ' ')}"),
        detail=detail,
        page_size=8,
        title=tr(t"record-browse-title = Records"),
        empty=tr(t"record-browse-empty = No published records are available."),
    )
