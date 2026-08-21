"""The Discord consent gate: ask first, then continue what was asked for."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import anyio
import discord
import pytest
from discord.ext import commands
from whenever import Instant

import squid_layouts as sl
from squid.accounts.application import AccountService
from squid.accounts.domain import CURRENT_CONSENT_VERSION, Account, AccountConsent, AccountIdentity, IdentityProvider
from squid.bot.consent import NOT_ASKED, ensure_consented_account, prompt_for_consent
from squid.bot.utils.mount_registry import MountRegistry, SessionKey
from squid_layouts.discord.testing import fake_message

AFTER_CUTOFF = Instant.from_utc(2026, 8, 5)
USER_ID = 123


def discord_account(*, account_id: int = 7, consented: bool) -> Account:
    return Account(
        id=account_id,
        created_at=AFTER_CUTOFF,
        identities=(AccountIdentity.discord(USER_ID),),
        consent=AccountConsent(CURRENT_CONSENT_VERSION, AFTER_CUTOFF) if consented else None,
    )


def make_accounts(existing: Account | None) -> Any:
    accounts = AsyncMock()
    accounts.get_account_by_identity.return_value = existing
    accounts.get_or_create_identity.return_value = discord_account(consented=True)
    return accounts


def make_context() -> Any:
    """A prefix/hybrid context; `send` is the only surface the gate uses."""
    return cast(
        commands.Context[Any],
        SimpleNamespace(
            author=SimpleNamespace(id=USER_ID),
            interaction=None,
            send=AsyncMock(return_value=fake_message(message_id=1)),
            bot=SimpleNamespace(mounts=MountRegistry()),
        ),
    )


def make_interaction(*, response_done: bool) -> Any:
    return SimpleNamespace(
        user=SimpleNamespace(id=USER_ID),
        response=SimpleNamespace(is_done=lambda: response_done, send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock(return_value=fake_message(message_id=1))),
        original_response=AsyncMock(return_value=fake_message(message_id=1)),
    )


def answer(view_holder: list[Any], *, agree: bool) -> None:
    """Stand in for the user pressing a button, without a real interaction."""
    view = view_holder[0]
    if agree:
        view.consent = AccountConsent.grant_current()
    view.stop()


async def test_an_already_consented_user_is_never_prompted() -> None:
    """The fast path is one indexed read and nothing sent; it runs on every gated command."""
    accounts = make_accounts(discord_account(consented=True))
    ctx = make_context()

    assert await ensure_consented_account(ctx, cast(AccountService, accounts)) == 7
    ctx.send.assert_not_awaited()
    accounts.get_or_create_identity.assert_not_awaited()


async def test_declining_stores_nothing_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """The notice's central promise, expressed as a test.

    Cancelling cannot store anything, which it could not promise if the account row had been
    minted in order to ask the question. So the gate must not have created one.
    """
    accounts = make_accounts(None)
    ctx = make_context()
    _stub_prompt(monkeypatch, agree=False)

    assert await ensure_consented_account(ctx, cast(AccountService, accounts)) is None
    accounts.get_or_create_identity.assert_not_awaited()
    ctx.send.assert_awaited()  # The user is told, rather than left with a silent no-op.


async def test_agreeing_mints_the_account_and_its_receipt_in_one_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Creating the row and recording consent separately leaves a receipt-less account behind
    whenever the second call fails."""
    accounts = make_accounts(None)
    ctx = make_context()
    _stub_prompt(monkeypatch, agree=True)

    assert await ensure_consented_account(ctx, cast(AccountService, accounts)) == 7

    accounts.get_or_create_identity.assert_awaited_once()
    call = accounts.get_or_create_identity.await_args
    assert call.args == (IdentityProvider.DISCORD, str(USER_ID))
    assert call.kwargs["consent"].version == CURRENT_CONSENT_VERSION


