"""What `/build debug` hands a maintainer."""

import json
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import discord
from discord.ext import commands

from squid.bot.submission.search import SearchCog, _debug_dump
from squid.builds.domain import DoorBuild, Status
from tests.helpers.discord import make_layout_bot


class StubQueries:
    def __init__(self, build: Any) -> None:
        self._build = build

    async def get(self, build_id: int) -> Any:
        return self._build


def _cog(build: Any) -> SearchCog[Any]:
    cog = SearchCog.__new__(SearchCog)
    cog.bot = cast(Any, make_layout_bot(services=SimpleNamespace(settings=SimpleNamespace())))
    cog.queries = cast(Any, StubQueries(build))
    return cog


def _context(bot: Any) -> commands.Context[Any]:
    return cast(
        commands.Context[Any],
        cast(
            Any,
            SimpleNamespace(
                defer=AsyncMock(),
                send=AsyncMock(return_value=SimpleNamespace(id=1)),
                guild=None,
                interaction=None,
                author=SimpleNamespace(id=7),
                bot=bot,
            ),
        ),
    )


def test_the_dump_is_json_a_person_can_read() -> None:
    """It was `str(build.__dict__)`, which renders enums as their repr and dicts as Python."""
    build = DoorBuild(id=42, submission_status=Status.PENDING, creators_ign=["Alice"])

    state = json.loads(_debug_dump(build))

    assert state["id"] == 42
    assert state["submission_status"] == "PENDING"
    assert state["creators_ign"] == ["Alice"]


def test_the_embedding_is_summarized_rather_than_dumped() -> None:
    """A few thousand floats would dominate the file and tell a reader nothing,
    but whether a build is embedded at all is a real question."""
    state = json.loads(_debug_dump(DoorBuild(id=42, embedding=[0.5] * 1536)))

    assert "embedding" not in state
    assert state["embedding_dimensions"] == 1536

    assert json.loads(_debug_dump(DoorBuild(id=42)))["embedding_dimensions"] is None


async def test_the_state_is_attached_instead_of_pasted() -> None:
    build = DoorBuild(id=42, submission_status=Status.PENDING)
    cog = _cog(build)
    ctx = _context(cog.bot)

    await SearchCog.debug_build.callback(cog, ctx, build_id=42)  # type: ignore[arg-type]

    kwargs = cast(Any, ctx).send.call_args.kwargs
    attachment = kwargs["files"][0]
    assert isinstance(attachment, discord.File)
    assert attachment.filename == "build-42-debug.json"
    assert json.loads(attachment.fp.read())["id"] == 42


async def test_a_missing_build_is_an_error_card_not_an_empty_file() -> None:
    cog = _cog(None)
    ctx = _context(cog.bot)

    await SearchCog.debug_build.callback(cog, ctx, build_id=42)  # type: ignore[arg-type]

    kwargs = cast(Any, ctx).send.call_args.kwargs
    assert "file" not in kwargs
