"""What `/account` answers with, and to whom."""

from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import UUID

import discord
import pytest
from whenever import Instant

import squid_ui as sl
import squid_ui_discord as sd
import squid_ui_widgets as sp
from squid.accounts.domain import (
    Account,
    AccountConsent,
    AccountIdentity,
    AccountProfile,
    ProfileLink,
    PublicCreatorProfile,
)
from squid.bot.account_view import AccountScreen
from squid.bot.account_workspace import AccountWorkspace
from squid.bot.verify import VerifyCog
from squid.permissions.domain import PermissionNode
from squid_ui.testing import labels
from squid_ui.text import NEUTRAL, resolve_text
from squid_ui_discord.testing import commit_render, fake_interaction

ACCOUNT_ID = 42
AUTHOR_ID = 555
NOW = Instant.from_utc(2026, 8, 19)
JAVA_UUID = UUID("11111111-1111-1111-1111-111111111111")

DISCORD = replace(AccountIdentity.discord(AUTHOR_ID), id=1, verified_at=NOW)
JAVA = replace(AccountIdentity.java(JAVA_UUID, username="Notch"), id=2, verified_at=NOW)


def text_of(view: discord.ui.LayoutView) -> str:
    return "\n".join(child.content for child in view.walk_children() if isinstance(child, discord.ui.TextDisplay))


async def test_someone_with_no_account_gets_consent_and_linking_workspace() -> None:
    accounts = SimpleNamespace(get_account_by_identity=AsyncMock(return_value=None))

    async def authorize(_node: PermissionNode) -> bool:
        return False

    workspace = AccountWorkspace(
        accounts=cast(Any, accounts),
        actor_id=AUTHOR_ID,
        account=None,
        request_consent=AsyncMock(),
        can_review_claims=False,
        can_approve_claims=False,
        can_reject_claims=False,
        authorize_claim=authorize,
    )

    await workspace.on_load()

    assert workspace._tabs is not None
    assert {"Link and refresh", "Review privacy notice"} <= set(labels(workspace._tabs.render()))


async def test_somebody_elses_creator_page_uses_the_public_profile_projection() -> None:
    page = UUID(int=7)
    cog = VerifyCog.__new__(VerifyCog)
    cog.account_service = cast(
        Any,
        SimpleNamespace(get_public_profile=AsyncMock(return_value=PublicCreatorProfile(public_id=page, hidden=False))),
    )

    card = await cog._public_profile_card(page, "Someone")

    assert "Someone" in str(card)


async def test_linking_runs_inside_the_workspace_and_refreshes_it() -> None:
    account = Account((DISCORD,), AccountConsent.grant_current(), ACCOUNT_ID, NOW)
    refresh = SimpleNamespace(current_name="Notch")
    accounts = SimpleNamespace(
        reserve_minecraft_link=AsyncMock(return_value=SimpleNamespace()),
        link_minecraft_account=AsyncMock(return_value=refresh),
        release_minecraft_link=AsyncMock(),
    )

    async def authorize(_node: PermissionNode) -> bool:
        return False

    workspace = AccountWorkspace(
        accounts=cast(Any, accounts),
        actor_id=AUTHOR_ID,
        account=account,
        request_consent=AsyncMock(),
        can_review_claims=False,
        can_approve_claims=False,
        can_reject_claims=False,
        authorize_claim=authorize,
    )
    workspace._rebuild = AsyncMock()  # type: ignore[method-assign]
    event = SimpleNamespace(values={"code": "abcd"}, notice=AsyncMock())

    await workspace._link(cast(sl.SubmitEvent, event))

    accounts.link_minecraft_account.assert_awaited_once()
    accounts.release_minecraft_link.assert_not_awaited()
    event.notice.assert_awaited_once()