async def test_a_stale_receipt_is_asked_again(monkeypatch: pytest.MonkeyPatch) -> None:
    accounts = make_accounts(Account(id=7, created_at=AFTER_CUTOFF, consent=AccountConsent("1970-01-01", AFTER_CUTOFF)))
    ctx = make_context()
    _stub_prompt(monkeypatch, agree=True)

    assert await ensure_consented_account(ctx, cast(AccountService, accounts)) == 7
    accounts.get_or_create_identity.assert_awaited_once()


@pytest.mark.parametrize("response_done", [False, True], ids=["fresh", "deferred"])
async def test_the_gate_works_from_an_interaction_in_either_response_state(
    monkeypatch: pytest.MonkeyPatch, response_done: bool
) -> None:
    """Slash cogs, modals and view buttons all reach the gate, and they differ only in whether
    the interaction has already been answered."""
    accounts = make_accounts(None)
    interaction = make_interaction(response_done=response_done)
    _stub_prompt(monkeypatch, agree=True)

    assert (
        await ensure_consented_account(cast(discord.Interaction[Any], interaction), cast(AccountService, accounts)) == 7
    )


async def test_the_gate_stays_silent_when_the_user_was_never_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    """`NOT_ASKED` is not a decline, and reporting it as one is a lie the user can read.

    The prompt already told them why nothing happened. A second message saying "cancelled,
    nothing was stored" describes a cancellation that never took place.
    """
    accounts = make_accounts(None)
    ctx = make_context()

    async def prompt(*_args: object, **_kwargs: object) -> object:
        return NOT_ASKED

    monkeypatch.setattr("squid.bot.consent.prompt_for_consent", prompt)

    assert await ensure_consented_account(ctx, cast(AccountService, accounts)) is None
    accounts.get_or_create_identity.assert_not_awaited()
    ctx.send.assert_not_awaited()


class _Blank(sl.Component):
    def render(self):
        return [sl.primitives.Text("parent")]


async def _collect(into: list[Any], awaitable: Any) -> None:
    into.append(await awaitable)


async def test_a_second_prompt_is_refused_while_the_first_is_open() -> None:
    """Two prompts for one user leave the first's waiter stranded whichever one wins."""
    ctx = make_context()
    key = SessionKey("consent", USER_ID)
    outcomes: list[Any] = []

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(lambda: _collect(outcomes, prompt_for_consent(ctx, user_id=USER_ID)))
        while ctx.bot.mounts.get(key) is None:
            await anyio.sleep(0)
        first = ctx.bot.mounts.get(key)

        second = await prompt_for_consent(ctx, user_id=USER_ID)

        assert second is NOT_ASKED
        assert ctx.bot.mounts.get(key) is first  # the one being awaited is the one that stands
        await ctx.bot.mounts.close(key, disable=False)

    assert outcomes == [None]


async def test_a_closing_parent_ends_the_wait_instead_of_stranding_it() -> None:
    """Disabling the prompt's buttons is not enough.

    The prompt is awaited, and the handler that awaits it holds the parent's action lock. A
    cascade that stops the mount without ending the wait leaves both blocked for the rest of
    the 120 s, which is the leak this was supposed to close.
    """
    ctx = make_context()
    parent = sl.discord.Mount(_Blank(), timeout=None)
    outcomes: list[Any] = []

    # Bounded well under the prompt's own 120 s: a wait that only ends when the timeout
    # expires would otherwise pass this test for exactly the wrong reason.
    with anyio.fail_after(5):
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(lambda: _collect(outcomes, prompt_for_consent(ctx, user_id=USER_ID, parent=parent)))
            while ctx.bot.mounts.get(SessionKey("consent", USER_ID)) is None:
                await anyio.sleep(0)

            await parent.finish(disable=False)

    assert outcomes == [None]


def _stub_prompt(monkeypatch: pytest.MonkeyPatch, *, agree: bool) -> None:
    """Replace the waiting prompt with its outcome; the view itself is covered elsewhere."""

    async def prompt(*_args: object, **_kwargs: object) -> AccountConsent | None:
        return AccountConsent.grant_current() if agree else None

    monkeypatch.setattr("squid.bot.consent.prompt_for_consent", prompt)
