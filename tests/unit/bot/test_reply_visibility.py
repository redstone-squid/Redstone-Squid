"""The one ephemerality rule, at the sites where getting it wrong costs something (audit C2)."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import discord
import pytest
from discord.ext.commands import Context
from whenever import Instant

from squid.bot.ui import text_layout
from squid.bot.utils.visibility import deliver_privately, personal
from squid.bot.verify import VerifyCog

MERGE_CODE = "SPRUCE-PISTON-42"


def make_context(*, slash: bool = False, in_guild: bool = True, dm_raises: Exception | None = None) -> Any:
    author_send = AsyncMock(side_effect=dm_raises, return_value=AsyncMock(spec=discord.Message))
    return SimpleNamespace(
        interaction=(
            SimpleNamespace(
                guild_locale=None,
                locale="en-US",
                response=SimpleNamespace(is_done=lambda: False),
                is_expired=lambda: False,
                expires_at=None,
            )
            if slash
            else None
        ),
        guild=SimpleNamespace(id=5, preferred_locale="en-US") if in_guild else None,
        author=SimpleNamespace(id=1, send=author_send),
        send=AsyncMock(return_value=AsyncMock(spec=discord.Message)),
    )


def make_cog() -> VerifyCog[Any]:
    accounts = SimpleNamespace(
        create_merge_code=AsyncMock(
            return_value=(MERGE_CODE, SimpleNamespace(expires_at=Instant.from_utc(2026, 8, 20)))
        ),
    )
    cog = VerifyCog.__new__(VerifyCog)
    cog.bot = cast(
        Any,
        SimpleNamespace(services=SimpleNamespace(settings=SimpleNamespace(get_locale=AsyncMock(return_value=None)))),
    )
    cog.account_service = cast(Any, accounts)
    return cog


@pytest.fixture(autouse=True)
def _consented(monkeypatch: pytest.MonkeyPatch) -> None:
    import squid.bot.verify as verify

    monkeypatch.setattr(verify, "ensure_consented_account", AsyncMock(return_value=7))


def _rendered(call: Any) -> str:
    return str(call.kwargs["view"].to_components())


def test_personal_admits_the_condition_instead_of_implying_it() -> None:
    """`Context.send` drops `ephemeral` without an interaction, so a literal `True` is a lie."""
    assert personal(cast(Context[Any], make_context(slash=True))) is True
    assert personal(cast(Context[Any], make_context())) is False


async def test_a_merge_code_never_reaches_the_channel_it_was_asked_for_in() -> None:
    """It hands an account over; the old comment claimed ephemerality the transport dropped."""
    ctx = make_context()

    await VerifyCog.merge_code.callback(make_cog(), cast(Context[Any], ctx))  # type: ignore[arg-type]

    assert MERGE_CODE in _rendered(ctx.author.send.await_args)
    assert MERGE_CODE not in _rendered(ctx.send.await_args)


async def test_the_slash_form_answers_ephemerally_rather_than_by_dm() -> None:
    ctx = make_context(slash=True)

    await VerifyCog.merge_code.callback(make_cog(), cast(Context[Any], ctx))  # type: ignore[arg-type]

    ctx.author.send.assert_not_awaited()
    assert ctx.send.await_args.kwargs["ephemeral"] is True
    assert MERGE_CODE in _rendered(ctx.send.await_args)


async def test_a_closed_dm_delivers_nothing_rather_than_falling_back() -> None:
    """The channel is exactly what the payload must not reach, so there is nowhere to fall back to."""
    ctx = make_context(dm_raises=discord.Forbidden(cast(Any, SimpleNamespace(status=403, reason="")), "no dms"))

    delivered = await deliver_privately(
        cast(Context[Any], ctx), text_layout("secret"), reason="Because it is a credential."
    )

    assert delivered is None
    assert "secret" not in _rendered(ctx.send.await_args)
    assert "direct message" in _rendered(ctx.send.await_args)


async def test_a_direct_message_context_answers_where_it_was_asked() -> None:
    """A DM is already private, so routing it to another DM would just be a second message."""
    ctx = make_context(in_guild=False)

    await deliver_privately(cast(Context[Any], ctx), text_layout("secret"), reason="Because it is a credential.")

    ctx.author.send.assert_not_awaited()
    assert "secret" in _rendered(ctx.send.await_args)
