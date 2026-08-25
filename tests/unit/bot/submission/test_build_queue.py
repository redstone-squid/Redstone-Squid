"""What `/build queue` shows a reviewer."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import discord
from discord.ext import commands

from squid.bot.submission.search import SearchCog
from squid_discord.testing import fake_message
from tests.helpers.discord import make_layout_bot


class StubQueries:
    """Build queries that answer `pending()` with whatever the test set up."""

    def __init__(self, builds: list[Any]) -> None:
        self._builds = builds

    async def pending(self) -> list[Any]:
        return self._builds


def _build(build_id: int, *, submitter: int | None = 4242, creators: list[str] | None = None) -> Any:
    return SimpleNamespace(
        id=build_id,
        title=f"{build_id}x{build_id} Piston Door",
        creators_ign=creators if creators is not None else ["Alice", "Bob"],
        submitter_discord_id=submitter,
    )


def _cog(builds: list[Any]) -> SearchCog[Any]:
    cog = SearchCog.__new__(SearchCog)
    cog.bot = cast(Any, SimpleNamespace(services=SimpleNamespace(settings=SimpleNamespace())))
    cog.queries = cast(Any, StubQueries(builds))
    return cog


def _context() -> commands.Context[Any]:
    return cast(
        commands.Context[Any],
        cast(
            Any,
            SimpleNamespace(
                bot=make_layout_bot(),
                defer=AsyncMock(),
                send=AsyncMock(return_value=fake_message(message_id=1)),
                guild=None,
                interaction=None,
                author=SimpleNamespace(id=7),
            ),
        ),
    )


async def _run(builds: list[Any]) -> discord.ui.LayoutView:
    ctx = _context()
    await SearchCog.get_pending_submissions.callback(_cog(builds), ctx)  # type: ignore[arg-type]
    return cast(Any, ctx).send.call_args.kwargs["view"]


def _text(view: discord.ui.LayoutView) -> str:
    return "\n".join(child.content for child in view.walk_children() if isinstance(child, discord.ui.TextDisplay))


async def test_the_submitter_is_named_rather_than_numbered() -> None:
    """The old list printed `submitter_discord_id` as a bare integer (audit C5)."""
    body = _text(await _run([_build(1)]))

    assert "<@4242>" in body
    assert "submitted by 4242" not in body


async def test_a_build_submitted_from_an_unlinked_account_still_lists() -> None:
    """`submitter_discord_id` is derived from the account and can legitimately be absent."""
    body = _text(await _run([_build(1, submitter=None)]))

    assert "**#1**" in body
    assert "None" not in body


async def test_the_card_says_what_the_command_says() -> None:
    """It was titled "Open Records" while describing itself as pending submissions."""
    body = _text(await _run([_build(1)]))

    assert "Pending submissions" in body
    assert "Open Records" not in body


async def test_a_long_queue_is_paged_rather_than_truncated() -> None:
    view = await _run([_build(index) for index in range(1, 101)])
    body = _text(view)

    assert "**#1**" in body
    assert "**#11**" in body  # budget-filled, not the PagedList default of ten entries
    assert "**#100**" not in body
    assert any(isinstance(child, discord.ui.Button) for child in view.walk_children())


async def test_an_empty_queue_says_so() -> None:
    assert "Nothing is waiting for review." in _text(await _run([]))
