"""Tests for reading a stored error report from Discord."""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import override
from uuid import UUID

import anyio
import discord
import pytest
from whenever import Instant

import squid_ui_widgets as sp
from squid.bot.diagnostics_view import ErrorReportScreen, report_attachment
from squid.diagnostics.application import ErrorReportService
from squid.diagnostics.domain import ErrorReport
from squid_ui.runtime import LocalTopicBus, Resource, UIExecutionHost
from squid_ui.sources import Position
from squid_ui.testing import RecordingResponder, labels, press_event, render_tree
from squid_ui_discord import (
    V2_LIMITS as LIMITS,
)
from squid_ui_discord import MessageRoot, Owner, Private
from squid_ui_discord.testing import assert_within_limits, commit_render, interaction_harness
from tests.support.discord import make_layout_bot


@asynccontextmanager
async def running_execution_host() -> AsyncIterator[UIExecutionHost]:
    with anyio.fail_after(5):
        async with anyio.create_task_group() as tasks:
            host = UIExecutionHost()
            await tasks.start(host.run)
            try:
                yield host
            finally:
                await host.close()


def make_report(
    reference: str = "abc123",
    *,
    traceback: str = "Traceback (most recent call last):\n  File 'a.py', line 1\nValueError: boom\n",
    log_tail: tuple[str, ...] = (),
    work_lost: bool = False,
) -> ErrorReport:
    return ErrorReport(
        id=UUID(int=0),
        correlation_id=f"{reference}-full",
        reference=reference,
        occurred_at=Instant.from_utc(2026, 8, 19, 12),
        expires_at=Instant.from_utc(2026, 8, 26, 12),
        surface="app_command",
        origin="build submit",
        exception_type="ValueError",
        message="boom",
        traceback=traceback,
        log_tail=log_tail,
        work_lost=work_lost,
    )


def make_screen(
    reports: tuple[ErrorReport, ...] = (),
    *,
    report: ErrorReport | None = None,
    matches: int = 1,
    can_clear: bool = False,
) -> ErrorReportScreen:
    operations = ErrorReportRecorder(reports, report=report, matches=matches)
    return ErrorReportScreen(
        operations,
        bus=LocalTopicBus(),
        reference=report.reference if report is not None else None,
        can_clear=can_clear,
        authorize_clear=Authorizer(allowed=True) if can_clear else None,
    )


class ErrorReportRecorder(ErrorReportService):
    def __init__(
        self,
        reports: tuple[ErrorReport, ...],
        *,
        report: ErrorReport | None,
        matches: int,
    ) -> None:
        self.reports = reports
        self.report = report
        self.matches = matches
        self.recent_calls: list[tuple[int, bool]] = []
        self.clear_calls = 0

    @override
    async def lookup(self, reference: str) -> tuple[ErrorReport, int]:
        assert self.report is not None
        assert reference == self.report.reference
        return self.report, self.matches

    @override
    async def recent(self, *, limit: int = 20, work_lost_only: bool = False) -> Sequence[ErrorReport]:
        self.recent_calls.append((limit, work_lost_only))
        return self.reports

    @override
    async def clear_all(self) -> int:
        self.clear_calls += 1
        return 3


class Authorizer:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed

    async def __call__(self) -> bool:
        return self.allowed


def decision_event(responder: RecordingResponder) -> sp.TransitionEvent[sp.DecisionState]:
    return sp.TransitionEvent(
        source=press_event(responder=responder),
        action="approve",
        previous=sp.DecisionState(),
        state=sp.DecisionState("approve"),
    )


async def message_root_browser(
    browser: ErrorReportScreen, *, execution_host: UIExecutionHost
) -> tuple[MessageRoot, discord.ui.LayoutView]:
    await browser.on_load()
    browser._loaded = True
    bot = make_layout_bot(topic_bus=browser._bus)
    message_root = bot.ui.mount(browser, access=Owner(1), chrome=browser.chrome, execution_host=execution_host)
    if browser._browser is not None:
        await browser._browser.window._load()
    return message_root, commit_render(message_root)


async def open_report(message_root: MessageRoot, interaction: discord.Interaction, reference: str) -> None:
    """Use the Browser's semantic action whether the planner chose buttons or a select."""
    key = next(key for key in message_root._handlers if "error-reports.open" in key)
    values = None if key.endswith(f".{reference}") else [reference]
    await message_root.dispatch(key, interaction, values)


def _texts(view: discord.ui.LayoutView) -> list[str]:
    return [c.content for c in view.walk_children() if isinstance(c, discord.ui.TextDisplay)]


