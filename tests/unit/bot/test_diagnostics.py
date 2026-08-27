"""Tests for reading a stored error report from Discord."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import UUID

import discord
import pytest
from discord.ext.commands import Context
from whenever import Instant

import squid.bot.app
from squid.bot.diagnostics import Diagnostics
from squid.bot.diagnostics_view import ErrorReportBrowser, report_attachment
from squid.diagnostics.domain import ErrorReport
from squid_ui.sources import Position
from squid_ui_discord import (
    V2_LIMITS as LIMITS,
)
from squid_ui_discord import MessageRoot, Owner
from squid_ui_discord.message_root import MountedView
from squid_ui_discord.testing import assert_within_limits, commit_render, fake_interaction
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


def make_context(
    *,
    slash: bool = False,
    in_guild: bool = True,
    dm_raises: Exception | None = None,
    bot: Any = None,
) -> Any:
    """A context stub exposing only what the cog's delivery path touches."""
    author_send = AsyncMock(side_effect=dm_raises, return_value=AsyncMock(spec=discord.Message))
    interaction = SimpleNamespace(
        guild_locale=None,
        locale="en-US",
        expires_at=None,
        is_expired=lambda: False,
        response=SimpleNamespace(is_done=lambda: False),
    )
    return SimpleNamespace(
        bot=bot if bot is not None else make_layout_bot(),
        interaction=interaction if slash else None,
        guild=SimpleNamespace(id=5, preferred_locale="en-US") if in_guild else None,
        author=SimpleNamespace(id=1, send=author_send),
        send=AsyncMock(return_value=AsyncMock(spec=discord.Message)),
    )


def make_cog(*, report: ErrorReport | None = None, reports: tuple[ErrorReport, ...] = ()) -> Diagnostics[Any]:
    error_reports = SimpleNamespace(
        lookup=AsyncMock(return_value=(report, 1)),
        recent=AsyncMock(return_value=reports),
        clear_all=AsyncMock(return_value=3),
    )
    settings = SimpleNamespace(get_locale=AsyncMock(return_value=None))
    bot = make_layout_bot(services=SimpleNamespace(error_reports=error_reports, settings=settings))
    return Diagnostics(cast("squid.bot.app.RedstoneSquid", bot))


def message_root_browser(browser: ErrorReportBrowser) -> tuple[MessageRoot, discord.ui.LayoutView]:
    bot = make_layout_bot()
    message_root = bot.client_runtime.mount(browser, access=Owner(1), chrome=browser.chrome())
    return message_root, commit_render(message_root)


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
    _, view = message_root_browser(ErrorReportBrowser(reports))

    payload = view.to_components()
    select = payload[1]["components"][0]
    assert [option["label"] for option in select["options"]] == ["aaa111", "bbb222"]
    assert "aaa111" in str(payload[0])


async def test_opening_a_listed_report_replaces_the_list_in_place() -> None:
    browser = ErrorReportBrowser((make_report("aaa111"),))
    message_root, _ = message_root_browser(browser)
    interaction = fake_interaction()

    await message_root.dispatch("open", interaction, ["0"])

    payload = str(interaction.response.edit_message.await_args.kwargs["view"].to_components())
    assert "Error aaa111" in payload
    assert "ValueError: boom" in payload
    assert "Back" in payload


async def test_a_long_traceback_is_readable_past_one_page() -> None:
    """Previously the card showed a fixed 1200-character tail and nothing could reach the rest."""
    frames = "\n".join(f"  File 'module{index}.py', line {index}, in frame{index}" for index in range(300))
    browser = ErrorReportBrowser(report=make_report(traceback=f"Traceback:\n{frames}\nValueError: boom"))
    message_root, view = message_root_browser(browser)

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
    _, view = message_root_browser(ErrorReportBrowser(report=make_report(traceback="one frame")))

    buttons = [item.label for item in view.walk_children() if isinstance(item, discord.ui.Button)]
    assert "Earlier" not in buttons
    assert "Later" not in buttons


async def test_paging_stops_at_both_ends() -> None:
    frames = "\n".join("frame " + "x" * 90 for _ in range(200))
    browser = ErrorReportBrowser(report=make_report(traceback=frames))
    message_root, view = message_root_browser(browser)

    nav = [item for item in view.walk_children() if isinstance(item, discord.ui.Button) and item.custom_id]
    later = next(item for item in nav if ":__cursor_next" in (item.custom_id or ""))
    assert later.disabled  # opened at the end
    interaction = fake_interaction()
    await message_root.dispatch("__cursor_next.traceback", interaction)
    interaction.response.defer.assert_awaited_once()  # nothing to advance to


