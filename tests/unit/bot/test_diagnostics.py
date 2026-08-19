"""Tests for reading a stored error report from Discord."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import discord
import pytest
from discord.ext.commands import Context
from whenever import Instant

import squid.bot.app
from squid.bot.diagnostics import Diagnostics
from squid.bot.diagnostics_view import PAGE_CHARS, ErrorReportSelect, ErrorReportView
from squid.diagnostics.domain import ErrorReport


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
) -> Any:
    """A context stub exposing only what the cog's delivery path touches."""
    author_send = AsyncMock(side_effect=dm_raises, return_value=AsyncMock(spec=discord.Message))
    return SimpleNamespace(
        interaction=SimpleNamespace(guild_locale=None, locale="en-US") if slash else None,
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
    bot = SimpleNamespace(services=SimpleNamespace(error_reports=error_reports, settings=settings))
    return Diagnostics(cast("squid.bot.app.RedstoneSquid", bot))


async def test_recent_list_offers_every_entry_for_opening() -> None:
    """The audit's complaint: a listed reference could only be read, then retyped."""
    reports = (make_report("aaa111"), make_report("bbb222", work_lost=True))
    view = ErrorReportView(author_id=1, reports=reports)

    payload = view.to_components()
    select = payload[1]["components"][0]
    assert [option["label"] for option in select["options"]] == ["aaa111", "bbb222"]
    assert "aaa111" in str(payload[0])


async def test_opening_a_listed_report_replaces_the_list_in_place() -> None:
    view = ErrorReportView(author_id=1, reports=(make_report("aaa111"),))

    view.open(view.report_at(0))

    payload = str(view.to_components())
    assert "Error aaa111" in payload
    assert "ValueError: boom" in payload
    assert "Back" in payload


async def test_a_long_traceback_is_readable_past_one_page() -> None:
    """Previously the card showed a fixed 1200-character tail and nothing could reach the rest."""
    frames = "\n".join(f"  File 'module{index}.py', line {index}, in frame{index}" for index in range(300))
    view = ErrorReportView(author_id=1, report=make_report(traceback=f"Traceback:\n{frames}\nValueError: boom"))

    page = view.page
    assert page is not None
    assert page.total > 1
    # The failing frame is at the end, so that is where the report opens.
    assert page.number == page.total
    assert "ValueError: boom" in page.body

    view.previous_page()
    earlier = view.page
    assert earlier is not None
    assert earlier.number == page.number - 1
    while view.can_go_back:
        view.previous_page()
    first = view.page
    assert first is not None
    assert "frame0" in first.body


async def test_paging_stops_at_both_ends() -> None:
    view = ErrorReportView(author_id=1, report=make_report(traceback="one frame"))

    assert not view.can_go_back
    assert not view.can_go_forward
    view.next_page()
    assert view.page is not None
    assert view.page.number == 1


async def test_the_log_tail_pages_after_the_traceback() -> None:
    view = ErrorReportView(author_id=1, report=make_report(log_tail=("first line", "second line")))

    view.next_page()
    page = view.page
    assert page is not None
    assert page.section == "Log tail"
    assert "second line" in page.body


async def test_pages_never_exceed_the_display_budget() -> None:
    long_line = "x" * (PAGE_CHARS * 2 + 7)
    view = ErrorReportView(author_id=1, report=make_report(traceback=f"{long_line}\nValueError: boom"))

    view.open(make_report(traceback=f"{long_line}\nValueError: boom"))
    bodies: list[str] = []
    while True:
        page = view.page
        assert page is not None
        bodies.append(page.body)
        if not view.can_go_back:
            break
        view.previous_page()

    assert all(len(body) <= PAGE_CHARS for body in bodies)
    assert "".join(reversed(bodies)).replace("\n", "") == long_line + "ValueError: boom"


async def test_choosing_a_report_attaches_its_full_text() -> None:
    view = ErrorReportView(author_id=1, reports=(make_report("aaa111"),))
    select = next(child for child in view.walk_children() if isinstance(child, ErrorReportSelect))
    select._values = ["0"]  # type: ignore[reportPrivateUsage]  # what Discord would deliver
    edit = AsyncMock()
    interaction = cast(
        discord.Interaction[discord.Client],
        SimpleNamespace(response=SimpleNamespace(edit_message=edit, is_done=Mock(return_value=False)), message=None),
    )

    await select.callback(interaction)

    assert edit.await_args is not None
    attachments = edit.await_args.kwargs["attachments"]
    assert [file.filename for file in attachments] == ["error-aaa111.txt"]


async def test_prefix_invocation_does_not_post_a_traceback_in_the_channel() -> None:
    """`Context.send` drops `ephemeral` without an interaction, so the report goes to DMs."""
    report = make_report()
    cog = make_cog(report=report)
    ctx = make_context()

    await Diagnostics.error_group.callback(cog, cast(Context[Any], ctx), "abc123")  # type: ignore[arg-type]

    ctx.author.send.assert_awaited_once()
    assert isinstance(ctx.author.send.await_args.kwargs["view"], ErrorReportView)
    assert "view" in ctx.send.await_args.kwargs
    assert not isinstance(ctx.send.await_args.kwargs["view"], ErrorReportView)


async def test_a_closed_dm_is_reported_rather_than_worked_around() -> None:
    cog = make_cog(report=make_report())
    ctx = make_context(dm_raises=discord.Forbidden(cast(Any, SimpleNamespace(status=403, reason="")), "no dms"))

    await Diagnostics.error_group.callback(cog, cast(Context[Any], ctx), "abc123")  # type: ignore[arg-type]

    assert not isinstance(ctx.send.await_args.kwargs["view"], ErrorReportView)
    assert "direct message" in str(ctx.send.await_args.kwargs["view"].to_components())


async def test_slash_invocation_stays_ephemeral_in_the_channel() -> None:
    cog = make_cog(report=make_report())
    ctx = make_context(slash=True)

    await Diagnostics.error_group.callback(cog, cast(Context[Any], ctx), "abc123")  # type: ignore[arg-type]

    ctx.author.send.assert_not_awaited()
    assert ctx.send.await_args.kwargs["ephemeral"] is True


@pytest.mark.parametrize("in_guild", [True, False])
async def test_recent_delivers_the_view_privately(in_guild: bool) -> None:
    cog = make_cog(reports=(make_report(),))
    ctx = make_context(in_guild=in_guild)

    await Diagnostics.recent_errors.callback(cog, cast(Context[Any], ctx), work_lost=False)  # type: ignore[arg-type]

    # In a direct message the channel already is private, so the view stays where it was asked for.
    sender = ctx.author.send if in_guild else ctx.send
    assert isinstance(sender.await_args.kwargs["view"], ErrorReportView)


async def test_a_fence_inside_a_traceback_cannot_close_the_card_fence() -> None:
    view = ErrorReportView(author_id=1, report=make_report(traceback="ValueError: ```not markdown```"))

    content = view.to_components()[0]["components"][0]["content"]
    assert "```not" not in content
    assert "`\u200b``not markdown`\u200b``" in content