def _code_pages(message_root: MessageRoot) -> list[str]:
    """Every page of the fenced body, walked via the mount's own nav handlers."""
    pages = []
    while True:
        view = commit_render(message_root)
        pages.append(next(text for text in _texts(view) if text.startswith("```")))
        next_button = next(
            item
            for item in view.walk_children()
            if isinstance(item, discord.ui.Button) and item.custom_id and ":__cursor_next" in item.custom_id
        )
        if next_button.disabled:
            return pages
        cursor = message_root.presentation.cursor("traceback")
        message_root.presentation.move_cursor("traceback", Position(offset=cursor.position.offset + 1))


async def test_recent_list_offers_every_entry_for_opening() -> None:
    async with running_execution_host() as execution_host:
        """The audit's complaint: a listed reference could only be read, then retyped."""
        reports = (make_report("aaa111"), make_report("bbb222", work_lost=True))
        browser = make_screen(reports)
        await message_root_browser(browser, execution_host=execution_host)

        assert browser._browser is not None
        rendered_labels = labels(render_tree(browser._browser))
        assert "aaa111" in rendered_labels
        assert "bbb222" in rendered_labels


async def test_opening_a_listed_report_replaces_the_list_in_place() -> None:
    async with running_execution_host() as execution_host:
        browser = make_screen((make_report("aaa111"),))
        message_root, _ = await message_root_browser(browser, execution_host=execution_host)
        interaction = interaction_harness()

        await open_report(message_root, interaction, "aaa111")

        payload = str(interaction.response.edit_message.await_args.kwargs["view"].to_components())
        assert "Error aaa111" in payload
        assert "ValueError: boom" in payload
        assert "Back" in payload


async def test_a_long_traceback_is_readable_past_one_page() -> None:
    async with running_execution_host() as execution_host:
        """Previously the card showed a fixed 1200-character tail and nothing could reach the rest."""
        frames = "\n".join(f"  File 'module{index}.py', line {index}, in frame{index}" for index in range(300))
        browser = make_screen(report=make_report(traceback=f"Traceback:\n{frames}\nValueError: boom"))
        message_root, view = await message_root_browser(browser, execution_host=execution_host)

        # The failing frame is at the end, so that is where the report opens.
        first_shown = next(text for text in _texts(view) if text.startswith("```"))
        assert "ValueError: boom" in first_shown
        footers = [text for text in _texts(view) if text.startswith("-#")]
        assert footers
        assert "page 1 of" not in footers[0]

        message_root.presentation.move_cursor("traceback", Position())
        earliest = commit_render(message_root)
        assert "frame0" in "\n".join(_texts(earliest))


async def test_paging_controls_absent_for_a_short_traceback() -> None:
    async with running_execution_host() as execution_host:
        _, view = await message_root_browser(
            make_screen(report=make_report(traceback="one frame")), execution_host=execution_host
        )

        buttons = [item.label for item in view.walk_children() if isinstance(item, discord.ui.Button)]
        assert "Earlier" not in buttons
        assert "Later" not in buttons


async def test_paging_stops_at_both_ends() -> None:
    async with running_execution_host() as execution_host:
        frames = "\n".join("frame " + "x" * 90 for _ in range(200))
        browser = make_screen(report=make_report(traceback=frames))
        message_root, view = await message_root_browser(browser, execution_host=execution_host)

        nav = [item for item in view.walk_children() if isinstance(item, discord.ui.Button) and item.custom_id]
        later = next(item for item in nav if ":__cursor_next" in (item.custom_id or ""))
        assert later.disabled  # opened at the end
        interaction = interaction_harness()
        await message_root.dispatch("__cursor_next.traceback", interaction)
        interaction.response.defer.assert_awaited_once()  # nothing to advance to


async def test_the_log_tail_is_shown_and_keeps_its_last_lines() -> None:
    async with running_execution_host() as execution_host:
        browser = make_screen(report=make_report(log_tail=("first line", "second line")))
        _, view = await message_root_browser(browser, execution_host=execution_host)

        text = "\n".join(_texts(view))
        assert "Log tail" in text
        assert "second line" in text


async def test_every_page_fits_the_real_display_budget() -> None:
    async with running_execution_host() as execution_host:
        """The PAGE_CHARS killer: each page, chrome and footer included, fits the actual budget."""
        long_line = "x" * 9001
        browser = make_screen(report=make_report(traceback=f"{long_line}\nValueError: boom"))
        message_root, _ = await message_root_browser(browser, execution_host=execution_host)

        message_root.presentation.move_cursor("traceback", Position())
        pages = _code_pages(message_root)
        assert len(pages) > 1
        for page_index in range(len(pages)):
            message_root.presentation.move_cursor("traceback", Position(offset=page_index))
            view = commit_render(message_root)
            assert_within_limits(view)
            assert sum(len(text) for text in _texts(view)) <= LIMITS.total_text
        assert "ValueError: boom" in pages[-1]
        # No content was lost to the split.
        joined = "".join(page.removeprefix("```\n").removesuffix("\n```") for page in pages).replace("\n", "")
        assert long_line in joined


