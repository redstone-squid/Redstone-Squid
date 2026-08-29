"""The call sites that traded their own truncation scheme for the shared paginator (audit C6)."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import discord
from discord.ext import commands

from squid.bot.submission.records import RecordCog
from squid.bot.version_tracking import VersionTracker
from squid_ui_discord.testing import fake_interaction, fake_message
from tests.helpers.discord import make_layout_bot


def _context() -> commands.Context[Any]:
    return cast(
        commands.Context[Any],
        cast(
            Any,
            SimpleNamespace(
                bot=make_layout_bot(),
                send=AsyncMock(return_value=fake_message(message_id=1)),
                guild=None,
                interaction=None,
                author=SimpleNamespace(id=7),
            ),
        ),
    )


def _text(view: discord.ui.LayoutView) -> str:
    return "\n".join(child.content for child in view.walk_children() if isinstance(child, discord.ui.TextDisplay))


def _sent(ctx: commands.Context[Any]) -> discord.ui.LayoutView:
    return cast(Any, ctx).send.call_args.kwargs["view"]


def _gap(definition_id: int) -> Any:
    return SimpleNamespace(
        definition_id=definition_id,
        title=f"Smallest {definition_id}x{definition_id} Door",
        build_ids=[definition_id],
        fields=["volume"],
        diagnostics=[{"code": "unknown_restriction"}],
    )


def _records_cog(gaps: list[Any]) -> RecordCog[Any]:
    cog = RecordCog.__new__(RecordCog)
    cog.bot = cast(Any, SimpleNamespace(services=SimpleNamespace(settings=SimpleNamespace())))
    cog.records = cast(Any, SimpleNamespace(gaps=AsyncMock(return_value=gaps), title_gaps=AsyncMock(return_value=gaps)))
    return cog


def _version_cog(versions: list[str]) -> VersionTracker[Any]:
    cog = VersionTracker.__new__(VersionTracker)
    cog.bot = cast(Any, SimpleNamespace(services=SimpleNamespace(settings=SimpleNamespace())))
    cog.version_service = cast(Any, SimpleNamespace(list_display=AsyncMock(return_value=versions)))
    return cog


async def test_the_version_list_asks_for_every_version_it_knows() -> None:
    """It stopped at 20 with a TODO where the pagination should have been."""
    cog = _version_cog([f"1.{minor}" for minor in range(60)])
    ctx = _context()

    await VersionTracker.versions.callback(cog, ctx)  # type: ignore[arg-type]

    assert cast(Any, cog).version_service.list_display.await_args.kwargs.get("limit") is None
    assert "**Page 1 of 2**" not in _text(_sent(ctx))
    assert "Page 1 of 2" in _text(_sent(ctx))


async def test_versions_read_as_a_run_of_tokens_rather_than_paragraphs() -> None:
    cog = _version_cog(["1.20", "1.21"])
    ctx = _context()

    await VersionTracker.versions.callback(cog, ctx)  # type: ignore[arg-type]

    assert "1.20, 1.21" in _text(_sent(ctx))


async def test_record_gaps_page_instead_of_stopping_at_thirty() -> None:
    """The old cap hid the backlog exactly when there was one worth reading."""
    cog = _records_cog([_gap(index) for index in range(1, 41)])
    ctx = _context()

    await RecordCog.gaps.callback(cog, ctx)  # type: ignore[arg-type]
    body = _text(_sent(ctx))

    assert "…and 10 more." not in body
    assert "Page 1 of 3" in body


async def test_title_diagnostics_page_too() -> None:
    cog = _records_cog([_gap(index) for index in range(1, 41)])
    ctx = _context()

    await RecordCog.title_gaps.callback(cog, ctx)  # type: ignore[arg-type]

    assert "Page 1 of 3" in _text(_sent(ctx))


async def test_a_clean_diagnostic_still_says_so() -> None:
    cog = _records_cog([])
    ctx = _context()

    await RecordCog.gaps.callback(cog, ctx)  # type: ignore[arg-type]

    assert "No unresolved active record categories." in _text(_sent(ctx))


async def test_a_staff_diagnostic_stays_out_of_the_channel_on_the_slash_path() -> None:
    cog = _records_cog([_gap(1)])
    ctx = _context()
    # The package's own double rather than a hand-rolled namespace: delivery reads
    # `is_expired`, `response.is_done()` and the followup surfaces before it decides
    # between the interaction and the channel, and a partial stub only reports that one
    # attribute at a time. Locales stay unset, which is what this test is about.
    interaction = fake_interaction()
    interaction.guild_locale = None
    interaction.locale = None
    cast(Any, ctx).interaction = interaction

    await RecordCog.gaps.callback(cog, ctx)  # type: ignore[arg-type]

    assert cast(Any, ctx).send.call_args.kwargs["ephemeral"] is True