async def test_the_log_tail_is_shown_and_keeps_its_last_lines() -> None:
    browser = ErrorReportBrowser(report=make_report(log_tail=("first line", "second line")))
    _, view = message_root_browser(browser)

    text = "\n".join(_texts(view))
    assert "Log tail" in text
    assert "second line" in text


async def test_every_page_fits_the_real_display_budget() -> None:
    """The PAGE_CHARS killer: each page, chrome and footer included, fits the actual budget."""
    long_line = "x" * 9001
    browser = ErrorReportBrowser(report=make_report(traceback=f"{long_line}\nValueError: boom"))
    message_root, _ = message_root_browser(browser)

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
    browser = ErrorReportBrowser((make_report("aaa111"),))
    message_root, _ = message_root_browser(browser)
    interaction = fake_interaction()

    await message_root.dispatch("open", interaction, ["0"])

    attachments = interaction.response.edit_message.await_args.kwargs["attachments"]
    assert [file.filename for file in attachments] == ["error-aaa111.txt"]


async def test_going_back_removes_the_attachment() -> None:
    browser = ErrorReportBrowser((make_report("aaa111"),))
    message_root, _ = message_root_browser(browser)
    await message_root.dispatch("open", fake_interaction(), ["0"])
    interaction = fake_interaction()

    await message_root.dispatch("back", interaction)

    assert interaction.response.edit_message.await_args.kwargs["attachments"] == []


async def test_prefix_invocation_does_not_post_a_traceback_in_the_channel() -> None:
    """`Context.send` drops `ephemeral` without an interaction, so the report goes to DMs."""
    report = make_report()
    cog = make_cog(report=report)
    ctx = make_context(bot=cog.bot)

    await Diagnostics.error_group.callback(cog, cast(Context[Any], ctx), "abc123")  # type: ignore[arg-type]

    ctx.author.send.assert_awaited_once()
    assert isinstance(ctx.author.send.await_args.kwargs["view"], MountedView)
    assert "view" in ctx.send.await_args.kwargs
    assert not isinstance(ctx.send.await_args.kwargs["view"], MountedView)


async def test_a_closed_dm_is_reported_rather_than_worked_around() -> None:
    cog = make_cog(report=make_report())
    ctx = make_context(
        bot=cog.bot, dm_raises=discord.Forbidden(cast(Any, SimpleNamespace(status=403, reason="")), "no dms")
    )

    await Diagnostics.error_group.callback(cog, cast(Context[Any], ctx), "abc123")  # type: ignore[arg-type]

    assert not isinstance(ctx.send.await_args.kwargs["view"], MountedView)
    assert "direct message" in str(ctx.send.await_args.kwargs["view"].to_components())


async def test_slash_invocation_stays_ephemeral_in_the_channel() -> None:
    cog = make_cog(report=make_report())
    ctx = make_context(bot=cog.bot, slash=True)

    await Diagnostics.error_group.callback(cog, cast(Context[Any], ctx), "abc123")  # type: ignore[arg-type]

    ctx.author.send.assert_not_awaited()
    assert ctx.send.await_args.kwargs["ephemeral"] is True


@pytest.mark.parametrize("in_guild", [True, False])
async def test_recent_delivers_the_view_privately(in_guild: bool) -> None:
    cog = make_cog(reports=(make_report(),))
    ctx = make_context(bot=cog.bot, in_guild=in_guild)

    await Diagnostics.recent_errors.callback(cog, cast(Context[Any], ctx), work_lost=False)  # type: ignore[arg-type]

    # In a direct message the channel already is private, so the view stays where it was asked for.
    sender = ctx.author.send if in_guild else ctx.send
    assert isinstance(sender.await_args.kwargs["view"], MountedView)


async def test_a_fence_inside_a_traceback_cannot_close_the_card_fence() -> None:
    _, view = message_root_browser(ErrorReportBrowser(report=make_report(traceback="ValueError: ```not markdown```")))

    fenced = next(text for text in _texts(view) if "not markdown" in text)
    assert "```not" not in fenced


async def test_the_attachment_bundles_traceback_and_log_tail() -> None:
    report = make_report(log_tail=("line a", "line b"))
    payload = report_attachment(report).fp.read().decode()
    assert "ValueError: boom" in payload
    assert "line b" in payload