async def test_choosing_a_report_attaches_its_full_text() -> None:
    async with running_execution_host() as execution_host:
        browser = make_screen((make_report("aaa111"),))
        message_root, _ = await message_root_browser(browser, execution_host=execution_host)
        interaction = interaction_harness()

        await open_report(message_root, interaction, "aaa111")

        attachments = interaction.response.edit_message.await_args.kwargs["attachments"]
        assert [file.filename for file in attachments] == ["error-aaa111.txt"]


async def test_going_back_removes_the_attachment() -> None:
    async with running_execution_host() as execution_host:
        browser = make_screen((make_report("aaa111"),))
        message_root, _ = await message_root_browser(browser, execution_host=execution_host)
        opened = interaction_harness()
        await open_report(message_root, opened, "aaa111")
        interaction = interaction_harness()
        back_key = next(key for key in message_root._handlers if key.endswith("error-reports.back"))

        await message_root.dispatch(back_key, interaction)

        assert interaction.response.edit_message.await_args.kwargs["attachments"] == []


def test_errors_are_a_private_session() -> None:
    assert ErrorReportScreen.session is not None
    assert ErrorReportScreen.session.name == "errors"
    assert ErrorReportScreen.timeout == 300
    assert isinstance(ErrorReportScreen.audience, Private)


async def test_filtering_reloads_from_the_service() -> None:
    screen = make_screen((make_report(),))
    await screen.on_load()
    responder = RecordingResponder()

    await screen._toggle_filter(press_event(responder=responder))

    assert screen.work_lost_only is True
    assert isinstance(screen._operations, ErrorReportRecorder)
    assert screen._operations.recent_calls[-1] == (100, True)


async def test_clear_finishes_once_with_a_terminal_count() -> None:
    screen = make_screen((make_report(),), can_clear=True)
    await screen.on_load()
    responder = RecordingResponder()

    await screen._clear(decision_event(responder))

    assert screen.cleared_count == 3
    assert responder.finished is True


async def test_clear_rechecks_permission_before_deleting() -> None:
    screen = make_screen((make_report(),), can_clear=True)
    await screen.on_load()
    screen._authorize_clear = Authorizer(allowed=False)
    responder = RecordingResponder()

    await screen._clear(decision_event(responder))

    assert isinstance(screen._operations, ErrorReportRecorder)
    assert screen._operations.clear_calls == 0
    assert len(responder.notices) == 1
    assert responder.finished is False


async def test_a_fence_inside_a_traceback_cannot_close_the_card_fence() -> None:
    async with running_execution_host() as execution_host:
        _, view = await message_root_browser(
            make_screen(report=make_report(traceback="ValueError: ```not markdown```")), execution_host=execution_host
        )

        fenced = next(text for text in _texts(view) if "not markdown" in text)
        assert "```not" not in fenced


async def test_the_attachment_bundles_traceback_and_log_tail() -> None:
    report = make_report(log_tail=("line a", "line b"))
    payload = report_attachment(report).fp.read().decode()
    assert "ValueError: boom" in payload
    assert "line b" in payload


@pytest.mark.parametrize("hide", [False, True])
async def test_live_updates_preserve_the_open_report_and_stop_with_the_screen(
    monkeypatch: pytest.MonkeyPatch, *, hide: bool
) -> None:
    import asyncio

    from anyio.abc import TaskStatus

    from squid.bot.diagnostics_refresh import ErrorReportRefresh

    stopped = anyio.Event()

    class ObservedRefresh(ErrorReportRefresh):
        def __init__(self, changes: Resource[bool]) -> None:
            super().__init__(changes, interval=0.001)

        @override
        async def run(self, *, task_status: TaskStatus[None]) -> None:
            try:
                await super().run(task_status=task_status)
            finally:
                stopped.set()

    monkeypatch.setattr("squid.bot.diagnostics_view.ErrorReportRefresh", ObservedRefresh)
    async with running_execution_host() as execution_host:
        screen = make_screen((make_report("aaa111"),))
        root, _ = await message_root_browser(screen, execution_host=execution_host)
        await root.runtime.wait_services_ready()
        await open_report(root, interaction_harness(), "aaa111")
        original_browser = screen._browser
        assert screen._updates is not None
        assert isinstance(screen._operations, ErrorReportRecorder)
        screen._operations.reports = (make_report("bbb222"), make_report("aaa111"))
        while not screen._updates.changed.pending:
            await asyncio.sleep(0)
        assert screen._updates.changed.pending
        assert screen._updates._shown != screen._operations.reports
        await screen._updates.changed._load()
        assert screen._updates.changed.value is True
        view = commit_render(root)
        text = "\n".join(_texts(view))
        assert "report list has changed" in text
        assert "Error aaa111" in text
        assert screen._browser is original_browser
        assert screen.reports == (make_report("aaa111"),)
        if hide:
            screen.confirming_clear = True
            commit_render(root)
            await root.runtime.wait_services()
            assert stopped.is_set()
            assert not root.finished
        await root.finish(disable=False)
        assert stopped.is_set()


