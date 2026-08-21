"""What `/account` answers with, and to whom."""

from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import UUID

import discord
from whenever import Instant

from squid.accounts.domain import (
    Account,
    AccountIdentity,
    PublicCreatorProfile,
)
from squid.bot.verify import VerifyCog

ACCOUNT_ID = 42
AUTHOR_ID = 555
NOW = Instant.from_utc(2026, 8, 19)
JAVA_UUID = UUID("11111111-1111-1111-1111-111111111111")

DISCORD = replace(AccountIdentity.discord(AUTHOR_ID), id=1, verified_at=NOW)
JAVA = replace(AccountIdentity.java(JAVA_UUID, username="Notch"), id=2, verified_at=NOW)


def text_of(view: discord.ui.LayoutView) -> str:
    return "\n".join(child.content for child in view.walk_children() if isinstance(child, discord.ui.TextDisplay))


async def test_someone_with_no_account_is_told_how_to_get_one() -> None:
    """There is no panel to open for an account that does not exist yet."""
    cog = VerifyCog.__new__(VerifyCog)
    cog.bot = cast(
        Any,
        SimpleNamespace(services=SimpleNamespace(settings=SimpleNamespace(get_locale=AsyncMock(return_value=None)))),
    )
    cog.account_service = cast(Any, SimpleNamespace(get_account_by_identity=AsyncMock(return_value=None)))
    ctx = SimpleNamespace(
        interaction=None,
        guild=SimpleNamespace(id=5, preferred_locale="en-US"),
        author=SimpleNamespace(id=AUTHOR_ID),
        send=AsyncMock(),
    )

    await VerifyCog.account_group.callback(cog, cast(Any, ctx))  # type: ignore[arg-type]

    assert "/account link" in text_of(ctx.send.await_args.kwargs["view"])


async def test_somebody_elses_creator_page_is_a_public_read() -> None:
    """Rule 2 of the ephemerality policy: a read of shared content answers in the channel.

    `profile` answered privately whoever it was about, which the rule 5.7 wrote down does not.
    """
    page = UUID(int=7)
    cog = VerifyCog.__new__(VerifyCog)
    cog.bot = cast(
        Any,
        SimpleNamespace(services=SimpleNamespace(settings=SimpleNamespace(get_locale=AsyncMock(return_value=None)))),
    )
    cog.account_service = cast(
        Any,
        SimpleNamespace(
            get_account_by_identity=AsyncMock(return_value=Account((JAVA,), None, 9, NOW, page)),
            get_public_profile=AsyncMock(return_value=PublicCreatorProfile(public_id=page, hidden=False)),
        ),
    )
    ctx = SimpleNamespace(
        interaction=SimpleNamespace(guild_locale=None, locale="en-US"),
        guild=SimpleNamespace(id=5, preferred_locale="en-US"),
        author=SimpleNamespace(id=AUTHOR_ID),
        send=AsyncMock(),
    )
    other = SimpleNamespace(id=999, display_name="Someone")

    await VerifyCog.account_group.callback(cog, cast(Any, ctx), cast(Any, other))  # type: ignore[arg-type]

    assert ctx.send.await_args.kwargs.get("ephemeral") is not True
