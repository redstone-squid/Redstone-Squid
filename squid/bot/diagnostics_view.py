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
from squid.bot.i18n import t
from squid.bot.ui import chrome_for
from squid.core.i18n import _
from squid.diagnostics.domain import ErrorReport

SESSION_SECONDS = 300


class ErrorReportBrowser(sl.Component):
    """A list of recent reports, each openable in place and readable to its last line.

    Holds the reports it was constructed with rather than a service: `recent` already fetches
    every row the select can offer, so opening one is a re-render, not a round trip.
    """

    detail: ErrorReport | None = sl.state(None)
    matches: int = sl.state(1)

    def __init__(
        self,
        reports: Sequence[ErrorReport] = (),
        *,
        locale: str | None = None,
        report: ErrorReport | None = None,
        matches: int = 1,
    ) -> None:
        self._reports = tuple(reports)
        self.locale = locale
        if report is not None:
            self.detail = report
            self.matches = matches

    @property
    def reports(self) -> tuple[ErrorReport, ...]:
        """The reports the list offers."""
        return self._reports

    def chrome(self) -> sl.Chrome:
        """This browser's chrome: temporal paging labels and a footer that names the attachment."""
        return dataclasses.replace(
            chrome_for(self.locale),
            not_yours=t(self.locale, _("These error controls belong to someone else.")),
            previous=t(self.locale, _("Earlier")),
            next=t(self.locale, _("Later")),
            page_footer=lambda page, pages: t(
                self.locale,
                _("{section} — page {page} of {pages} · the attachment has the whole report"),
                section=t(self.locale, _("Traceback")),
                page=page,
                pages=pages,
            ),
        )

    def render(self) -> Sequence[sl.Node]:
        return self._render_detail() if self.detail is not None else self._render_list()

    def _render_list(self) -> Sequence[sl.Node]:
        entries = tuple(_list_line(report) for report in self._reports)
        body = "\n".join(entries) or t(self.locale, _("Nothing has failed within the retention window."))
        nodes: list[sl.Node] = [sl.card(t(self.locale, _("Recent errors")), body)]
        if self._reports:
            nodes.append(
                sl.SelectMenu(
                    options=tuple(
                        sl.Option(
                            label=report.reference,
                            value=str(index),
                            description=_list_description(report),
                        )
                        for index, report in enumerate(self._reports)
                    ),
                    on_select=self._open,
                    key="open",
                    placeholder=t(self.locale, _("Choose an error to open")),
                )
            )
        nodes.append(sl.Row((self._close_button(),)))
        return nodes

    def _render_detail(self) -> Sequence[sl.Node]:
        report = self.detail
        assert report is not None
        traceback_text = report.traceback.strip() or t(self.locale, _("No traceback was recorded."))
        children: list[sl.Node] = [
            sl.Heading(t(self.locale, _("Error {reference}"), reference=report.reference)),
            # Opens at the end because the failing frame is the last one.
            sl.Code(traceback_text, overflow=sl.Paginate(initial="end")),
        ]
        if report.log_tail:
            # The run-up to the failure: its last lines matter most, so it trims from the
            # front; the attachment carries all of it.
            children.append(sl.Lines((f"**{t(self.locale, _('Log tail'))}**",), priority=2))
            children.append(sl.Code("\n".join(report.log_tail), overflow=sl.Truncate(keep="tail"), priority=-8))
        children.append(sl.Lines(tuple(_summary_entries(report, self.matches, self.locale)), priority=5))
        controls: list[sl.Button] = []
        if self._reports:
            controls.append(sl.Button(label=t(self.locale, _("Back")), on_click=self._back, key="back"))
        controls.append(self._close_button())
        return [sl.Panel(children=tuple(children)), sl.Row(tuple(controls))]

    def _close_button(self) -> sl.Button:
        return sl.Button(
            label=t(self.locale, _("Close")),
            on_click=self._close,
            key="close",
            style=discord.ButtonStyle.secondary,
        )

    async def _open(self, interaction: discord.Interaction, values: list[str]) -> None:
        report = self._reports[int(values[0])]
        self.detail = report
        self.matches = 1
        self.mount.reset_page()
        self.mount.set_attachments([report_attachment(report)])

    async def _back(self, interaction: discord.Interaction) -> None:
        self.detail = None
        self.mount.reset_page()
        # The list is not about any one report, so its attachment goes with it.
        self.mount.set_attachments([])

    async def _close(self, interaction: discord.Interaction) -> None:
        await self.mount.finish_via(interaction)


def report_attachment(report: ErrorReport) -> discord.File:
    """Bundle the traceback and the log tail, for reading outside Discord."""
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
    return discord.File(io.BytesIO("\n".join(lines).encode()), filename=f"error-{report.reference}.txt")


def _summary_entries(report: ErrorReport, matches: int, locale: str | None) -> list[str]:
    entries = [
        f"**{t(locale, _('When'))}**\n<t:{report.occurred_at.timestamp()}:f>",
        f"**{t(locale, _('Where'))}**\n{report.surface} — {report.origin or '—'}",
        f"**{t(locale, _('Exception'))}**\n{report.exception_type}",
        f"**{t(locale, _('Full ID'))}**\n`{report.correlation_id}`",
    ]
    if report.work_lost:
        entries.append(
            f"**{t(locale, _('Work lost'))}**\n{t(locale, _('This job was abandoned; nothing will retry it.'))}"
        )
    if matches > 1:
        # The reference is a 48-bit prefix, not a key. Silently showing the newest of several
        # would have a moderator confidently reading the wrong incident.
        entries.append(
            f"**{t(locale, _('Ambiguous'))}**\n"
            + t(locale, _("{count} reports share this reference; this is the newest."), count=matches)
        )
    return entries


def _list_line(report: ErrorReport) -> str:
    marker = ":warning: " if report.work_lost else ""
    return f"{marker}`{report.reference}` — {report.exception_type} in {report.origin or report.surface}"


def _list_description(report: ErrorReport) -> str:
    return f"{report.exception_type} · {report.origin or report.surface}"
