"""The panel that replaced `account identities`, `account visibility` and `account unlink`."""

from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import UUID

import discord
from whenever import Instant

from squid.accounts.domain import Account, AccountConsent, AccountIdentity, AccountProfile
from squid.bot.account_view import (
    AccountPanelView,
    IdentitySelect,
    IdentityVisibilityButton,
    UnlinkIdentityButton,
)
from squid.bot.verify import VerifyCog

ACCOUNT_ID = 42
AUTHOR_ID = 555
NOW = Instant.from_utc(2026, 8, 19)
JAVA_UUID = UUID("11111111-1111-1111-1111-111111111111")

DISCORD = replace(AccountIdentity.discord(AUTHOR_ID), id=1, verified_at=NOW)
JAVA = replace(AccountIdentity.java(JAVA_UUID, username="Notch"), id=2, verified_at=NOW)


def _removes(identities: tuple[AccountIdentity, ...]) -> Any:
    """Answer with the identity the caller named, since the message reads it back."""

    async def unlink(account_id: int, identity_id: int) -> AccountIdentity:
        del account_id
        return next(identity for identity in identities if identity.id == identity_id)

    return unlink


async def make_panel(
    identities: tuple[AccountIdentity, ...] = (DISCORD, JAVA),
    *,
    hidden: bool = False,
    consented: bool = True,
) -> tuple[AccountPanelView, Any]:
    account = Account(
        identities,
        AccountConsent.grant_current() if consented else None,
        ACCOUNT_ID,
        NOW,
    )
    profile = AccountProfile(account_id=ACCOUNT_ID, hidden=hidden)
    accounts = SimpleNamespace(
        get_account_by_id=AsyncMock(return_value=account),
        get_profile=AsyncMock(return_value=profile),
        set_identity_visibility=AsyncMock(return_value=identities[0]),
        update_profile=AsyncMock(return_value=profile),
        unlink_identity=AsyncMock(side_effect=_removes(identities)),
        grant_current_consent=AsyncMock(return_value=account),
    )
    panel = AccountPanelView(accounts=cast(Any, accounts), account_id=ACCOUNT_ID, author_id=AUTHOR_ID)
    await panel.load()
    return panel, accounts


def make_interaction() -> Any:
    """A component interaction that remembers having been deferred."""
    deferred = False

    async def defer(*args: object, **kwargs: object) -> None:
        nonlocal deferred
        deferred = True

    return SimpleNamespace(
        user=SimpleNamespace(id=AUTHOR_ID),
        message=None,
        response=SimpleNamespace(
            edit_message=AsyncMock(),
            send_message=AsyncMock(),
            defer=AsyncMock(side_effect=defer),
            is_done=lambda: deferred,
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
    )


def text_of(view: discord.ui.LayoutView) -> str:
    return "\n".join(child.content for child in view.walk_children() if isinstance(child, discord.ui.TextDisplay))


def select_of(view: AccountPanelView) -> IdentitySelect:
    return next(child for child in view.walk_children() if isinstance(child, IdentitySelect))


def button_of[ButtonT: discord.ui.Button[Any]](view: AccountPanelView, kind: type[ButtonT]) -> ButtonT:
    return next(child for child in view.walk_children() if isinstance(child, kind))


async def test_every_linked_account_is_listed_and_pickable_without_its_id() -> None:
    """`identities` printed the id and told you to type it into two other commands."""
    panel, _ = await make_panel()

    assert "Minecraft (Java) — Notch" in text_of(panel)
    assert "id `2`" not in text_of(panel)
    assert [option.value for option in select_of(panel).options] == ["1", "2"]


async def test_the_controls_wait_for_an_account_to_be_picked() -> None:
    panel, _ = await make_panel()

    assert button_of(panel, IdentityVisibilityButton).disabled
    assert button_of(panel, UnlinkIdentityButton).disabled


async def test_hiding_the_picked_account_names_it_by_the_id_the_select_carried() -> None:
    panel, accounts = await make_panel()
    panel.select(2)

    await panel.toggle_identity(make_interaction())

    accounts.set_identity_visibility.assert_awaited_once_with(ACCOUNT_ID, 2, is_public=False)


async def test_the_page_toggle_covers_the_whole_page_rather_than_one_account() -> None:
    """`visibility` decided between the two by whether `identity:` was given."""
    panel, accounts = await make_panel()

    await panel.toggle_page(make_interaction())

    update = accounts.update_profile.await_args.args[1]
    assert update.hidden is True
    accounts.set_identity_visibility.assert_not_awaited()


async def test_unlinking_asks_before_it_acts() -> None:
    panel, accounts = await make_panel()
    panel.select(2)

    await panel.unlink(make_interaction())

    accounts.unlink_identity.assert_not_awaited()
    assert panel.unlink_armed
    assert str(button_of(panel, UnlinkIdentityButton).label) == "Unlink for good"

    confirming = make_interaction()
    await panel.unlink(confirming)

    accounts.unlink_identity.assert_awaited_once_with(ACCOUNT_ID, 2)
    said = text_of(confirming.followup.send.await_args.kwargs["view"])
    assert "Notch" in said
    assert "credit" in said


async def test_picking_a_different_account_disarms_the_unlink() -> None:
    panel, _ = await make_panel()
    panel.select(2)
    await panel.unlink(make_interaction())

    panel.select(1)

    assert not panel.unlink_armed


async def test_unlinking_the_identity_you_are_speaking_through_says_what_it_costs() -> None:
    panel, _ = await make_panel()
    panel.select(1)

    await panel.unlink(make_interaction())

    assert "stop recognising you here" in text_of(panel)


async def test_a_hidden_page_still_says_what_stays_public() -> None:
    """The explanation `visibility` gave when it hid a page has to survive the merge."""
    panel, _ = await make_panel(hidden=True)

    assert "still lists the creator names you hold" in text_of(panel)


async def test_a_published_change_is_refused_until_the_current_notice_is_accepted(
    monkeypatch: Any,
) -> None:
    """The receipt goes to the account the panel holds, not to whoever a lookup would find."""
    import squid.bot.account_view as account_view

    monkeypatch.setattr(account_view, "prompt_for_consent", AsyncMock(return_value=None))
    panel, accounts = await make_panel(consented=False)

    await panel.toggle_page(make_interaction())

    accounts.update_profile.assert_not_awaited()
    accounts.grant_current_consent.assert_not_awaited()


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
