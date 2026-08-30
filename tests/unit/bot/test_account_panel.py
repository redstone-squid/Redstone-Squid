"""What `/account` answers with, and to whom."""

from dataclasses import replace
from typing import Any, cast, override
from uuid import UUID

import discord
import pytest
from whenever import Instant

import squid_ui as sl
import squid_ui_discord as sd
import squid_ui_widgets as sp
from squid.accounts.application import AccountService
from squid.accounts.domain import (
    Account,
    AccountConsent,
    AccountIdentity,
    AccountMerge,
    AccountProfile,
    IdentityProvider,
    IdentityRefresh,
    LinkPreview,
    LinkReservation,
    MergePreview,
    ProfileLink,
    ProfileUpdate,
    PublicCreatorProfile,
)
from squid.bot.account_view import AccountScreen
from squid.bot.account_workspace import AccountWorkspace
from squid.bot.verify import VerifyCog
from squid.permissions.domain import PermissionNode
from squid_ui.testing import labels
from squid_ui.text import NEUTRAL, resolve_text
from squid_ui_discord.testing import commit_render, interaction_harness

ACCOUNT_ID = 42
AUTHOR_ID = 555
NOW = Instant.from_utc(2026, 8, 19)
JAVA_UUID = UUID("11111111-1111-1111-1111-111111111111")

DISCORD = replace(AccountIdentity.discord(AUTHOR_ID), id=1, verified_at=NOW)
JAVA = replace(AccountIdentity.java(JAVA_UUID, username="Notch"), id=2, verified_at=NOW)


async def no_consent_request(_event: sl.ActionEvent, _callback: Any) -> None:
    pass


async def no_refresh() -> None:
    pass


class NoticeSource:
    def __init__(self, **values: object) -> None:
        self.values = values
        self.notices: list[object] = []

    async def notice(self, text: object, **_kwargs: object) -> None:
        self.notices.append(text)


class ScheduleRoot:
    def __init__(self) -> None:
        self.scheduled = 0
        self.localization = NEUTRAL

    async def schedule(self) -> None:
        self.scheduled += 1


class EventResponder:
    def __init__(self, message_root: ScheduleRoot) -> None:
        self.interaction = interaction_harness(user_id=AUTHOR_ID).source
        self.message_root = message_root


class PressSource:
    def __init__(self, message_root: ScheduleRoot) -> None:
        self.responder = EventResponder(message_root)
        self.value = True


class TransitionSource:
    def __init__(self, source: NoticeSource) -> None:
        self.source = source


class NoAccountService(AccountService):
    def __init__(self) -> None:
        pass

    @override
    async def get_account_by_identity(self, provider: IdentityProvider, subject: str) -> Account | None:
        del provider, subject
        return None


class PublicProfileService(AccountService):
    def __init__(self, profile: PublicCreatorProfile) -> None:
        self.profile = profile

    @override
    async def get_public_profile(self, public_id: UUID) -> PublicCreatorProfile | None:
        del public_id
        return self.profile


class LinkAccountService(AccountService):
    def __init__(self) -> None:
        self.linked: list[tuple[int, str]] = []
        self.released: list[str] = []
        self.reservation = LinkReservation("held", NOW.add(minutes=5), LinkPreview(JAVA_UUID, "Notch"))

    @override
    async def reserve_minecraft_link(
        self,
        code: str,
        *,
        attempted_by: tuple[IdentityProvider, str],
        ttl_seconds: int = 120,
    ) -> LinkReservation:
        del attempted_by, ttl_seconds
        assert code == "abcd"
        return self.reservation

    @override
    async def link_minecraft_account(
        self,
        account_id: int,
        code: str,
        *,
        consent: AccountConsent,
        attempted_by: tuple[IdentityProvider, str],
        reservation: LinkReservation | None = None,
    ) -> IdentityRefresh:
        del consent, attempted_by
        assert reservation is self.reservation
        self.linked.append((account_id, code))
        return IdentityRefresh(account_id, JAVA_UUID, "Notch")

    @override
    async def release_minecraft_link(self, code: str, reservation: LinkReservation) -> None:
        assert reservation is self.reservation
        self.released.append(code)


class MergeAccountService(AccountService):
    def __init__(self) -> None:
        self.completed: list[tuple[int, str]] = []

    @override
    async def preview_merge(self, surviving_account_id: int, code: str) -> MergePreview:
        del surviving_account_id, code
        return MergePreview(UUID(int=8), ("Notch",), identity_count=2, build_count=3)

    @override
    async def complete_merge(self, surviving_account_id: int, code: str, *, now: Instant | None = None) -> AccountMerge:
        del now
        self.completed.append((surviving_account_id, code))
        return AccountMerge(surviving_account_id, 9, UUID(int=8), UUID(int=9))


