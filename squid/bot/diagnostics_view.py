"""Browse stored error reports and download complete traceback and log attachments.

The mounted screen owns reader access and expiry. Periodic checks compare the
recent report IDs and offer refresh without replacing the report being read.
"""

import dataclasses
import io
from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol, cast, override

import discord

import squid_ui as sl
import squid_ui_discord as sd
import squid_ui_widgets as sp
from squid.bot.diagnostics_refresh import ErrorReportRefresh
from squid.bot.ui import CHROME, tr
from squid.diagnostics.models import ErrorReport

SESSION_SECONDS = 300
RECENT_LIMIT = 100


class ErrorReportOperations(Protocol):
    """Service operations required by the live diagnostics screen."""

    async def lookup(self, reference: str) -> tuple[ErrorReport, int]: ...

    async def recent(self, *, limit: int = 20, work_lost_only: bool = False) -> Sequence[ErrorReport]: ...

    async def clear_all(self) -> int: ...


def _traceback_footer(page: int, pages: int) -> sl.TextLike:
    section = tr(t"diagnostics-view-traceback = Traceback")
    return tr(
        t"diagnostics-attachment-page-hint = {section} — page {page} of {pages} · the attachment has the whole report"
    )


ERROR_CHROME = dataclasses.replace(
    CHROME,
    not_yours=tr(t"diagnostics-controls-not-owner = These error controls belong to someone else."),
    previous=tr(t"diagnostics-view-earlier = Earlier"),
    next=tr(t"diagnostics-view-later = Later"),
    page_footer=_traceback_footer,
)


class _ReportUpdates(sl.Component[sl.ComponentsV2Target, sd.Request]):
    """Check for report changes without replacing the browser the reader is using."""

    def __init__(
        self,
        operations: ErrorReportOperations,
        shown: tuple[ErrorReport, ...],
        *,
        work_lost_only: bool,
        refresh: Callable[[sl.PressEvent], Awaitable[None]],
    ) -> None:
        self._operations = operations
        self._shown = tuple(report.id for report in shown)
        self._work_lost_only = work_lost_only
        self._notice = _ReportUpdateNotice()
        self._controls = _ReportUpdateControls(refresh)
        self.monitor = self.own(ErrorReportRefresh(self._check))

    @property
    def changed(self) -> sl.runtime.Resource[bool]:
        return self.monitor.changes

    async def _check(self) -> bool:
        reports = await self._operations.recent(limit=RECENT_LIMIT, work_lost_only=self._work_lost_only)
        return tuple(report.id for report in reports) != self._shown

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        self.provide(_REPORT_MONITOR, self.monitor)
        return self.boundary(self._notice, key="notice"), self.boundary(self._controls, key="controls")


_REPORT_MONITOR = sl.runtime.ContextKey[ErrorReportRefresh]("report-monitor")


class _ReportUpdateNotice(sl.Component[sl.ComponentsV2Target, sd.Request]):
    """Display the shared monitor's latest comparison without changing the open report."""

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        status = self.inject(_REPORT_MONITOR).changes.status
        changed = (
            status.value
            if isinstance(status, sl.resources.Ready)
            else (status.previous.value if status.previous is not None else False)
        )
        nodes: list[sl.LayoutNode[sl.ComponentsV2Target, sd.Request]] = []
        if isinstance(status, sl.resources.Failed):
            nodes.append(
                sl.note(
                    tr(t"diagnostics-update-check-failed = Could not check for updates. You can still refresh reports.")
                )
            )
        elif changed:
            nodes.append(
                sl.note(tr(t"diagnostics-report-list-stale = The report list has changed. Refresh when you are ready."))
            )
        return tuple(nodes)


class _ReportUpdateControls(sl.Component[sl.ComponentsV2Target, sd.Request]):
    """Keep manual refresh available while automatic checks load or fail."""

    def __init__(self, refresh: Callable[[sl.PressEvent], Awaitable[None]]) -> None:
        self._refresh = refresh

    @override
    def render(self) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request]:
        status = self.inject(_REPORT_MONITOR).changes.status
        changed = isinstance(status, sl.resources.Ready) and status.value
        return sl.action_controls(
            sl.action_control(
                tr(t"diagnostics-view-refresh-reports = Refresh reports"),
                self._refresh,
                key="refresh",
                tone=sl.Tone.INFO if changed else sl.Tone.NEUTRAL,
            ),
            key="report-updates",
        )


