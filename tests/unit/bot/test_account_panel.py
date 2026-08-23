"""What `/account` answers with, and to whom."""

from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import UUID

import discord
from whenever import Instant

import squid_layouts as sl
from squid.accounts.domain import (
    Account,
    AccountIdentity,
    AccountProfile,
    ProfileLink,
    PublicCreatorProfile,
)
from squid.bot.account_view import AccountPanel
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
        interaction=SimpleNamespace(
            guild_locale=None,
            locale="en-US",
            response=SimpleNamespace(is_done=lambda: False),
            is_expired=lambda: False,
            expires_at=None,
        ),
        guild=SimpleNamespace(id=5, preferred_locale="en-US"),
        author=SimpleNamespace(id=AUTHOR_ID),
        send=AsyncMock(),
    )
    other = SimpleNamespace(id=999, display_name="Someone")

    await VerifyCog.account_group.callback(cog, cast(Any, ctx), cast(Any, other))  # type: ignore[arg-type]

    assert ctx.send.await_args.kwargs.get("ephemeral") is not True


def _account_panel(profile: AccountProfile) -> AccountPanel:
    panel = AccountPanel(
        accounts=cast(Any, SimpleNamespace(update_profile=AsyncMock())),
        account_id=ACCOUNT_ID,
        author_id=AUTHOR_ID,
        locale="en",
    )
    panel._profile = profile
    return panel


def test_profile_editor_splits_profile_fields_from_ordered_links() -> None:
    panel = _account_panel(
        AccountProfile(
            ACCOUNT_ID,
            display_name="Builder",
            bio="Hello",
            links=(ProfileLink("Site", "https://example.com"),),
        )
    )

    component = panel._build_profile_editor()
    editor = cast(sl.patterns.Editor, component.pattern)
    values = editor.values(component.pattern_state)

    assert values["profile"] == {"display_name": "Builder", "pronouns": None, "bio": "Hello"}
    assert tuple(dict(link) for link in cast(tuple, values["links"])) == (
        {"label": "Site", "url": "https://example.com"},
    )


def test_profile_editor_rejects_non_https_links_before_staging() -> None:
    panel = _account_panel(AccountProfile.empty(ACCOUNT_ID))

    issues = panel._validate_link({"label": "Bad", "url": "http://example.com"})

    assert len(issues) == 1
    assert isinstance(issues[0], sl.forms.FormError)


async def test_profile_editor_commit_persists_and_returns_to_account_panel() -> None:
    panel = _account_panel(AccountProfile.empty(ACCOUNT_ID))
    panel._refresh = AsyncMock()  # type: ignore[method-assign]
    component = panel._build_profile_editor()
    panel._profile_editor = component
    editor = cast(sl.patterns.Editor, component.pattern)
    staged = editor.transition(
        component.pattern_state,
        "submit:profile",
        submitted={"display_name": "Builder", "pronouns": None, "bio": "Hello"},
    )
    committed = editor.transition(staged, "save")
    source = SimpleNamespace(notice=AsyncMock())

    assert component.on_change is not None
    await component.on_change(sl.patterns.PatternEvent(cast(Any, source), "save", staged, committed))

    cast(AsyncMock, panel._accounts.update_profile).assert_awaited_once()
    assert panel._profile_editor is None
    source.notice.assert_awaited_once()
