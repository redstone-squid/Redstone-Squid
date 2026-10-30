"""Interactive Components V2 rendering for stored error reports.

The browser is a mounted squid-ui component: the list ↔ detail switch is a state change,
the traceback pages through the engine's budget solver (which replaced the hand-tuned
PAGE_CHARS constant), and author lock, expiry, and error routing belong to the mount.
"""

import dataclasses
import io
from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol, override

import discord

import squid_ui as sl
import squid_ui_discord as sd
import squid_ui_widgets as sp
from squid.bot.diagnostics_refresh import ErrorReportRefresh
from squid.bot.ui import CHROME, tr
from squid.diagnostics.domain import ErrorReport

SESSION_SECONDS = 300
RECENT_LIMIT = 100


class ErrorReportOperations(Protocol):
    """Service operations required by the live diagnostics screen."""

    async def lookup(self, reference: str) -> tuple[ErrorReport, int]: ...

    async def recent(self, *, limit: int = 20, work_lost_only: bool = False) -> Sequence[ErrorReport]: ...

    async def clear_all(self) -> int: ...


def _traceback_footer(page: int, pages: int) -> sl.TextLike:
    section = tr(t"Traceback")
    return tr(t"{section} — page {page} of {pages} · the attachment has the whole report")


ERROR_CHROME = dataclasses.replace(
    CHROME,
    not_yours=tr(t"These error controls belong to someone else."),
    previous=tr(t"Earlier"),
    next=tr(t"Later"),
    page_footer=_traceback_footer,
)


class _ReportChanges(sl.runtime.SharedState):
    """Share the latest report comparison between the notice and refresh controls."""

    def __init__(self, bus: sl.runtime.TopicBus, check: Callable[[], Awaitable[bool]]) -> None:
        super().__init__(bus)
        self._check = check
        self.changed.replace(value=False)

    @sl.resource
    async def changed(self) -> bool:
        return await self._check()


class _ReportUpdates(sl.Component[sl.ComponentsV2Target]):
    """Check for report changes without replacing the browser the reader is using."""

    def __init__(
        self,
        operations: ErrorReportOperations,
        shown: tuple[ErrorReport, ...],
        *,
        bus: sl.runtime.TopicBus,
        work_lost_only: bool,
        refresh: Callable[[sl.PressEvent], Awaitable[None]],
    ) -> None:
        self._operations = operations
        self._shown = shown
        self._work_lost_only = work_lost_only
        self._notice = _ReportUpdateNotice()
        self._controls = _ReportUpdateControls(refresh)
        self._changes = _ReportChanges(bus, self._check)
        self.monitor = self.own(ErrorReportRefresh(self.changed))

    @property
    def changed(self) -> sl.runtime.Resource[bool]:
        return self._changes.changed

    async def _check(self) -> bool:
        reports = await self._operations.recent(limit=RECENT_LIMIT, work_lost_only=self._work_lost_only)
        return tuple(reports) != self._shown

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        self.provide(_REPORT_MONITOR, self.monitor)
        return self.boundary(self._notice, key="notice"), self.boundary(self._controls, key="controls")


_REPORT_MONITOR = sl.runtime.ContextKey[ErrorReportRefresh]("report-monitor")


class _ReportUpdateNotice(sl.Component[sl.ComponentsV2Target]):
    """Display the shared monitor's latest comparison without changing the open report."""

    @override
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        status = self.inject(_REPORT_MONITOR).changes.status
        changed = (
            status.value
            if isinstance(status, sl.resources.Ready)
            else (status.previous.value if status.previous is not None else False)
        )
        nodes: list[sl.LayoutNode[sl.ComponentsV2Target]] = []
        if isinstance(status, sl.resources.Failed):
            nodes.append(sl.note(tr(t"Could not check for updates. You can still refresh reports.")))
        elif changed:
            nodes.append(sl.note(tr(t"The report list has changed. Refresh when you are ready.")))
        return tuple(nodes)


class _ReportUpdateControls(sl.Component[sl.ComponentsV2Target]):
    """Keep manual refresh available while automatic checks load or fail."""

    def __init__(self, refresh: Callable[[sl.PressEvent], Awaitable[None]]) -> None:
        self._refresh = refresh

    @override
    def render(self) -> sl.LayoutNode[sl.ComponentsV2Target]:
        status = self.inject(_REPORT_MONITOR).changes.status
        changed = isinstance(status, sl.resources.Ready) and status.value
        return sl.action_controls(
            sl.action_control(
                tr(t"Refresh reports"),
                self._refresh,
                key="refresh",
                tone=sl.Tone.INFO if changed else sl.Tone.NEUTRAL,
            ),
            key="report-updates",
        )