class ErrorReportScreen(sd.Screen[int]):
    """Browse reports and complete with the number removed when clearing them."""

    policy = sd.ResponseSpec(
        timeout=SESSION_SECONDS,
        audience=sd.Private(
            tr(
                t"diagnostics-path-privacy-warning = An error report names "
                t"internal paths, so it is never posted in a channel."
            )
        ),
        chrome=ERROR_CHROME,
        session=sd.SessionSpec("errors"),
        # The report monitor checks for new errors in the background; its notice needs a redraw.
        live_updates=True,
    )

    work_lost_only: bool = sl.state(default=False)
    confirming_clear: bool = sl.state(default=False)
    cleared_count: int | None = sl.state(None)
    _reports: tuple[ErrorReport, ...] = sl.state((), persist=False, opaque=True)
    _updates: _ReportUpdates | None = sl.state(None, persist=False, opaque=True)
    _browser: sp.Browser[ErrorReport, sl.ComponentsV2Target, sd.Request] | None = sl.state(
        None, persist=False, opaque=True
    )

    def __init__(
        self,
        operations: ErrorReportOperations,
        *,
        reference: str | None = None,
        can_clear: bool = False,
        authorize_clear: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        self._operations = operations
        self._reference = reference
        self._can_clear = can_clear
        self._authorize_clear = authorize_clear
        self._detail: ErrorReport | None = None
        self._matches = 1
        self._clear_decision = sp.confirm(
            sl.section(
                sl.heading(tr(t"diagnostics-view-clear-every-error-report = Clear every error report?")),
                sl.paragraph(
                    tr(t"diagnostics-delete-warning = This permanently deletes reports that have not expired yet.")
                ),
            ),
            key="clear-errors",
            on_confirm=self._clear,
            on_cancel=self._cancel_clear,
            confirm_label=tr(t"diagnostics-view-delete-reports = Delete reports"),
        )

    @override
    async def on_load(self) -> None:
        """Load a requested reference or the recent-report list when the screen mounts."""
        if self._reference is not None:
            self._detail, self._matches = await self._operations.lookup(self._reference)
            return
        await self._load_recent()

    async def _load_recent(self) -> None:
        self._reports = tuple(await self._operations.recent(limit=RECENT_LIMIT, work_lost_only=self.work_lost_only))
        self._updates = _ReportUpdates(
            self._operations,
            self._reports,
            work_lost_only=self.work_lost_only,
            refresh=self._refresh_reports,
        )
        self._browser = sp.Browser(
            sl.sources.list_source(self._reports),
            key="error-reports",
            identity=lambda report: report.reference,
            label=lambda report: report.reference,
            summary=_list_line,
            detail=lambda report: self._render_report(report, 1),
            page_size=10,
            title=tr(t"diagnostics-view-recent-errors = Recent errors"),
            empty=tr(t"diagnostics-no-recent-errors = Nothing has failed within the retention window."),
        )

    @property
    def reports(self) -> tuple[ErrorReport, ...]:
        """The reports the list offers."""
        return self._reports

    @override
    def render(self) -> sl.Document[sl.ComponentsV2Target, sd.Request]:
        if self.cleared_count is not None:
            count = self.cleared_count
            return sl.Document(
                (
                    sl.section(
                        sl.heading(tr(t"diagnostics-view-errors-cleared = Errors cleared")),
                        sl.paragraph(
                            tr(
                                (
                                    t"diagnostics-view-deleted-stored-error-reports = Deleted {count} stored error "
                                    t"reports."
                                )
                            )
                        ),
                    ),
                )
            )
        if self.confirming_clear:
            return sl.Document((self.boundary(self._clear_decision, key="clear-decision"),))
        if self._detail is not None:
            return sl.Document((self._render_report(self._detail, self._matches),))
        if self._browser is None:
            return sl.Document((sl.status(tr(t"diagnostics-view-loading-error-reports = Loading error reports.")),))
        assert self._updates is not None
        return sl.Document(
            (
                self.boundary(self._browser, key="browser"),
                self.boundary(self._updates, key="updates"),
                sl.action_controls(
                    sl.action_control(
                        tr(t"diagnostics-view-show-all = Show all")
                        if self.work_lost_only
                        else tr(t"diagnostics-view-show-work-lost-only = Show work lost only"),
                        self._toggle_filter,
                        key="filter",
                    ),
                    sl.action_control(
                        tr(t"diagnostics-view-clear-all = Clear all"),
                        self._confirm_clear,
                        key="clear",
                        tone=sl.Tone.DANGER,
                    )
                    if self._can_clear
                    else None,
                    key="error-actions",
                ),
            )
        )

    def _render_report(self, report: ErrorReport, matches: int) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request]:
        reference = report.reference
        traceback_text: sl.TextLike = report.traceback.strip() or tr(
            t"diagnostics-view-no-traceback-was-recorded = No traceback was recorded."
        )
        children: list[sl.LayoutNode[sl.ComponentsV2Target, sd.Request]] = [
            sd.v2.heading(tr(t"diagnostics-view-error = Error {reference}")),
            # Opens at the end because the failing frame is the last one.
            sd.v2.code(traceback_text, overflow=sl.primitives.Paginate(key="traceback", initial="end")),
        ]
        if report.log_tail:
            # The run-up to the failure: its last lines matter most, so it trims from the
            # front; the attachment carries all of it.
            children.append(sd.v2.heading(tr(t"diagnostics-view-log-tail = Log tail"), level=3, priority=2))
            children.append(
                sd.v2.code("\n".join(report.log_tail), overflow=sl.primitives.Truncate(keep="tail"), priority=-8)
            )
        children.append(sl.fields(*_summary_fields(report, matches)))
        children.append(
            sl.download(
                tr(t"diagnostics-view-full-report = Full report"),
                report_asset(report),
                key="full-report",
                emphasis=sl.semantic.Emphasis.STRONG,
            )
        )
        return sl.stack(*children)

    async def _refresh_reports(self, event: sl.PressEvent) -> None:
        await event.acknowledge()
        await self._load_recent()

    async def _toggle_filter(self, event: sl.PressEvent) -> None:
        await event.acknowledge()
        self.work_lost_only = not self.work_lost_only
        await self._load_recent()

    async def _confirm_clear(self, _event: sl.PressEvent) -> None:
        self.confirming_clear = True

    async def _clear(self, event: sp.TransitionEvent[sp.DecisionState]) -> None:
        if self._authorize_clear is None or not await self._authorize_clear():
            self.confirming_clear = False
            await event.source.notice(
                tr(t"diagnostics-clear-forbidden = You are no longer allowed to clear error reports.")
            )
            return
        self.cleared_count = await self._operations.clear_all()
        self.confirming_clear = False
        self.complete(cast(sd.ActionEvent, event.source), self.cleared_count)

    async def _cancel_clear(self, _event: sp.TransitionEvent[sp.DecisionState]) -> None:
        self.confirming_clear = False


