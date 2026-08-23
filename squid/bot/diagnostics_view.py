"""Interactive Components V2 rendering for stored error reports.

The browser is a mounted squid-layouts component: the list ↔ detail switch is a state change,
the traceback pages through the engine's budget solver (which replaced the hand-tuned
PAGE_CHARS constant), and author lock, expiry, and error routing belong to the mount.
"""

import dataclasses
import io
from collections.abc import Sequence

import discord

import squid_layouts as sl
from squid.bot.ui import CHROME, L
from squid.diagnostics.domain import ErrorReport

SESSION_SECONDS = 300


class ErrorReportBrowser(sl.Component):
    """A list of recent reports, each openable in place and readable to its last line.

    Holds the reports it was constructed with rather than a service: `recent` already fetches
    every row the select can offer, so opening one is a re-render, not a round trip.
    """

    # Opaque: a stored report is a value this browser shows and never writes to, and its
    # redacted `context` mapping is a plain dict, which the state immutability check refuses.
    detail: ErrorReport | None = sl.state(None, opaque=True)
    matches: int = sl.state(1)

    def __init__(
        self,
        reports: Sequence[ErrorReport] = (),
        *,
        report: ErrorReport | None = None,
        matches: int = 1,
    ) -> None:
        self._reports = tuple(reports)
        if report is not None:
            self.detail = report
            self.matches = matches

    @property
    def reports(self) -> tuple[ErrorReport, ...]:
        """The reports the list offers."""
        return self._reports

    def chrome(self) -> sl.semantic.Chrome:
        """This browser's chrome: temporal paging labels and a footer that names the attachment."""
        return dataclasses.replace(
            CHROME,
            not_yours=L(t"These error controls belong to someone else."),
            previous=L(t"Earlier"),
            next=L(t"Later"),
            page_footer=lambda page, pages: L(
                "{section} — page {page} of {pages} · the attachment has the whole report",
                section=L("Traceback"),
                page=page,
                pages=pages,
            ),
        )

    def render(self) -> sl.Document:
        nodes = self._render_detail() if self.detail is not None else self._render_list()
        return sl.Document(tuple(nodes))

    def _render_list(self) -> Sequence[sl.LayoutNode]:
        entries = tuple(_list_line(report) for report in self._reports)
        body: sl.TextLike = "\n".join(entries) or L(t"Nothing has failed within the retention window.")
        nodes: list[sl.LayoutNode] = [sl.section(sl.heading(L(t"Recent errors")), sl.truncate(sl.paragraph(body)))]
        if self._reports:
            nodes.append(
                sl.primitives.SelectMenu(
                    options=tuple(
                        sl.primitives.Option(
                            label=report.reference,
                            value=str(index),
                            description=_list_description(report),
                        )
                        for index, report in enumerate(self._reports)
                    ),
                    on_select=self._open,
                    key="open",
                    placeholder=L(t"Choose an error to open"),
                )
            )
        nodes.append(sl.primitives.Row((self._close_button(),)))
        return nodes

    def _render_detail(self) -> Sequence[sl.LayoutNode]:
        report = self.detail
        assert report is not None
        reference = report.reference
        traceback_text: sl.TextLike = report.traceback.strip() or L(t"No traceback was recorded.")
        children: list[sl.LayoutNode] = [
            sl.primitives.Heading(L(t"Error {reference}")),
            # Opens at the end because the failing frame is the last one.
            sl.primitives.Code(traceback_text, overflow=sl.primitives.Paginate(key="traceback", initial="end")),
        ]
        if report.log_tail:
            # The run-up to the failure: its last lines matter most, so it trims from the
            # front; the attachment carries all of it.
            children.append(sl.primitives.Heading(L(t"Log tail"), level=3, priority=2))
            children.append(
                sl.primitives.Code(
                    "\n".join(report.log_tail), overflow=sl.primitives.Truncate(keep="tail"), priority=-8
                )
            )
        children.append(sl.fields(*_summary_fields(report, self.matches)))
        children.append(
            sl.download(L(t"Full report"), report_asset(report), key="full-report", emphasis=sl.semantic.Emphasis.STRONG)
        )
        controls: list[sl.primitives.Button] = []
        if self._reports:
            controls.append(sl.primitives.Button(label=L(t"Back"), on_click=self._back, key="back"))
        controls.append(self._close_button())
        return [sl.stack(*children), sl.primitives.Row(tuple(controls))]

    def _close_button(self) -> sl.primitives.Button:
        return sl.primitives.Button(
            label=L(t"Close"),
            on_click=self._close,
            key="close",
            style=sl.primitives.ActionStyle.SECONDARY,
        )

    async def _open(self, event: sl.SelectionEvent) -> None:
        report = self._reports[int(event.values[0])]
        self.detail = report
        self.matches = 1

    async def _back(self, event: sl.PressEvent) -> None:
        self.detail = None

    async def _close(self, event: sl.PressEvent) -> None:
        await event.finish()


def report_attachment(report: ErrorReport) -> discord.File:
    """Bundle the traceback and the log tail, for reading outside Discord."""
    asset = report_asset(report)
    assert isinstance(asset.source, sl.semantic.InlineAsset)
    return discord.File(io.BytesIO(asset.source.data), filename=asset.name)


def report_asset(report: ErrorReport) -> sl.semantic.Asset:
    """Describe the full report as a portable inline text asset."""
    lines = [
        f"reference: {report.reference}",
        f"correlation_id: {report.correlation_id}",
        f"occurred_at: {report.occurred_at}",
        f"surface: {report.surface}",
        f"origin: {report.origin or '-'}",
        f"exception: {report.exception_type}",
        f"code: {report.error_code.value if report.error_code else '-'}",
        f"work_lost: {report.work_lost}",
        f"message: {report.message}",
        f"context: {dict(report.context)}",
        "",
        "traceback:",
        report.traceback,
        "",
        "log tail:",
        *report.log_tail,
    ]
    return sl.semantic.Asset(
        key="full-report",
        name=f"error-{report.reference}.txt",
        media_type="text/plain",
        source=sl.semantic.InlineAsset("\n".join(lines).encode()),
    )


def _summary_fields(report: ErrorReport, matches: int) -> list[sl.semantic.Field]:
    entries = [
        sl.field(
            L(t"When"),
            sl.md(t"{report.occurred_at.to_stdlib()}"),
        ),
        sl.field(L(t"Where"), sl.md(t"{report.surface} — {report.origin or '—'}")),
        sl.field(L(t"Exception"), sl.md(t"{report.exception_type}")),
        sl.field(L(t"Full ID"), sl.md("{identifier}", identifier=sl.raw_md(f"`{report.correlation_id}`"))),
    ]
    if report.work_lost:
        entries.append(sl.field(L(t"Work lost"), L(t"This job was abandoned; nothing will retry it.")))
    if matches > 1:
        # The reference is a 48-bit prefix, not a key. Silently showing the newest of several
        # would have a moderator confidently reading the wrong incident.
        entries.append(
            sl.field(
                L(t"Ambiguous"),
                L("{count} reports share this reference; this is the newest.", count=matches),
            )
        )
    return entries


def _list_line(report: ErrorReport) -> str:
    marker = ":warning: " if report.work_lost else ""
    return f"{marker}`{report.reference}` — {report.exception_type} in {report.origin or report.surface}"


def _list_description(report: ErrorReport) -> str:
    return f"{report.exception_type} · {report.origin or report.surface}"