class ErrorReportScreen(sd.Screen):
    """An error browser that ends when closed, cleared, replaced, or timed out."""

    session = sd.SessionSpec("errors")
    timeout = SESSION_SECONDS
    audience = sd.Private(tr(t"An error report names internal paths, so it is never posted in a channel."))
    chrome = ERROR_CHROME

    work_lost_only: bool = sl.state(default=False)
    confirming_clear: bool = sl.state(default=False)
    cleared_count: int | None = sl.state(None)
    _reports: tuple[ErrorReport, ...] = sl.state((), persist=False, opaque=True)
    _updates: _ReportUpdates | None = sl.state(None, persist=False, opaque=True)
    _browser: sp.Browser[ErrorReport, sl.ComponentsV2Target] | None = sl.state(None, persist=False, opaque=True)

    def __init__(
        self,
        operations: ErrorReportOperations,
        *,
        bus: sl.runtime.TopicBus,
        reference: str | None = None,
        can_clear: bool = False,
        authorize_clear: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        self._operations = operations
        self._bus = bus
        self._reference = reference
        self._can_clear = can_clear
        self._authorize_clear = authorize_clear
        self._detail: ErrorReport | None = None
        self._matches = 1
        self._clear_decision = sp.confirm(
            sl.section(
                sl.heading(tr(t"Clear every error report?")),
                sl.paragraph(tr(t"This permanently deletes reports that have not expired yet.")),
            ),
            key="clear-errors",
            on_confirm=self._clear,
            on_cancel=self._cancel_clear,
            confirm_label=tr(t"Delete reports"),
        )

    @override
    async def on_load(self) -> None:
        """Load the requested report or the current diagnostic window after delivery wins."""
        if self._reference is not None:
            self._detail, self._matches = await self._operations.lookup(self._reference)
            return
        await self._load_recent()

    async def _load_recent(self) -> None:
        self._reports = tuple(await self._operations.recent(limit=RECENT_LIMIT, work_lost_only=self.work_lost_only))
        self._updates = _ReportUpdates(
            self._operations,
            self._reports,
            bus=self._bus,
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
            title=tr(t"Recent errors"),
            empty=tr(t"Nothing has failed within the retention window."),
        )

    @property
    def reports(self) -> tuple[ErrorReport, ...]:
        """The reports the list offers."""
        return self._reports

    @override
    def render(self) -> sl.Document[sl.ComponentsV2Target]:
        if self.cleared_count is not None:
            count = self.cleared_count
            return sl.Document(
                (
                    sl.section(
                        sl.heading(tr(t"Errors cleared")),
                        sl.paragraph(tr(t"Deleted {count} stored error reports.")),
                    ),
                )
            )
        if self.confirming_clear:
            return sl.Document((self.boundary(self._clear_decision, key="clear-decision"),))
        if self._detail is not None:
            return sl.Document((self._render_report(self._detail, self._matches), self._detail_actions()))
        if self._browser is None:
            return sl.Document((sl.status(tr(t"Loading error reports.")),))
        assert self._updates is not None
        return sl.Document(
            (
                self.boundary(self._browser, key="browser"),
                self.boundary(self._updates, key="updates"),
                sl.action_controls(
                    sl.action_control(
                        tr(t"Show all") if self.work_lost_only else tr(t"Show work lost only"),
                        self._toggle_filter,
                        key="filter",
                    ),
                    sl.action_control(
                        tr(t"Clear all"),
                        self._confirm_clear,
                        key="clear",
                        tone=sl.Tone.DANGER,
                    )
                    if self._can_clear
                    else None,
                    self._close_action(),
                    key="error-actions",
                ),
            )
        )

    def _render_report(self, report: ErrorReport, matches: int) -> sl.LayoutNode[sl.ComponentsV2Target]:
        reference = report.reference
        traceback_text: sl.TextLike = report.traceback.strip() or tr(t"No traceback was recorded.")
        children: list[sl.LayoutNode[sl.ComponentsV2Target]] = [
            sl.primitives.Heading(tr(t"Error {reference}")),
            # Opens at the end because the failing frame is the last one.
            sl.primitives.Code(traceback_text, overflow=sl.primitives.Paginate(key="traceback", initial="end")),
        ]
        if report.log_tail:
            # The run-up to the failure: its last lines matter most, so it trims from the
            # front; the attachment carries all of it.
            children.append(sl.primitives.Heading(tr(t"Log tail"), level=3, priority=2))
            children.append(
                sl.primitives.Code(
                    "\n".join(report.log_tail), overflow=sl.primitives.Truncate(keep="tail"), priority=-8
                )
            )
        children.append(sl.fields(*_summary_fields(report, matches)))
        children.append(
            sl.download(
                tr(t"Full report"), report_asset(report), key="full-report", emphasis=sl.semantic.Emphasis.STRONG
            )
        )
        return sl.stack(*children)

    def _detail_actions(self) -> sl.semantic.ActionControls:
        return sl.action_controls(self._close_action(), key="detail-actions")

    def _close_action(self) -> sl.semantic.ActionControl:
        return sl.action_control(
            tr(t"Close"),
            self._close,
            key="close",
        )

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
            await event.source.notice(tr(t"You are no longer allowed to clear error reports."))
            return
        self.cleared_count = await self._operations.clear_all()
        self.confirming_clear = False
        event.source.finish_on_success()

    async def _cancel_clear(self, _event: sp.TransitionEvent[sp.DecisionState]) -> None:
        self.confirming_clear = False

    async def _close(self, event: sl.PressEvent) -> None:
        event.finish_on_success()


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
            tr(t"When"),
            sl.md(t"{report.occurred_at.to_stdlib()}"),
        ),
        sl.field(tr(t"Where"), sl.md(t"{report.surface} — {report.origin or '—'}")),
        sl.field(tr(t"Exception"), sl.md(t"{report.exception_type}")),
        sl.field(tr(t"Full ID"), sl.md(t"{identifier}")),
    ]
    if report.work_lost:
        entries.append(sl.field(tr(t"Work lost"), tr(t"This job was abandoned; nothing will retry it.")))
    if matches > 1:
        # The reference is a 48-bit prefix, not a key. Silently showing the newest of several
        # would have a moderator confidently reading the wrong incident.
        count = matches
        entries.append(
            sl.field(
                tr(t"Ambiguous"),
                tr(t"{count} reports share this reference; this is the newest."),
            )
        )
    return entries


def _list_line(report: ErrorReport) -> str:
    marker = ":warning: " if report.work_lost else ""
    return f"{marker}`{report.reference}` — {report.exception_type} in {report.origin or report.surface}"


def _list_description(report: ErrorReport) -> str:
    return f"{report.exception_type} · {report.origin or report.surface}"