async def test_manual_refresh_replaces_the_list_and_preserves_filter() -> None:
    screen = make_screen((make_report("aaa111"),))
    await screen.on_load()
    responder = RecordingResponder()
    await screen._toggle_filter(press_event(responder=responder))
    assert isinstance(screen._operations, ErrorReportRecorder)
    screen._operations.reports = (make_report("bbb222", work_lost=True),)
    old_updates = screen._updates
    await screen._refresh_reports(press_event(responder=responder))
    assert screen.reports == screen._operations.reports
    assert screen._operations.recent_calls[-1] == (100, True)
    assert screen._updates is not old_updates
    assert screen._updates is not None
    assert screen._updates.changed.value is False


async def test_update_check_failure_keeps_manual_refresh_available() -> None:
    from unittest.mock import AsyncMock

    screen = make_screen((make_report(),))
    await screen.on_load()
    assert isinstance(screen._operations, ErrorReportRecorder)
    screen._operations.recent = AsyncMock(side_effect=RuntimeError("database unavailable"))
    updates = screen._updates
    assert updates is not None
    await updates.changed.reload()
    rendered = render_tree(updates)
    assert "Refresh reports" in labels(rendered)
    assert screen.reports == (make_report(),)


async def test_refresh_button_commits_a_new_report_window() -> None:
    async with running_execution_host() as execution_host:
        screen = make_screen((make_report("aaa111"),))
        root, _ = await message_root_browser(screen, execution_host=execution_host)
        old_updates = screen._updates
        assert isinstance(screen._operations, ErrorReportRecorder)
        screen._operations.reports = (make_report("bbb222"),)
        key = next(key for key in root._handlers if key.endswith("refresh"))
        await root.dispatch(key, interaction_harness())
        assert screen.reports == (make_report("bbb222"),)
        assert screen._updates is not old_updates
        assert screen._updates in root.runtime.components.values()
        assert old_updates not in root.runtime.components.values()
        await root.finish(disable=False)


async def test_report_monitor_cancels_initial_check_and_remounts_with_a_new_run() -> None:
    import asyncio
    from unittest.mock import AsyncMock

    from squid_ui.runtime import ServiceRunStatus

    entered, release = asyncio.Event(), asyncio.Event()
    screen = make_screen((make_report(),))
    assert isinstance(screen._operations, ErrorReportRecorder)
    original = screen._operations.recent
    calls = 0

    async def recent(*, limit: int = 20, work_lost_only: bool = False) -> Sequence[ErrorReport]:
        nonlocal calls
        calls += 1
        if calls > 1:
            entered.set()
            await release.wait()
        return await original(limit=limit, work_lost_only=work_lost_only)

    screen._operations.recent = AsyncMock(side_effect=recent)
    async with running_execution_host() as host:
        root, _ = await message_root_browser(screen, execution_host=host)
        updates = screen._updates
        assert updates is not None
        first = updates.monitor.current_run
        assert first is not None
        await entered.wait()
        assert first.status is ServiceRunStatus.STARTING
        assert screen.reports == (make_report(),)
        screen.confirming_clear = True
        commit_render(root)
        await root.runtime.wait_services()
        assert first.status is ServiceRunStatus.CANCELLED
        release.set()
        screen.confirming_clear = False
        commit_render(root)
        await root.runtime.wait_services_ready()
        assert updates.monitor.current_run is not first
        assert first.status is ServiceRunStatus.CANCELLED
        assert screen._updates is updates
        await root.finish(disable=False)


async def test_initial_monitor_failure_keeps_report_and_refresh_controls_live() -> None:
    from unittest.mock import AsyncMock

    screen = make_screen((make_report(),))
    async with running_execution_host() as host:
        root, _ = await message_root_browser(screen, execution_host=host)
        assert isinstance(screen._operations, ErrorReportRecorder)
        screen._operations.recent = AsyncMock(side_effect=RuntimeError("database unavailable"))
        updates = screen._updates
        assert updates is not None
        run = updates.monitor.current_run
        assert run is not None
        await run.wait_ready()
        view = commit_render(root)
        assert "Could not check for updates" in "\n".join(_texts(view))
        assert any(key.endswith("refresh") for key in root._handlers)
        assert screen.reports == (make_report(),)
        assert not root.finished
        await root.finish(disable=False)
        await run.wait()