async def test_merge_requires_the_workspace_decision() -> None:
    account = Account((DISCORD,), AccountConsent.grant_current(), ACCOUNT_ID, NOW)
    accounts = SimpleNamespace(
        preview_merge=AsyncMock(return_value=SimpleNamespace(alias_names=("Notch",), identity_count=2, build_count=3)),
        complete_merge=AsyncMock(return_value=SimpleNamespace(redirected_public_creator_id=UUID(int=9))),
    )

    async def authorize(_node: PermissionNode) -> bool:
        return False

    workspace = AccountWorkspace(
        accounts=cast(Any, accounts),
        actor_id=AUTHOR_ID,
        account=account,
        request_consent=AsyncMock(),
        can_review_claims=False,
        can_approve_claims=False,
        can_reject_claims=False,
        authorize_claim=authorize,
    )
    submit = SimpleNamespace(values={"code": "merge-me"})

    await workspace._request_merge(cast(sl.SubmitEvent, submit))

    accounts.complete_merge.assert_not_awaited()
    assert workspace._merge_decision is not None
    workspace._rebuild = AsyncMock()  # type: ignore[method-assign]
    source = SimpleNamespace(notice=AsyncMock())
    await workspace._finish_merge(cast(Any, SimpleNamespace(source=source)), "confirm")
    accounts.complete_merge.assert_awaited_once_with(ACCOUNT_ID, "merge-me")


def test_account_is_one_app_only_workspace() -> None:
    cog = cast(Any, VerifyCog)
    assert all(command.name != "account" for command in cog.__cog_commands__)
    assert "account" in {command.name for command in cog.__cog_app_commands__}


