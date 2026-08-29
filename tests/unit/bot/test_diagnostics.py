"""Tests for reading a stored error report from Discord."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import UUID

import discord
from whenever import Instant

from squid.bot.diagnostics import Diagnostics
from squid.bot.diagnostics_view import ErrorReportOperations, ErrorReportScreen, report_attachment
from squid.diagnostics.domain import ErrorReport
from squid_ui.sources import Position
from squid_ui.testing import labels, render_tree
from squid_ui_discord import (
    V2_LIMITS as LIMITS,
)
from squid_ui_discord import MessageRoot, Owner, Private
from squid_ui_discord.testing import assert_within_limits, commit_render, interaction_harness
from tests.helpers.discord import make_layout_bot


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
    operations = SimpleNamespace(
        lookup=AsyncMock(return_value=(report, matches)),
        recent=AsyncMock(return_value=reports),
        clear_all=AsyncMock(return_value=3),
    )
    return ErrorReportScreen(
        cast(ErrorReportOperations, operations),
        reference=report.reference if report is not None else None,
        can_clear=can_clear,
        authorize_clear=AsyncMock(return_value=True) if can_clear else None,
    )


async def message_root_browser(browser: ErrorReportScreen) -> tuple[MessageRoot, discord.ui.LayoutView]:
    await browser.on_load()
    bot = make_layout_bot()
    message_root = bot.client_runtime.mount(browser, access=Owner(1), chrome=browser.chrome())
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
    """The audit's complaint: a listed reference could only be read, then retyped."""
    reports = (make_report("aaa111"), make_report("bbb222", work_lost=True))
    browser = make_screen(reports)
    await message_root_browser(browser)

    assert browser._browser is not None
    rendered_labels = labels(render_tree(browser._browser))
    assert "aaa111" in rendered_labels
    assert "bbb222" in rendered_labels


async def test_opening_a_listed_report_replaces_the_list_in_place() -> None:
    browser = make_screen((make_report("aaa111"),))
    message_root, _ = await message_root_browser(browser)
    interaction = interaction_harness()

    await open_report(message_root, interaction, "aaa111")

    payload = str(interaction.response.edit_message.await_args.kwargs["view"].to_components())
    assert "Error aaa111" in payload
    assert "ValueError: boom" in payload
    assert "Back" in payload


async def test_a_long_traceback_is_readable_past_one_page() -> None:
    """Previously the card showed a fixed 1200-character tail and nothing could reach the rest."""
    frames = "\n".join(f"  File 'module{index}.py', line {index}, in frame{index}" for index in range(300))
    browser = make_screen(report=make_report(traceback=f"Traceback:\n{frames}\nValueError: boom"))
    message_root, view = await message_root_browser(browser)

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
    _, view = await message_root_browser(make_screen(report=make_report(traceback="one frame")))

    buttons = [item.label for item in view.walk_children() if isinstance(item, discord.ui.Button)]
    assert "Earlier" not in buttons
    assert "Later" not in buttons


async def test_paging_stops_at_both_ends() -> None:
    frames = "\n".join("frame " + "x" * 90 for _ in range(200))
    browser = make_screen(report=make_report(traceback=frames))
    message_root, view = await message_root_browser(browser)

    nav = [item for item in view.walk_children() if isinstance(item, discord.ui.Button) and item.custom_id]
    later = next(item for item in nav if ":__cursor_next" in (item.custom_id or ""))
    assert later.disabled  # opened at the end
    interaction = interaction_harness()
    await message_root.dispatch("__cursor_next.traceback", interaction)
    interaction.response.defer.assert_awaited_once()  # nothing to advance to


async def test_the_log_tail_is_shown_and_keeps_its_last_lines() -> None:
    browser = make_screen(report=make_report(log_tail=("first line", "second line")))
    _, view = await message_root_browser(browser)

    text = "\n".join(_texts(view))
    assert "Log tail" in text
    assert "second line" in text


async def test_every_page_fits_the_real_display_budget() -> None:
    """The PAGE_CHARS killer: each page, chrome and footer included, fits the actual budget."""
    long_line = "x" * 9001
    browser = make_screen(report=make_report(traceback=f"{long_line}\nValueError: boom"))
    message_root, _ = await message_root_browser(browser)

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
    browser = make_screen((make_report("aaa111"),))
    message_root, _ = await message_root_browser(browser)
    interaction = interaction_harness()

    await open_report(message_root, interaction, "aaa111")

    attachments = interaction.response.edit_message.await_args.kwargs["attachments"]
    assert [file.filename for file in attachments] == ["error-aaa111.txt"]


async def test_going_back_removes_the_attachment() -> None:
    browser = make_screen((make_report("aaa111"),))
    message_root, _ = await message_root_browser(browser)
    opened = interaction_harness()
    await open_report(message_root, opened, "aaa111")
    interaction = interaction_harness()
    back_key = next(key for key in message_root._handlers if key.endswith("error-reports.back"))

    await message_root.dispatch(back_key, interaction)

    assert interaction.response.edit_message.await_args.kwargs["attachments"] == []


def test_errors_are_one_app_only_command_and_one_private_session() -> None:
    diagnostics = cast(Any, Diagnostics)
    assert [command.qualified_name for command in diagnostics.__cog_app_commands__] == ["errors"]
    assert not diagnostics.__cog_commands__
    assert ErrorReportScreen.session_name == "errors"
    assert ErrorReportScreen.timeout == 300
    assert isinstance(ErrorReportScreen.visibility, Private)


async def test_filtering_reloads_from_the_service() -> None:
    screen = make_screen((make_report(),))
    await screen.on_load()
    event = SimpleNamespace(acknowledge=AsyncMock())

    await screen._toggle_filter(cast(Any, event))

    assert screen.work_lost_only is True
    cast(Any, screen._operations).recent.assert_awaited_with(limit=100, work_lost_only=True)


async def test_clear_finishes_once_with_a_terminal_count() -> None:
    screen = make_screen((make_report(),), can_clear=True)
    await screen.on_load()
    source = SimpleNamespace(finish=AsyncMock())

    await screen._clear(cast(Any, SimpleNamespace(source=source)))

    assert screen.cleared_count == 3
    source.finish.assert_awaited_once()


async def test_clear_rechecks_permission_before_deleting() -> None:
    screen = make_screen((make_report(),), can_clear=True)
    await screen.on_load()
    screen._authorize_clear = AsyncMock(return_value=False)
    source = SimpleNamespace(finish=AsyncMock(), notice=AsyncMock())

    await screen._clear(cast(Any, SimpleNamespace(source=source)))

    cast(Any, screen._operations).clear_all.assert_not_awaited()
    source.notice.assert_awaited_once()
    source.finish.assert_not_awaited()


async def test_a_fence_inside_a_traceback_cannot_close_the_card_fence() -> None:
    _, view = await message_root_browser(make_screen(report=make_report(traceback="ValueError: ```not markdown```")))

    fenced = next(text for text in _texts(view) if "not markdown" in text)
    assert "```not" not in fenced


async def test_the_attachment_bundles_traceback_and_log_tail() -> None:
    report = make_report(log_tail=("line a", "line b"))
    payload = report_attachment(report).fp.read().decode()
    assert "ValueError: boom" in payload
    assert "line b" in payload