def report_attachment(report: ErrorReport) -> discord.File:
    """Bundle the traceback and the log tail, for reading outside Discord."""
    asset = report_asset(report)
    assert isinstance(asset.source, sl.document.InlineAsset)
    return discord.File(io.BytesIO(asset.source.data), filename=asset.name)


def report_asset(report: ErrorReport) -> sl.document.Asset:
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
    return sl.document.Asset(
        key="full-report",
        name=f"error-{report.reference}.txt",
        media_type="text/plain",
        source=sl.document.InlineAsset("\n".join(lines).encode()),
    )


def _summary_fields(report: ErrorReport, matches: int) -> list[sl.semantic.Field]:
    identifier = sl.raw_md(f"`{report.correlation_id}`")
    entries = [
        sl.field(
            tr(t"diagnostics-view-when = When"),
            sl.md(t"{report.occurred_at.to_stdlib()}"),
        ),
        sl.field(tr(t"diagnostics-view-where = Where"), sl.md(t"{report.surface} — {report.origin or '—'}")),
        sl.field(tr(t"diagnostics-view-exception = Exception"), sl.md(t"{report.exception_type}")),
        sl.field(tr(t"diagnostics-view-full-id = Full ID"), sl.md(t"{identifier}")),
    ]
    if report.work_lost:
        entries.append(
            sl.field(
                tr(t"diagnostics-view-work-lost = Work lost"),
                tr(t"diagnostics-abandoned-job-note = This job was abandoned; nothing will retry it."),
            )
        )
    if matches > 1:
        # The reference is a 48-bit prefix, not a key. Silently showing the newest of several
        # would have a moderator confidently reading the wrong incident.
        count = matches
        entries.append(
            sl.field(
                tr(t"diagnostics-view-ambiguous = Ambiguous"),
                tr(t"diagnostics-shared-reference-note = {count} reports share this reference; this is the newest."),
            )
        )
    return entries


def _list_line(report: ErrorReport) -> str:
    marker = ":warning: " if report.work_lost else ""
    return f"{marker}`{report.reference}` — {report.exception_type} in {report.origin or report.surface}"