def _account_panel(profile: AccountProfile) -> AccountScreen:
    panel = AccountScreen(
        accounts=cast(Any, SimpleNamespace(update_profile=AsyncMock())),
        account_id=ACCOUNT_ID,
        actor_id=AUTHOR_ID,
        request_consent=AsyncMock(),
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
    editor = cast(sp.Editor, component.machine)
    values = editor.values(component.machine_state)

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
    editor = cast(sp.Editor, component.machine)
    staged = editor.transition(
        component.machine_state,
        "submit:profile",
        submitted={"display_name": "Builder", "pronouns": None, "bio": "Hello"},
    )
    committed = editor.transition(staged, "save")
    source = SimpleNamespace(notice=AsyncMock())

    assert component.on_change is not None
    await component.on_change(sp.TransitionEvent(cast(Any, source), "save", staged, committed))

    cast(AsyncMock, panel._accounts.update_profile).assert_awaited_once()
    assert panel._profile_editor is None
    source.notice.assert_awaited_once()


def _gated_panel(monkeypatch: pytest.MonkeyPatch) -> tuple[AccountScreen, dict[str, Any]]:
    """A panel whose reader has not consented, with the notice stubbed out.

    The prompt is a mount of its own and is covered in `test_consent_gate`; what matters here
    is that the press ends without one being awaited, and that the continuation does the work.
    """
    del monkeypatch
    opened: dict[str, Any] = {}

    async def request(event: sl.ActionEvent, on_answer: Any) -> None:
        async def answer(consent: AccountConsent | None) -> None:
            await on_answer(consent)
            if consent is not None:
                await cast(Any, event).responder.message_root.schedule()

        opened["on_answer"] = answer

    panel = AccountScreen(
        accounts=cast(
            Any,
            SimpleNamespace(
                update_profile=AsyncMock(),
                set_identity_visibility=AsyncMock(),
                grant_current_consent=AsyncMock(),
            ),
        ),
        account_id=ACCOUNT_ID,
        actor_id=AUTHOR_ID,
        request_consent=cast(Any, request),
    )
    panel._profile = AccountProfile.empty(ACCOUNT_ID)
    panel._needs_consent = True
    panel._refresh = AsyncMock()  # type: ignore[method-assign]

    return panel, opened


def _press(message_root: Any) -> Any:
    """A press double carrying the Discord facts `_with_consent` reads off an event."""
    responder = SimpleNamespace(
        interaction=SimpleNamespace(user=SimpleNamespace(id=AUTHOR_ID)), message_root=message_root
    )
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
    monkeypatch.setattr("squid_ui_discord.native", lambda event: event.responder.interaction)
    monkeypatch.setattr("squid_ui_discord.responder", lambda event: event.responder)
    message_root = SimpleNamespace(schedule=AsyncMock(), localization=NEUTRAL)

    await panel._edit_page(cast(Any, _press(message_root)))

    assert panel._profile_editor is None
    message_root.schedule.assert_not_awaited()

    await opened["on_answer"](AccountConsent.grant_current())

    # The press resumes where the reader left it, on the panel's own message.
    assert panel._profile_editor is not None
    cast(AsyncMock, panel._accounts.grant_current_consent).assert_awaited_once()
    message_root.schedule.assert_awaited_once()


async def test_declining_leaves_the_panel_exactly_as_it_was(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cancelling stores nothing, changes nothing, and does not redraw anything."""
    panel, opened = _gated_panel(monkeypatch)
    monkeypatch.setattr("squid_ui_discord.native", lambda event: event.responder.interaction)
    monkeypatch.setattr("squid_ui_discord.responder", lambda event: event.responder)
    message_root = SimpleNamespace(schedule=AsyncMock(), localization=NEUTRAL)

    await panel._edit_page(cast(Any, _press(message_root)))
    await opened["on_answer"](None)

    assert panel._profile_editor is None
    assert panel._needs_consent
    cast(AsyncMock, panel._accounts.grant_current_consent).assert_not_awaited()
    message_root.schedule.assert_not_awaited()


async def test_a_toggle_needing_consent_still_applies_once_the_reader_agrees(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A toggle carries no `guard=`, so admission-stage confirmation could not have reached it.

    It is also where two of this panel's three consent waits lived, which is why the fix had
    to sit in the handler rather than in the control declaration.
    """
    panel, opened = _gated_panel(monkeypatch)
    monkeypatch.setattr("squid_ui_discord.native", lambda event: event.responder.interaction)
    monkeypatch.setattr("squid_ui_discord.responder", lambda event: event.responder)
    panel._identities = (DISCORD,)
    panel.selected_id = DISCORD.id
    message_root = SimpleNamespace(schedule=AsyncMock(), localization=NEUTRAL)

    await panel._toggle_identity(cast(Any, _press(message_root)))

    cast(AsyncMock, panel._accounts.set_identity_visibility).assert_not_awaited()

    await opened["on_answer"](AccountConsent.grant_current())

    cast(AsyncMock, panel._accounts.set_identity_visibility).assert_awaited_once_with(
        ACCOUNT_ID, DISCORD.id, is_public=True
    )
    message_root.schedule.assert_awaited_once()


class _Recorder:
    """A challenge presenter that keeps the question instead of showing it."""

    def __init__(self) -> None:
        self.requests: list[Any] = []

    async def present(self, request: Any) -> None:
        self.requests.append(request)


def _linked_panel() -> tuple[AccountScreen, _Recorder, AsyncMock, sd.MessageRoot]:
    unlink = AsyncMock(return_value=JAVA)
    panel = AccountScreen(
        accounts=cast(Any, SimpleNamespace(unlink_identity=unlink)),
        account_id=ACCOUNT_ID,
        actor_id=AUTHOR_ID,
        request_consent=AsyncMock(),
    )
    panel._profile = AccountProfile.empty(ACCOUNT_ID)
    panel._identities = (DISCORD, JAVA)
    panel.selected_id = JAVA.id
    panel._refresh = AsyncMock()  # type: ignore[method-assign]
    presenter = _Recorder()
    message_root = sd.MessageRoot(panel, access=sd.Everyone(), timeout=None, challenge=presenter)
    commit_render(message_root)
    return panel, presenter, unlink, message_root


async def test_unlinking_asks_before_it_removes_anything() -> None:
    """The armed flag is gone: the button declares that it needs reaffirming.

    What used to be three pieces of view state, an early return and a relabelled button is now
    `guard=sp.guards.confirm(...)`, and the warning is in the question instead of the footer.
    """
    panel, presenter, unlink, message_root = _linked_panel()

    await message_root.dispatch("unlink", fake_interaction(user_id=AUTHOR_ID))

    unlink.assert_not_awaited()
    assert len(presenter.requests) == 1
    assert presenter.requests[0].key == "unlink"


async def test_agreeing_to_the_question_removes_the_identity() -> None:
    panel, presenter, unlink, message_root = _linked_panel()
    await message_root.dispatch("unlink", fake_interaction(user_id=AUTHOR_ID))

    await presenter.requests[0].approve()

    unlink.assert_awaited_once_with(ACCOUNT_ID, JAVA.id)


async def test_declining_the_question_removes_nothing() -> None:
    panel, presenter, unlink, message_root = _linked_panel()
    await message_root.dispatch("unlink", fake_interaction(user_id=AUTHOR_ID))

    await presenter.requests[0].decline()

    unlink.assert_not_awaited()


def test_unlinking_your_own_discord_account_says_what_that_costs() -> None:
    panel, _, _, _ = _linked_panel()
    panel.selected_id = DISCORD.id

    assert "stop recognising you here" in resolve_text(panel._unlink_warning(), NEUTRAL).content
