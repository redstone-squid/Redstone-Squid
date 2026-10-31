"""Interactive Components V2 rendering for stored error reports."""

import io
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import override

import discord

from squid.bot.errors import ExpiringLayoutView
from squid.bot.i18n import t
from squid.bot.utils.components import CardField, card_container, edit_interaction_layout, no_mentions
from squid.core.i18n import _
from squid.diagnostics.domain import ErrorReport

PAGE_CHARS = 1400
"""How much of a report body goes on one page.

A card shares a 4000-character display budget with its title, fields and footer, and the body is
fenced as code on top of that. 1400 leaves room for a report whose exception name and origin are
both long.
"""

SESSION_SECONDS = 300


@dataclass(frozen=True, slots=True)
class ReportPage:
    """One screenful of a report body, labelled with the section it came from."""

    section: str
    body: str
    number: int
    total: int


class ErrorReportView(ExpiringLayoutView):
    """A list of recent reports, each openable in place and readable to its last line.

    The view holds the reports it was constructed with rather than a service: `recent` already
    fetches every row the select can offer, so opening one is a re-render, not a round trip.
    """

    def __init__(
        self,
        *,
        author_id: int,
        locale: str | None = None,
        reports: Sequence[ErrorReport] = (),
        report: ErrorReport | None = None,
        matches: int = 1,
    ) -> None:
        super().__init__(timeout=SESSION_SECONDS)
        self._author_id = author_id
        self.locale = locale
        self._reports = tuple(reports)
        self._detail: ErrorReport | None = None
        self._matches = 1
        self._pages: tuple[ReportPage, ...] = ()
        self._page = 0
        if report is not None:
            self.open(report, matches=matches)
        else:
            self.render_list()

    @override
    async def interaction_check(self, interaction: discord.Interaction[discord.Client], /) -> bool:
        if interaction.user.id == self._author_id:
            return True
        await interaction.response.send_message(
            t(self.locale, _("These error controls belong to someone else.")),
            ephemeral=True,
            allowed_mentions=no_mentions(),
        )
        return False

    @property
    def reports(self) -> tuple[ErrorReport, ...]:
        """The reports the list offers."""
        return self._reports

    @property
    def page(self) -> ReportPage | None:
        """The body page currently displayed, or None while the list is showing."""
        return self._pages[self._page] if self._detail is not None else None

    def render_list(self) -> None:
        """Show the recent reports, with a select for opening one."""
        self._detail = None
        self.clear_items()
        lines = [_list_line(report) for report in self._reports]
        body = "\n".join(lines) or t(self.locale, _("Nothing has failed within the retention window."))
        self.add_item(card_container(t(self.locale, _("Recent errors")), body))
        if self._reports:
            self.add_item(discord.ui.ActionRow(ErrorReportSelect(self)))
        self.add_item(discord.ui.ActionRow(ErrorCloseButton(self)))

    def open(self, report: ErrorReport, *, matches: int = 1) -> None:
        """Show one report, opened at the end of its traceback."""
        self._detail = report
        self._matches = matches
        self._pages, self._page = _report_pages(report, self.locale)
        self.render_detail()

    def render_detail(self) -> None:
        """Render the open report at its current page."""
        report = self._detail
        assert report is not None, "render_detail runs only after open() set a report"
        page = self._pages[self._page]
        # A traceback can quote a message carrying a fence of its own; the zero-width space
        # keeps that one from closing ours and spilling the rest of the page into markdown.
        body = page.body.replace("```", "`\u200b``")
        self.clear_items()
        self.add_item(
            card_container(
                t(self.locale, _("Error {reference}"), reference=report.reference),
                f"```\n{body}\n```",
                fields=_summary_fields(report, self._matches, self.locale),
                footer=t(
                    self.locale,
                    _("{section} — page {page} of {pages} · the attachment has the whole report"),
                    section=page.section,
                    page=page.number,
                    pages=page.total,
                ),
            )
        )
        controls = discord.ui.ActionRow()
        controls.add_item(ErrorPreviousButton(self))
        controls.add_item(ErrorNextButton(self))
        if self._reports:
            controls.add_item(ErrorBackButton(self))
        controls.add_item(ErrorCloseButton(self))
        self.add_item(controls)

    @property
    def can_go_back(self) -> bool:
        """Whether an earlier body page exists."""
        return self._page > 0

    @property
    def can_go_forward(self) -> bool:
        """Whether a later body page exists."""
        return self._page + 1 < len(self._pages)

    def previous_page(self) -> None:
        """Move one page towards the start of the report."""
        if self.can_go_back:
            self._page -= 1
            self.render_detail()

    def next_page(self) -> None:
        """Move one page towards the end of the report."""
        if self.can_go_forward:
            self._page += 1
            self.render_detail()

    def report_at(self, index: int) -> ErrorReport:
        """A report on the list."""
        return self._reports[index]

    def disable_controls(self) -> None:
        """Disable every interactive component."""
        for child in self.walk_children():
            if isinstance(child, discord.ui.Button | discord.ui.Select):
                child.disabled = True
        self.stop()


