"""What `/account` answers with, and to whom."""

from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import UUID

import discord
import pytest
from whenever import Instant

import squid_discord as sd
import squid_layouts as sl
from squid.accounts.domain import (
    Account,
    AccountConsent,
    AccountIdentity,
    AccountProfile,
    ProfileLink,
    PublicCreatorProfile,
)
from squid.bot.account_view import AccountPanel
from squid.bot.verify import VerifyCog
from squid_discord.testing import commit_render, fake_interaction

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


def _gated_panel(monkeypatch: pytest.MonkeyPatch) -> tuple[AccountPanel, dict[str, Any]]:
    """A panel whose reader has not consented, with the notice stubbed out.

    The prompt is a mount of its own and is covered in `test_consent_gate`; what matters here
    is that the press ends without one being awaited, and that the continuation does the work.
    """
    panel = AccountPanel(
        accounts=cast(
            Any,
            SimpleNamespace(
                update_profile=AsyncMock(),
                set_identity_visibility=AsyncMock(),
                grant_current_consent=AsyncMock(),
            ),
        ),
        account_id=ACCOUNT_ID,
        author_id=AUTHOR_ID,
        locale="en",
    )
    panel._profile = AccountProfile.empty(ACCOUNT_ID)
    panel._needs_consent = True
    panel._refresh = AsyncMock()  # type: ignore[method-assign]
    opened: dict[str, Any] = {}

    async def request(_target: object, *, on_answer: Any, **_kwargs: Any) -> bool:
        opened["on_answer"] = on_answer
        return True

    monkeypatch.setattr("squid.bot.account_view.request_consent", request)
    return panel, opened


def _press(mount: Any) -> Any:
    """A press double carrying the Discord facts `_with_consent` reads off an event."""
    responder = SimpleNamespace(interaction=SimpleNamespace(user=SimpleNamespace(id=AUTHOR_ID)), mount=mount)
    return SimpleNamespace(responder=responder, value=True)


async def test_a_press_needing_consent_ends_instead_of_holding_the_panel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The notice goes up and the press is over; it used to be awaited where it stood.

    An action handler runs inside the mount's transaction and, under EXCLUSIVE, inside its
    dispatch lock, so awaiting the answer held the whole panel for as long as the reader took
    -- up to two minutes. Nothing here waits, so the editor is simply not open yet.
    """
    panel, opened = _gated_panel(monkeypatch)
    monkeypatch.setattr("squid_discord.native", lambda event: event.responder.interaction)
    monkeypatch.setattr("squid_discord.responder", lambda event: event.responder)
    mount = SimpleNamespace(refresh=AsyncMock())

    await panel._edit_page(cast(Any, _press(mount)))

    assert panel._profile_editor is None
    mount.refresh.assert_not_awaited()

    await opened["on_answer"](cast(Any, None), AccountConsent.grant_current())

    # The press resumes where the reader left it, on the panel's own message.
    assert panel._profile_editor is not None
    cast(AsyncMock, panel._accounts.grant_current_consent).assert_awaited_once()
    mount.refresh.assert_awaited_once()


async def test_declining_leaves_the_panel_exactly_as_it_was(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cancelling stores nothing, changes nothing, and does not redraw anything."""
    panel, opened = _gated_panel(monkeypatch)
    monkeypatch.setattr("squid_discord.native", lambda event: event.responder.interaction)
    monkeypatch.setattr("squid_discord.responder", lambda event: event.responder)
    mount = SimpleNamespace(refresh=AsyncMock())

    await panel._edit_page(cast(Any, _press(mount)))
    await opened["on_answer"](cast(Any, None), None)

    assert panel._profile_editor is None
    assert panel._needs_consent
    cast(AsyncMock, panel._accounts.grant_current_consent).assert_not_awaited()
    mount.refresh.assert_not_awaited()


async def test_a_toggle_needing_consent_still_applies_once_the_reader_agrees(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A toggle carries no `guard=`, so admission-stage confirmation could not have reached it.

    It is also where two of this panel's three consent waits lived, which is why the fix had
    to sit in the handler rather than in the control declaration.
    """
    panel, opened = _gated_panel(monkeypatch)
    monkeypatch.setattr("squid_discord.native", lambda event: event.responder.interaction)
    monkeypatch.setattr("squid_discord.responder", lambda event: event.responder)
    panel._identities = (DISCORD,)
    panel.selected_id = DISCORD.id
    mount = SimpleNamespace(refresh=AsyncMock())

    await panel._toggle_identity(cast(Any, _press(mount)))

    cast(AsyncMock, panel._accounts.set_identity_visibility).assert_not_awaited()

    await opened["on_answer"](cast(Any, None), AccountConsent.grant_current())

    cast(AsyncMock, panel._accounts.set_identity_visibility).assert_awaited_once_with(
        ACCOUNT_ID, DISCORD.id, is_public=True
    )
    mount.refresh.assert_awaited_once()


class _Recorder:
    """A challenge presenter that keeps the question instead of showing it."""

    def __init__(self) -> None:
        self.requests: list[Any] = []

    async def present(self, request: Any) -> None:
        self.requests.append(request)


def _linked_panel() -> tuple[AccountPanel, _Recorder, AsyncMock, sd.Mount]:
    unlink = AsyncMock(return_value=JAVA)
    panel = AccountPanel(
        accounts=cast(Any, SimpleNamespace(unlink_identity=unlink)),
        account_id=ACCOUNT_ID,
        author_id=AUTHOR_ID,
        locale="en",
    )
    panel._profile = AccountProfile.empty(ACCOUNT_ID)
    panel._identities = (DISCORD, JAVA)
    panel.selected_id = JAVA.id
    panel._refresh = AsyncMock()  # type: ignore[method-assign]
    presenter = _Recorder()
    mount = sd.Mount(panel, access=sd.Everyone(), timeout=None, challenge=presenter)
    commit_render(mount)
    return panel, presenter, unlink, mount


async def test_unlinking_asks_before_it_removes_anything() -> None:
    """The armed flag is gone: the button declares that it needs reaffirming.

    What used to be three pieces of view state, an early return and a relabelled button is now
    `guard=sl.guards.confirm(...)`, and the warning is in the question instead of the footer.
    """
    panel, presenter, unlink, mount = _linked_panel()

    await mount.dispatch("unlink", fake_interaction(user_id=AUTHOR_ID))

    unlink.assert_not_awaited()
    assert len(presenter.requests) == 1
    assert presenter.requests[0].key == "unlink"


async def test_agreeing_to_the_question_removes_the_identity() -> None:
    panel, presenter, unlink, mount = _linked_panel()
    await mount.dispatch("unlink", fake_interaction(user_id=AUTHOR_ID))

    await presenter.requests[0].approve()

    unlink.assert_awaited_once_with(ACCOUNT_ID, JAVA.id)


async def test_declining_the_question_removes_nothing() -> None:
    panel, presenter, unlink, mount = _linked_panel()
    await mount.dispatch("unlink", fake_interaction(user_id=AUTHOR_ID))

    await presenter.requests[0].decline()

    unlink.assert_not_awaited()


def test_unlinking_your_own_discord_account_says_what_that_costs() -> None:
    panel, _, _, _ = _linked_panel()
    panel.selected_id = DISCORD.id

    assert "stop recognising you here" in panel._unlink_warning()