class AccountMutationService(AccountService):
    def __init__(self, *, unlinked: AccountIdentity = JAVA) -> None:
        self.unlinked = unlinked
        self.profile_updates: list[tuple[int, ProfileUpdate]] = []
        self.consent_grants: list[int] = []
        self.visibility_writes: list[tuple[int, int, bool]] = []
        self.unlinks: list[tuple[int, int]] = []

    @override
    async def update_profile(self, account_id: int, update: ProfileUpdate) -> AccountProfile:
        self.profile_updates.append((account_id, update))
        return AccountProfile.empty(account_id)

    @override
    async def grant_current_consent(self, account_id: int) -> Account:
        self.consent_grants.append(account_id)
        return Account((), AccountConsent.grant_current(), account_id, NOW)

    @override
    async def set_identity_visibility(self, account_id: int, identity_id: int, *, is_public: bool) -> AccountIdentity:
        self.visibility_writes.append((account_id, identity_id, is_public))
        return DISCORD

    @override
    async def unlink_identity(self, account_id: int, identity_id: int) -> AccountIdentity:
        self.unlinks.append((account_id, identity_id))
        return self.unlinked


def text_of(view: discord.ui.LayoutView) -> str:
    return "\n".join(child.content for child in view.walk_children() if isinstance(child, discord.ui.TextDisplay))