class ErrorReportSelect(discord.ui.Select[ErrorReportView]):
    """Open a listed report without retyping its reference."""

    def __init__(self, view: ErrorReportView) -> None:
        options = [
            discord.SelectOption(
                label=report.reference[:100],
                value=str(index),
                description=_list_description(report)[:100],
                emoji="⚠️" if report.work_lost else None,
            )
            for index, report in enumerate(view.reports)
        ]
        super().__init__(placeholder=t(view.locale, _("Choose an error to open")), options=options)
        self._report_view = view

    @override
    async def callback(self, interaction: discord.Interaction[discord.Client]) -> None:
        report = self._report_view.report_at(int(self.values[0]))
        self._report_view.open(report)
        await edit_interaction_layout(interaction, self._report_view, attachments=[report_attachment(report)])


class ErrorPreviousButton(discord.ui.Button[ErrorReportView]):
    def __init__(self, view: ErrorReportView) -> None:
        super().__init__(label=t(view.locale, _("Earlier")), disabled=not view.can_go_back)
        self._report_view = view

    @override
    async def callback(self, interaction: discord.Interaction[discord.Client]) -> None:
        self._report_view.previous_page()
        await edit_interaction_layout(interaction, self._report_view)


class ErrorNextButton(discord.ui.Button[ErrorReportView]):
    def __init__(self, view: ErrorReportView) -> None:
        super().__init__(label=t(view.locale, _("Later")), disabled=not view.can_go_forward)
        self._report_view = view

    @override
    async def callback(self, interaction: discord.Interaction[discord.Client]) -> None:
        self._report_view.next_page()
        await edit_interaction_layout(interaction, self._report_view)


class ErrorBackButton(discord.ui.Button[ErrorReportView]):
    def __init__(self, view: ErrorReportView) -> None:
        super().__init__(label=t(view.locale, _("Back")))
        self._report_view = view

    @override
    async def callback(self, interaction: discord.Interaction[discord.Client]) -> None:
        self._report_view.render_list()
        # The list is not about any one report, so its attachment goes with it.
        await edit_interaction_layout(interaction, self._report_view, attachments=[])


class ErrorCloseButton(discord.ui.Button[ErrorReportView]):
    def __init__(self, view: ErrorReportView) -> None:
        super().__init__(label=t(view.locale, _("Close")), style=discord.ButtonStyle.secondary)
        self._report_view = view

    @override
    async def callback(self, interaction: discord.Interaction[discord.Client]) -> None:
        self._report_view.disable_controls()
        await edit_interaction_layout(interaction, self._report_view)


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


def _summary_fields(report: ErrorReport, matches: int, locale: str | None) -> list[CardField]:
    fields = [
        CardField(t(locale, _("When")), f"<t:{report.occurred_at.timestamp()}:f>"),
        CardField(t(locale, _("Where")), f"{report.surface} — {report.origin or '—'}"),
        CardField(t(locale, _("Exception")), report.exception_type),
        CardField(t(locale, _("Full ID")), f"`{report.correlation_id}`"),
    ]
    if report.work_lost:
        fields.append(
            CardField(
                t(locale, _("Work lost")),
                t(locale, _("This job was abandoned; nothing will retry it.")),
            )
        )
    if matches > 1:
        # The reference is a 48-bit prefix, not a key. Silently showing the newest of several
        # would have a moderator confidently reading the wrong incident.
        fields.append(
            CardField(
                t(locale, _("Ambiguous")),
                t(locale, _("{count} reports share this reference; this is the newest."), count=matches),
            )
        )
    return fields


def _list_line(report: ErrorReport) -> str:
    marker = ":warning: " if report.work_lost else ""
    return f"{marker}`{report.reference}` — {report.exception_type} in {report.origin or report.surface}"


def _list_description(report: ErrorReport) -> str:
    return f"{report.exception_type} · {report.origin or report.surface}"


def _report_pages(report: ErrorReport, locale: str | None) -> tuple[tuple[ReportPage, ...], int]:
    """Every page of a report body, and the page to open on.

    Opening lands on the last traceback page because the failing frame is at its end, which is
    what the previous tail-only preview showed. The log tail follows for anyone who wants the
    run-up to it, and was previously readable only by downloading the attachment.
    """
    traceback_label = t(locale, _("Traceback"))
    traceback_text = report.traceback.strip() or t(locale, _("No traceback was recorded."))
    pages = list(_section_pages(traceback_label, traceback_text))
    initial = len(pages) - 1
    if report.log_tail:
        pages.extend(_section_pages(t(locale, _("Log tail")), "\n".join(report.log_tail)))
    return tuple(pages), initial


def _section_pages(section: str, text: str) -> Iterator[ReportPage]:
    chunks = _paginate(text, PAGE_CHARS)
    for number, chunk in enumerate(chunks, start=1):
        yield ReportPage(section=section, body=chunk, number=number, total=len(chunks))


def _paginate(text: str, limit: int) -> tuple[str, ...]:
    """Split text into display-sized chunks without cutting a line in half."""
    pages: list[str] = []
    lines: list[str] = []
    length = 0
    for line in text.split("\n"):
        for chunk in _hard_split(line, limit):
            if lines and length + 1 + len(chunk) > limit:
                pages.append("\n".join(lines))
                lines, length = [], 0
            length += len(chunk) + (1 if lines else 0)
            lines.append(chunk)
    pages.append("\n".join(lines))
    return tuple(pages)


def _hard_split(line: str, limit: int) -> Iterator[str]:
    """Yield a line in budget-sized pieces; one source line can outgrow a whole page."""
    if len(line) <= limit:
        yield line
        return
    for start in range(0, len(line), limit):
        yield line[start : start + limit]