async def test_someone_with_no_account_gets_consent_and_linking_workspace() -> None:
    accounts = NoAccountService()

    async def authorize(_node: PermissionNode) -> bool:
        return False

    workspace = AccountWorkspace(
        accounts=accounts,
        actor_id=AUTHOR_ID,
        account=None,
        request_consent=no_consent_request,
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
    cog.account_service = PublicProfileService(PublicCreatorProfile(public_id=page, hidden=False))

    card = await cog._public_profile_card(page, "Someone")

    assert "Someone" in str(card)


async def test_linking_runs_inside_the_workspace_and_refreshes_it() -> None:
    account = Account((DISCORD,), AccountConsent.grant_current(), ACCOUNT_ID, NOW)
    accounts = LinkAccountService()

    async def authorize(_node: PermissionNode) -> bool:
        return False

    workspace = AccountWorkspace(
        accounts=accounts,
        actor_id=AUTHOR_ID,
        account=account,
        request_consent=no_consent_request,
        can_review_claims=False,
        can_approve_claims=False,
        can_reject_claims=False,
        authorize_claim=authorize,
    )
    workspace._rebuild = no_refresh  # type: ignore[method-assign]
    event = NoticeSource(code="abcd")

    await workspace._link(cast(sl.SubmitEvent, event))

    assert accounts.linked == [(ACCOUNT_ID, "abcd")]
    assert accounts.released == []
    assert len(event.notices) == 1


async def test_merge_requires_the_workspace_decision() -> None:
    account = Account((DISCORD,), AccountConsent.grant_current(), ACCOUNT_ID, NOW)
    accounts = MergeAccountService()

    async def authorize(_node: PermissionNode) -> bool:
        return False

    workspace = AccountWorkspace(
        accounts=accounts,
        actor_id=AUTHOR_ID,
        account=account,
        request_consent=no_consent_request,
        can_review_claims=False,
        can_approve_claims=False,
        can_reject_claims=False,
        authorize_claim=authorize,
    )
    submit = NoticeSource(code="merge-me")

    await workspace._request_merge(cast(sl.SubmitEvent, submit))

    assert accounts.completed == []
    assert workspace._merge_decision is not None
    workspace._rebuild = no_refresh  # type: ignore[method-assign]
    source = NoticeSource()
    await workspace._finish_merge(cast(Any, TransitionSource(source)), "confirm")
    assert accounts.completed == [(ACCOUNT_ID, "merge-me")]


def test_account_is_one_app_only_workspace() -> None:
    cog = cast(Any, VerifyCog)
    assert all(command.name != "account" for command in cog.__cog_commands__)
    assert "account" in {command.name for command in cog.__cog_app_commands__}


def _account_panel(profile: AccountProfile) -> AccountScreen:
    accounts = AccountMutationService()
    panel = AccountScreen(
        accounts=accounts,
        account_id=ACCOUNT_ID,
        actor_id=AUTHOR_ID,
        request_consent=no_consent_request,
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
    panel._refresh = no_refresh  # type: ignore[method-assign]
    component = panel._build_profile_editor()
    panel._profile_editor = component
    editor = cast(sp.Editor, component.machine)
    staged = editor.transition(
        component.machine_state,
        "submit:profile",
        submitted={"display_name": "Builder", "pronouns": None, "bio": "Hello"},
    )
    committed = editor.transition(staged, "save")
    source = NoticeSource()

    assert component.on_change is not None
    await component.on_change(sp.TransitionEvent(cast(Any, source), "save", staged, committed))

    accounts = cast(AccountMutationService, panel._accounts)
    assert len(accounts.profile_updates) == 1
    assert panel._profile_editor is None
    assert len(source.notices) == 1


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
        accounts=AccountMutationService(),
        account_id=ACCOUNT_ID,
        actor_id=AUTHOR_ID,
        request_consent=cast(Any, request),
    )
    panel._profile = AccountProfile.empty(ACCOUNT_ID)
    panel._needs_consent = True
    panel._refresh = no_refresh  # type: ignore[method-assign]

    return panel, opened


def _press(message_root: Any) -> Any:
    """A press double carrying the Discord facts `_with_consent` reads off an event."""
    return PressSource(message_root)


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
    message_root = ScheduleRoot()

    await panel._edit_page(cast(Any, _press(message_root)))

    assert panel._profile_editor is None
    assert message_root.scheduled == 0

    await opened["on_answer"](AccountConsent.grant_current())

    # The press resumes where the reader left it, on the panel's own message.
    assert panel._profile_editor is not None
    assert cast(AccountMutationService, panel._accounts).consent_grants == [ACCOUNT_ID]
    assert message_root.scheduled == 1


async def test_declining_leaves_the_panel_exactly_as_it_was(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cancelling stores nothing, changes nothing, and does not redraw anything."""
    panel, opened = _gated_panel(monkeypatch)
    monkeypatch.setattr("squid_ui_discord.native", lambda event: event.responder.interaction)
    monkeypatch.setattr("squid_ui_discord.responder", lambda event: event.responder)
    message_root = ScheduleRoot()

    await panel._edit_page(cast(Any, _press(message_root)))
    await opened["on_answer"](None)

    assert panel._profile_editor is None
    assert panel._needs_consent
    assert cast(AccountMutationService, panel._accounts).consent_grants == []
    assert message_root.scheduled == 0


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
    message_root = ScheduleRoot()

    await panel._toggle_identity(cast(Any, _press(message_root)))

    accounts = cast(AccountMutationService, panel._accounts)
    assert accounts.visibility_writes == []

    await opened["on_answer"](AccountConsent.grant_current())

    assert accounts.visibility_writes == [(ACCOUNT_ID, DISCORD.id, True)]
    assert message_root.scheduled == 1


class _Recorder:
    """A challenge presenter that keeps the question instead of showing it."""

    def __init__(self) -> None:
        self.requests: list[Any] = []

    async def present(self, request: Any) -> None:
        self.requests.append(request)


def _linked_panel() -> tuple[AccountScreen, _Recorder, AccountMutationService, sd.MessageRoot]:
    accounts = AccountMutationService()
    panel = AccountScreen(
        accounts=accounts,
        account_id=ACCOUNT_ID,
        actor_id=AUTHOR_ID,
        request_consent=no_consent_request,
    )
    panel._profile = AccountProfile.empty(ACCOUNT_ID)
    panel._identities = (DISCORD, JAVA)
    panel.selected_id = JAVA.id
    panel._refresh = no_refresh  # type: ignore[method-assign]
    presenter = _Recorder()
    message_root = sd.MessageRoot(panel, access=sd.Everyone(), timeout=None, challenge=presenter)
    commit_render(message_root)
    return panel, presenter, accounts, message_root


async def test_unlinking_asks_before_it_removes_anything() -> None:
    """The armed flag is gone: the button declares that it needs reaffirming.

    What used to be three pieces of view state, an early return and a relabelled button is now
    `guard=sp.guards.confirm(...)`, and the warning is in the question instead of the footer.
    """
    panel, presenter, accounts, message_root = _linked_panel()

    await message_root.dispatch("unlink", interaction_harness(user_id=AUTHOR_ID))

    assert accounts.unlinks == []
    assert len(presenter.requests) == 1
    assert presenter.requests[0].key == "unlink"


async def test_agreeing_to_the_question_removes_the_identity() -> None:
    panel, presenter, accounts, message_root = _linked_panel()
    await message_root.dispatch("unlink", interaction_harness(user_id=AUTHOR_ID))

    await presenter.requests[0].approve()

    assert accounts.unlinks == [(ACCOUNT_ID, JAVA.id)]


async def test_declining_the_question_removes_nothing() -> None:
    panel, presenter, accounts, message_root = _linked_panel()
    await message_root.dispatch("unlink", interaction_harness(user_id=AUTHOR_ID))

    await presenter.requests[0].decline()

    assert accounts.unlinks == []


def test_unlinking_your_own_discord_account_says_what_that_costs() -> None:
    panel, _, _, _ = _linked_panel()
    panel.selected_id = DISCORD.id

    assert "stop recognising you here" in resolve_text(panel._unlink_warning(), NEUTRAL).content
