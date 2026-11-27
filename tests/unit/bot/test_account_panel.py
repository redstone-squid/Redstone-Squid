"""What `/account` answers with, and to whom."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Any, cast, override
from uuid import UUID

import discord
import pytest
from pytest_mock import MockerFixture
from whenever import Instant

import squid_ui as sl
import squid_ui.ext.discord as sd
import squid_ui_widgets as sp
from squid.bot.accounts.cog import VerifyCog
from squid.bot.accounts.creator_page import CreatorPage
from squid.bot.accounts.overview import AccountOverview, LinkConfirmationAnswer
from squid.bot.accounts.presentation import refresh_message
from squid.bot.accounts.profile_editor import ProfileEditor
from squid.bot.accounts.workspace import AccountTab, AccountWorkspace
from squid.bot.utils.permissions import Grants
from squid.core.accounts.domain import (
    AccountMerge,
    ClaimStatus,
    CreditedAlias,
    IdentityProvider,
    IdentityRefresh,
    LinkPreview,
    LinkReservation,
    MergePreview,
    PrivacyNoticeReceipt,
    ProfileLink,
    PublicCreatorProfile,
)
from squid.core.accounts.errors import AccountNotFoundError
from squid.core.accounts.models import Account, AccountIdentity, AccountProfile, AliasClaim, CreatorAlias
from squid.core.actors import ActingContext, Actor, UserActor
from squid.core.notifications import NotificationPreferences, NotificationService
from squid.core.notifications.application import Inbox, NotificationEditorSnapshot
from squid.core.pagination import Page
from squid.core.permissions.domain import PermissionNode
from squid.core.permissions.domain.catalogue import ACCOUNT_IDENTITY_REFRESH, ACCOUNT_IDENTITY_REFRESH_ANY
from squid_ui.content.text import NEUTRAL, resolve_text, source_text
from squid_ui.ext.discord.testing import commit_render, interaction_harness
from squid_ui.interactions import Visibility
from squid_ui.testing import RecordingResponder, find, keys, labels, press_event, render_tree, submit_event
from tests.support.account_edits import AccountEditRecorder
from tests.support.accounts import FakeAccountRepository, InMemoryAccountService
from tests.support.services import bot_services

ACCOUNT_ID = 42
AUTHOR_ID = 555
NOW = Instant.from_utc(2026, 8, 19)
CONSENTED = PrivacyNoticeReceipt.grant_current()
JAVA_UUID = UUID("11111111-1111-1111-1111-111111111111")

DISCORD = replace(AccountIdentity.discord(AUTHOR_ID), id=1, verified_at=NOW)
JAVA = replace(AccountIdentity.java(JAVA_UUID, username="Notch"), id=2, verified_at=NOW)


async def edit_actor() -> Actor:
    return UserActor(account_id=ACCOUNT_ID, discord_user_id=AUTHOR_ID)


def _seeded(*identities: AccountIdentity, consent: PrivacyNoticeReceipt | None = CONSENTED) -> InMemoryAccountService:
    """The real account service holding the author's account, ACCOUNT_ID."""
    accounts = InMemoryAccountService()
    accounts.repository.accounts[ACCOUNT_ID] = Account(
        identities=identities, consent=consent, id=ACCOUNT_ID, created_at=NOW, public_account_id=UUID(int=ACCOUNT_ID)
    )
    return accounts


def stored(panel: AccountOverview) -> FakeAccountRepository:
    return cast(InMemoryAccountService, panel._accounts).repository


def _workspace(
    *, accounts: InMemoryAccountService, notifications: NotificationService | None = None, **kwargs: Any
) -> AccountWorkspace:
    return AccountWorkspace(
        accounts=accounts,
        notifications=notifications or bot_services().notifications,
        edits=AccountEditRecorder(accounts.repository),
        actor=edit_actor,
        **kwargs,
    )


def _overview(*, accounts: InMemoryAccountService, **kwargs: Any) -> AccountOverview:
    return AccountOverview(
        accounts=accounts, edits=AccountEditRecorder(accounts.repository), actor=edit_actor, **kwargs
    )


async def no_consent_request(
    event: sl.ActionEvent,
    answered: LinkConfirmationAnswer,
    *,
    preview: LinkPreview,
    on_abandon: Callable[[], Awaitable[None]] | None = None,
    timeout: float = 120.0,
) -> bool:
    del event, answered, preview, on_abandon, timeout
    return False


class ConsentCapture:
    """Keep the continuation so tests can accept, cancel, or abandon the notice after opening."""

    def __init__(self, *, opened: bool = True) -> None:
        self.opened = opened
        self.answered: LinkConfirmationAnswer | None = None
        self.preview: LinkPreview | None = None
        self.on_abandon: Callable[[], Awaitable[None]] | None = None
        self.timeout: float | None = None

    async def __call__(
        self,
        event: sl.ActionEvent,
        answered: LinkConfirmationAnswer,
        *,
        preview: LinkPreview,
        on_abandon: Callable[[], Awaitable[None]] | None = None,
        timeout: float = 120.0,
    ) -> bool:
        del event
        self.answered = answered
        self.preview = preview
        self.on_abandon = on_abandon
        self.timeout = timeout
        return self.opened


async def no_refresh(*, changed_account_id: int | None = None) -> None:
    pass


@dataclass
class TextResponder(RecordingResponder):
    """Keeps each notice as given, so tests can compare message objects."""

    texts: list[sl.TextLike] = field(default_factory=list)

    @override
    async def notice(self, text: sl.TextLike, *, visibility: Visibility = Visibility.PRIVATE) -> None:
        self.texts.append(text)
        await super().notice(text, visibility=visibility)


def notice_source(**values: object) -> sd.SubmitEvent:
    return cast(sd.SubmitEvent, submit_event(values, responder=TextResponder()))


def notice_press() -> sd.PressEvent:
    return cast(sd.PressEvent, press_event(responder=TextResponder()))


def notices(event: sl.ActionEventView) -> list[sl.TextLike]:
    return cast(TextResponder, event.responder).texts


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
    def __init__(self, source: sl.ActionEventView) -> None:
        self.source = source


class LinkAccountService(InMemoryAccountService):
    def __init__(self) -> None:
        super().__init__()
        self.linked: list[tuple[int, str]] = []
        self.released: list[str] = []
        self.reservation_ttls: list[int] = []
        self.created_consents: list[PrivacyNoticeReceipt] = []
        self.granted: list[int] = []
        self.reservation = LinkReservation("held", NOW.add(minutes=5), LinkPreview(JAVA_UUID, "Notch"))

    @override
    async def get_or_create_identity(
        self,
        provider: IdentityProvider,
        subject: str,
        *,
        consent: PrivacyNoticeReceipt | None = None,
    ) -> Account:
        assert provider is IdentityProvider.DISCORD
        assert subject == str(AUTHOR_ID)
        assert consent is not None
        self.created_consents.append(consent)
        return Account(identities=(AccountIdentity.discord(AUTHOR_ID),), consent=consent, id=ACCOUNT_ID, created_at=NOW)

    @override
    async def reserve_minecraft_link(
        self,
        code: str,
        *,
        context: ActingContext,
        ttl_seconds: int = 120,
    ) -> LinkReservation:
        assert code == "abcd"
        self.reservation_ttls.append(ttl_seconds)
        return self.reservation

    @override
    async def link_minecraft_account(
        self,
        code: str,
        *,
        consent: PrivacyNoticeReceipt,
        context: ActingContext,
        reservation: LinkReservation | None = None,
    ) -> IdentityRefresh:
        account_id = context.effective.require_account_id()
        del consent
        assert reservation is self.reservation
        self.linked.append((account_id, code))
        return IdentityRefresh(account_id, JAVA_UUID, "Notch")

    @override
    async def release_minecraft_link(self, code: str, reservation: LinkReservation, *, context: ActingContext) -> None:
        assert reservation is self.reservation
        self.released.append(code)


class StaffRefreshService(InMemoryAccountService):
    def __init__(self, target: Account | None, refresh: IdentityRefresh) -> None:
        super().__init__()
        self.target = target
        self.refresh = refresh
        self.identity_reads: list[tuple[IdentityProvider, str]] = []
        self.refreshed: list[int] = []

    @override
    async def get_account_by_identity(self, provider: IdentityProvider, subject: str) -> Account | None:
        self.identity_reads.append((provider, subject))
        return self.target

    @override
    async def refresh_java_identity(
        self, account_id: int, *, context: ActingContext, java_uuid: UUID | None = None
    ) -> IdentityRefresh:
        del java_uuid
        self.refreshed.append(account_id)
        return self.refresh


class MergeAccountService(InMemoryAccountService):
    def __init__(self) -> None:
        super().__init__()
        self.completed: list[tuple[int, str]] = []

    @override
    async def preview_merge(self, code: str, *, context: ActingContext) -> MergePreview:
        surviving_account_id = context.effective.require_account_id()
        del surviving_account_id, code
        return MergePreview(UUID(int=8), ("Notch",), identity_count=2, build_count=3)

    @override
    async def complete_merge(self, code: str, *, context: ActingContext, now: Instant | None = None) -> AccountMerge:
        surviving_account_id = context.effective.require_account_id()
        del now
        self.completed.append((surviving_account_id, code))
        return AccountMerge(surviving_account_id, 9, UUID(int=8), UUID(int=9))


async def test_someone_with_no_account_gets_consent_and_linking_workspace() -> None:
    async def authorize(_node: PermissionNode) -> bool:
        return False

    workspace = _workspace(
        accounts=InMemoryAccountService(),
        actor_id=AUTHOR_ID,
        account=None,
        request_link_confirmation=no_consent_request,
        grants=Grants(frozenset(), authorize),
    )

    await workspace.on_load()

    assert workspace._tabs is not None
    assert {"Overview", "Accounts", "Credits", "Notifications", "Create account"} <= set(
        labels(workspace._tabs.render())
    )


async def test_somebody_elses_creator_page_uses_the_public_profile_projection() -> None:
    with ActingContext.direct(await edit_actor()).scope():
        creator = await CreatorPage.load(_seeded(DISCORD), UUID(int=ACCOUNT_ID), "Someone")

    assert "Someone" in str(creator.render())


def test_a_creator_page_opens_their_builds_by_any_name() -> None:
    profile = PublicCreatorProfile(
        public_id=UUID(int=7),
        hidden=False,
        aliases=(CreditedAlias("Notch", 3), CreditedAlias("Old Name", 1)),
    )

    tree = CreatorPage(profile, "Someone").render()

    assert {"builds", "credited-as", "follow"} <= set(keys(tree))
    assert any("/creators/00000000-0000-0000-0000-000000000007/builds" in str(node) for node in tree)


def test_a_hidden_creator_page_still_opens_their_builds_but_cannot_be_followed() -> None:
    profile = PublicCreatorProfile(public_id=UUID(int=7), hidden=True, aliases=(CreditedAlias("Notch", 3),))

    tree_keys = set(keys(CreatorPage(profile, "Someone").render()))

    assert "builds" in tree_keys
    assert not {"follow", "credited-as"} & tree_keys


async def test_overview_previews_the_public_page_strangers_see() -> None:
    panel = _overview(accounts=_seeded(DISCORD), account_id=ACCOUNT_ID, actor_id=AUTHOR_ID)
    await panel.on_load()

    control = find(render_tree(panel), sl.semantic.RoutedActionControl, key="public_page")
    assert control.route_id == CreatorPage.open.custom_id(public_id=UUID(int=ACCOUNT_ID))


async def test_choose_claim_opens_the_creator_picker(mocker: MockerFixture) -> None:
    workspace = AccountWorkspace.__new__(AccountWorkspace)
    pick = mocker.patch("squid.bot.accounts.workspace.pick", new_callable=mocker.AsyncMock)
    event = notice_press()

    await workspace._choose_claim(event)

    assert pick.await_count == 1
    assert pick.await_args_list[0].args[:2] == (event, "creators")


async def test_linking_runs_inside_the_workspace_and_refreshes_it() -> None:
    account = Account(
        identities=(DISCORD,), consent=PrivacyNoticeReceipt.grant_current(), id=ACCOUNT_ID, created_at=NOW
    )
    accounts = LinkAccountService()
    consent = ConsentCapture()

    async def authorize(_node: PermissionNode) -> bool:
        return False

    workspace = _workspace(
        accounts=accounts,
        actor_id=AUTHOR_ID,
        account=account,
        request_link_confirmation=consent,
        grants=Grants(frozenset(), authorize),
    )
    workspace._rebuild = no_refresh  # type: ignore[method-assign]
    event = notice_source(code="abcd")

    await workspace._link(event)

    assert accounts.linked == []
    assert consent.preview is accounts.reservation.preview
    assert consent.timeout == 120.0
    assert accounts.reservation_ttls == [120]

    assert consent.answered is not None
    await consent.answered(event, PrivacyNoticeReceipt.grant_current())

    assert accounts.linked == [(ACCOUNT_ID, "abcd")]
    assert accounts.released == []
    assert len(notices(event)) == 1
    assert "linked to **Notch**" in source_text(notices(event)[0])


async def test_link_reservation_is_released_once_when_consent_is_abandoned() -> None:
    account = Account(
        identities=(DISCORD,), consent=PrivacyNoticeReceipt.grant_current(), id=ACCOUNT_ID, created_at=NOW
    )
    accounts = LinkAccountService()
    consent = ConsentCapture()

    async def authorize(_node: PermissionNode) -> bool:
        return False

    workspace = _workspace(
        accounts=accounts,
        actor_id=AUTHOR_ID,
        account=account,
        request_link_confirmation=consent,
        grants=Grants(frozenset(), authorize),
    )
    workspace._rebuild = no_refresh  # type: ignore[method-assign]

    await workspace._link(notice_source(code="abcd"))
    assert consent.on_abandon is not None
    await consent.on_abandon()
    await consent.on_abandon()

    assert accounts.linked == []
    assert accounts.released == ["abcd"]


async def test_link_reservation_is_released_when_prompt_is_not_opened() -> None:
    account = Account(
        identities=(DISCORD,), consent=PrivacyNoticeReceipt.grant_current(), id=ACCOUNT_ID, created_at=NOW
    )
    accounts = LinkAccountService()
    consent = ConsentCapture(opened=False)

    async def authorize(_node: PermissionNode) -> bool:
        return False

    workspace = _workspace(
        accounts=accounts,
        actor_id=AUTHOR_ID,
        account=account,
        request_link_confirmation=consent,
        grants=Grants(frozenset(), authorize),
    )

    await workspace._link(notice_source(code="abcd"))

    assert accounts.linked == []
    assert accounts.released == ["abcd"]


async def test_accountless_link_cancel_releases_without_creating_personal_rows() -> None:
    accounts = LinkAccountService()
    consent = ConsentCapture()

    async def authorize(_node: PermissionNode) -> bool:
        return False

    workspace = _workspace(
        accounts=accounts,
        actor_id=AUTHOR_ID,
        account=None,
        request_link_confirmation=consent,
        grants=Grants(frozenset(), authorize),
    )

    assert "Link Minecraft account" in labels(workspace._identity_nodes())
    await workspace._link(notice_source(code="abcd"))
    assert accounts.created_consents == []
    assert consent.on_abandon is not None
    await consent.on_abandon()

    assert accounts.created_consents == []
    assert accounts.linked == []
    assert accounts.released == ["abcd"]


async def test_accountless_link_agreement_creates_the_account_then_consumes_the_hold() -> None:
    accounts = LinkAccountService()
    consent = ConsentCapture()

    async def authorize(_node: PermissionNode) -> bool:
        return False

    workspace = _workspace(
        accounts=accounts,
        actor_id=AUTHOR_ID,
        account=None,
        request_link_confirmation=consent,
        grants=Grants(frozenset(), authorize),
    )
    workspace._rebuild = no_refresh  # type: ignore[method-assign]
    prompt = notice_source()

    await workspace._link(notice_source(code="abcd"))
    granted = PrivacyNoticeReceipt.grant_current()
    assert consent.answered is not None
    await consent.answered(prompt, granted)

    # A repeated callback or late abandonment must not redeem or release again.
    await consent.answered(prompt, granted)
    assert consent.on_abandon is not None
    await consent.on_abandon()

    assert accounts.created_consents == [granted]
    assert accounts.linked == [(ACCOUNT_ID, "abcd")]
    assert accounts.released == []
    assert "linked to **Notch**" in source_text(notices(prompt)[0])


async def test_existing_account_links_without_a_separate_receipt_write() -> None:
    account = Account(
        identities=(DISCORD,), consent=PrivacyNoticeReceipt("older-notice", NOW), id=ACCOUNT_ID, created_at=NOW
    )
    accounts = LinkAccountService()
    consent = ConsentCapture()

    async def authorize(_node: PermissionNode) -> bool:
        return False

    workspace = _workspace(
        accounts=accounts,
        actor_id=AUTHOR_ID,
        account=account,
        request_link_confirmation=consent,
        grants=Grants(frozenset(), authorize),
    )
    workspace._rebuild = no_refresh  # type: ignore[method-assign]

    await workspace._link(notice_source(code="abcd"))
    assert accounts.granted == []
    assert consent.answered is not None
    await consent.answered(notice_source(), PrivacyNoticeReceipt.grant_current())

    assert accounts.granted == []
    assert accounts.linked == [(ACCOUNT_ID, "abcd")]


def test_staff_refresh_picker_is_visible_only_with_the_moderation_permission() -> None:
    account = Account(
        identities=(DISCORD,), consent=PrivacyNoticeReceipt.grant_current(), id=ACCOUNT_ID, created_at=NOW
    )

    async def authorize(_node: PermissionNode) -> bool:
        return True

    def workspace(*, allowed: bool) -> AccountWorkspace:
        return _workspace(
            accounts=LinkAccountService(),
            actor_id=AUTHOR_ID,
            account=account,
            request_link_confirmation=no_consent_request,
            grants=Grants(frozenset({ACCOUNT_IDENTITY_REFRESH_ANY} if allowed else ()), authorize),
        )

    assert "Refresh member" not in labels(workspace(allowed=False)._identity_nodes())
    assert "Refresh member" in labels(workspace(allowed=True)._identity_nodes())


def test_own_refresh_control_is_visible_only_with_the_refresh_permission() -> None:
    account = Account(
        identities=(DISCORD, JAVA), consent=PrivacyNoticeReceipt.grant_current(), id=ACCOUNT_ID, created_at=NOW
    )

    async def authorize(_node: PermissionNode) -> bool:
        return True

    def workspace(*, allowed: bool) -> AccountWorkspace:
        return _workspace(
            accounts=LinkAccountService(),
            actor_id=AUTHOR_ID,
            account=account,
            request_link_confirmation=no_consent_request,
            grants=Grants(frozenset({ACCOUNT_IDENTITY_REFRESH} if allowed else ()), authorize),
        )

    assert "Refresh Minecraft" not in labels(workspace(allowed=False)._identity_nodes())
    assert "Refresh Minecraft" in labels(workspace(allowed=True)._identity_nodes())


@pytest.mark.parametrize("allowed", [False, True], ids=["revoked", "allowed"])
async def test_own_refresh_rechecks_permission_at_the_handler(allowed: bool) -> None:
    account = Account(
        identities=(DISCORD,), consent=PrivacyNoticeReceipt.grant_current(), id=ACCOUNT_ID, created_at=NOW
    )
    refresh = IdentityRefresh(ACCOUNT_ID, JAVA_UUID, "Notch", "Notch")
    accounts = StaffRefreshService(None, refresh)
    authorized: list[PermissionNode] = []

    async def authorize(node: PermissionNode) -> bool:
        authorized.append(node)
        return allowed

    workspace = _workspace(
        accounts=accounts,
        actor_id=AUTHOR_ID,
        account=account,
        request_link_confirmation=no_consent_request,
        grants=Grants(frozenset({ACCOUNT_IDENTITY_REFRESH}), authorize),
    )
    workspace._rebuild = no_refresh  # type: ignore[method-assign]
    event = notice_press()

    await workspace._refresh_identity(event)

    assert authorized == [ACCOUNT_IDENTITY_REFRESH]
    assert accounts.refreshed == ([ACCOUNT_ID] if allowed else [])
    if allowed:
        assert notices(event) == [refresh_message(refresh)]
    else:
        assert len(notices(event)) == 1
        assert "no longer allowed" in source_text(notices(event)[0])


async def test_staff_refresh_rechecks_permission_before_reading_the_member() -> None:
    target = Account(
        identities=(AccountIdentity.discord(999),), consent=PrivacyNoticeReceipt.grant_current(), id=99, created_at=NOW
    )
    accounts = StaffRefreshService(target, IdentityRefresh(99, JAVA_UUID, "Notch"))

    async def authorize(_node: PermissionNode) -> bool:
        return False

    workspace = _workspace(
        accounts=accounts,
        actor_id=AUTHOR_ID,
        account=None,
        request_link_confirmation=no_consent_request,
        grants=Grants(frozenset({ACCOUNT_IDENTITY_REFRESH_ANY}), authorize),
    )
    event = notice_source(user=discord.Object(id=999))

    await workspace._refresh_member_identity(event)

    assert accounts.identity_reads == []
    assert "no longer allowed" in source_text(notices(event)[0])


async def test_staff_refresh_reports_a_named_member_without_an_account() -> None:
    accounts = StaffRefreshService(None, IdentityRefresh(99, JAVA_UUID, "Notch"))

    async def authorize(_node: PermissionNode) -> bool:
        return True

    workspace = _workspace(
        accounts=accounts,
        actor_id=AUTHOR_ID,
        account=None,
        request_link_confirmation=no_consent_request,
        grants=Grants(frozenset({ACCOUNT_IDENTITY_REFRESH_ANY}), authorize),
    )

    with pytest.raises(AccountNotFoundError) as caught:
        await workspace._refresh_member_identity(notice_source(user=discord.Object(id=999)))

    assert caught.value.context["provider"] == IdentityProvider.DISCORD.value
    assert caught.value.context["subject"] == "999"


async def test_staff_refresh_targets_the_selected_account_and_forwards_its_notice() -> None:
    refresh = IdentityRefresh(
        99,
        JAVA_UUID,
        "New",
        "Old",
        contested_alias=CreatorAlias(id=5, name="New", account_id=100),
        opened_claim=AliasClaim(
            id=7, alias_id=5, alias_name="New", account_id=99, status=ClaimStatus.PENDING, created_at=NOW
        ),
    )
    target = Account(
        identities=(AccountIdentity.discord(999),), consent=PrivacyNoticeReceipt.grant_current(), id=99, created_at=NOW
    )
    accounts = StaffRefreshService(target, refresh)

    async def authorize(_node: PermissionNode) -> bool:
        return True

    workspace = _workspace(
        accounts=accounts,
        actor_id=AUTHOR_ID,
        account=None,
        request_link_confirmation=no_consent_request,
        grants=Grants(frozenset({ACCOUNT_IDENTITY_REFRESH_ANY}), authorize),
    )
    workspace._rebuild = no_refresh  # type: ignore[method-assign]
    event = notice_source(user=discord.Object(id=999))

    await workspace._refresh_member_identity(event)

    assert accounts.refreshed == [99]
    assert notices(event) == [refresh_message(refresh)]


async def test_merge_requires_the_workspace_decision() -> None:
    account = Account(
        identities=(DISCORD,), consent=PrivacyNoticeReceipt.grant_current(), id=ACCOUNT_ID, created_at=NOW
    )
    accounts = MergeAccountService()

    async def authorize(_node: PermissionNode) -> bool:
        return False

    workspace = _workspace(
        accounts=accounts,
        actor_id=AUTHOR_ID,
        account=account,
        request_link_confirmation=no_consent_request,
        grants=Grants(frozenset(), authorize),
    )
    submit = notice_source(code="merge-me")

    await workspace._request_merge(submit)

    assert accounts.completed == []
    assert workspace._merge_decision is not None
    workspace._rebuild = no_refresh  # type: ignore[method-assign]
    source = notice_source()
    await workspace._finish_merge(cast(Any, TransitionSource(source)), "confirm")
    assert accounts.completed == [(ACCOUNT_ID, "merge-me")]


def test_account_is_one_app_only_workspace() -> None:
    cog = cast(Any, VerifyCog)
    assert all(command.name != "account" for command in cog.__cog_commands__)
    assert "account" in {command.name for command in cog.__cog_app_commands__}


def _account_panel(profile: AccountProfile) -> AccountOverview:
    panel = _overview(
        accounts=_seeded(DISCORD),
        account_id=ACCOUNT_ID,
        actor_id=AUTHOR_ID,
    )
    panel._profile = profile
    return panel


def test_profile_editor_splits_profile_fields_from_ordered_links() -> None:
    panel = _account_panel(
        AccountProfile(
            account_id=ACCOUNT_ID,
            display_name="Builder",
            bio="Hello",
            links=(ProfileLink("Site", "https://example.com"),),
        )
    )

    draft = ProfileEditor(panel._profile, save=panel._save_profile, close=panel._close_profile_editor)
    component = draft.editor
    editor = cast(sp.Editor, component.machine)
    values = editor.values(component.machine_state)

    assert values["profile"] == {"display_name": "Builder", "pronouns": None, "bio": "Hello"}
    assert tuple(dict(link) for link in cast(tuple, values["links"])) == (
        {"label": "Site", "url": "https://example.com"},
    )


def test_profile_editor_rejects_non_https_links_before_staging() -> None:
    panel = _account_panel(AccountProfile.empty(ACCOUNT_ID))

    draft = ProfileEditor(panel._profile, save=panel._save_profile, close=panel._close_profile_editor)
    issues = draft._validate_link({"label": "Bad", "url": "http://example.com"})

    assert len(issues) == 1
    assert isinstance(issues[0], sl.forms.FormError)


async def test_profile_editor_commit_persists_and_returns_to_account_panel() -> None:
    panel = _account_panel(AccountProfile.empty(ACCOUNT_ID))
    panel._refresh = no_refresh  # type: ignore[method-assign]
    draft = ProfileEditor(panel._profile, save=panel._save_profile, close=panel._close_profile_editor)
    component = draft.editor
    panel._profile_editor = draft
    editor = cast(sp.Editor, component.machine)
    staged = editor.transition(
        component.machine_state,
        "submit:profile",
        submitted={"display_name": "Builder", "pronouns": None, "bio": "Hello"},
    )
    committed = editor.transition(staged, "save")
    source = notice_source()

    assert component.on_change is not None
    with sl.runtime.transaction():
        await component.on_change(sp.TransitionEvent(cast(Any, source), "save", staged, committed))

    assert stored(panel).profiles[ACCOUNT_ID].display_name == "Builder"
    assert panel._profile_editor is None
    assert len(notices(source)) == 1


async def test_profile_opens_without_a_privacy_receipt() -> None:
    panel = _overview(
        accounts=_seeded(DISCORD, consent=None),
        account_id=ACCOUNT_ID,
        actor_id=AUTHOR_ID,
    )
    panel._profile = AccountProfile.empty(ACCOUNT_ID)
    await panel._edit_page(cast(Any, PressSource(ScheduleRoot())))
    assert panel._profile_editor is not None


class _Recorder:
    """A challenge presenter that keeps the question instead of showing it."""

    def __init__(self) -> None:
        self.requests: list[Any] = []

    async def present(self, request: Any) -> None:
        self.requests.append(request)


def _linked_panel() -> tuple[AccountOverview, _Recorder, FakeAccountRepository, sd.MessageRoot]:
    accounts = _seeded(DISCORD, JAVA)
    panel = _overview(
        accounts=accounts,
        account_id=ACCOUNT_ID,
        actor_id=AUTHOR_ID,
    )
    panel._profile = AccountProfile.empty(ACCOUNT_ID)
    panel._identities = (DISCORD, JAVA)
    panel.selected_id = JAVA.id
    panel._refresh = no_refresh  # type: ignore[method-assign]
    presenter = _Recorder()
    message_root = sd.MessageRoot(panel, access=sd.Everyone(), timeout=None, challenge=presenter)
    commit_render(message_root)
    return panel, presenter, accounts.repository, message_root


async def test_unlinking_asks_before_it_removes_anything() -> None:
    """The confirmation guard must intercept unlink before the service receives a write."""
    panel, presenter, accounts, message_root = _linked_panel()

    await message_root.dispatch("unlink", interaction_harness(user_id=AUTHOR_ID))

    assert accounts.accounts[ACCOUNT_ID].identities == (DISCORD, JAVA)
    assert len(presenter.requests) == 1
    assert presenter.requests[0].key == "unlink"


async def test_agreeing_to_the_question_removes_the_identity() -> None:
    panel, presenter, accounts, message_root = _linked_panel()
    await message_root.dispatch("unlink", interaction_harness(user_id=AUTHOR_ID))

    await presenter.requests[0].approve()

    assert accounts.accounts[ACCOUNT_ID].identities == (DISCORD,)


async def test_declining_the_question_removes_nothing() -> None:
    panel, presenter, accounts, message_root = _linked_panel()
    await message_root.dispatch("unlink", interaction_harness(user_id=AUTHOR_ID))

    await presenter.requests[0].decline()

    assert accounts.accounts[ACCOUNT_ID].identities == (DISCORD, JAVA)


def test_unlinking_your_own_discord_account_says_what_that_costs() -> None:
    panel, _, _, _ = _linked_panel()
    panel.selected_id = DISCORD.id

    assert "stop recognising you here" in resolve_text(panel._unlink_warning(), NEUTRAL).content


async def test_link_reservation_is_released_once_when_prompt_delivery_fails() -> None:
    accounts = LinkAccountService()

    class FailedConsent(ConsentCapture):
        @override
        async def __call__(
            self,
            event: sl.ActionEvent,
            answered: LinkConfirmationAnswer,
            *,
            preview: LinkPreview,
            on_abandon: Callable[[], Awaitable[None]] | None = None,
            timeout: float = 120.0,
        ) -> bool:
            await super().__call__(event, answered, preview=preview, on_abandon=on_abandon, timeout=timeout)
            raise RuntimeError("delivery failed")

    consent = FailedConsent()

    async def authorize(_node: PermissionNode) -> bool:
        return False

    workspace = _workspace(
        accounts=accounts,
        actor_id=AUTHOR_ID,
        account=None,
        request_link_confirmation=consent,
        grants=Grants(frozenset(), authorize),
    )
    with pytest.raises(RuntimeError, match="delivery failed"):
        await workspace._link(notice_source(code="abcd"))
    assert consent.on_abandon is not None
    await consent.on_abandon()

    assert accounts.released == ["abcd"]
    assert accounts.created_consents == []
    assert accounts.linked == []


async def test_embedded_overview_opens_profile_editor_directly(mocker: MockerFixture) -> None:
    accounts = _seeded(DISCORD, consent=None)
    account = accounts.repository.accounts[ACCOUNT_ID]

    async def authorize(_node: PermissionNode) -> bool:
        return False

    notifications = mocker.Mock(spec=NotificationService)
    notifications.editor_snapshot.return_value = NotificationEditorSnapshot(NotificationPreferences(ACCOUNT_ID), ())
    notifications.inbox.return_value = Inbox(Page(items=(), total=0, next=None, prev=None), unread_count=0)
    workspace = _workspace(
        accounts=accounts,
        notifications=notifications,
        actor_id=AUTHOR_ID,
        account=account,
        request_link_confirmation=no_consent_request,
        grants=Grants(frozenset(), authorize),
    )
    async with sd.testing.stage(workspace, owner=object(), user_id=AUTHOR_ID) as ui:
        for tab in ("accounts", "credits", "notifications", "overview"):
            await ui.press(next(key for key in ui.root.snapshot().handler_keys if key.endswith(f"account-tabs.{tab}")))
        overview = workspace._overview
        assert isinstance(overview, AccountOverview)
        assert not isinstance(overview, sd.Screen)
        edit_key = next(key for key in ui.root.snapshot().handler_keys if key.endswith("edit_page"))
        await ui.press(edit_key)

        assert overview._profile_editor is not None

        draft = overview._profile_editor
        cancel_key = next(key for key in ui.root.snapshot().handler_keys if key.endswith("cancel-profile-edit"))
        await ui.press(cancel_key)
        assert overview._profile_editor is None
        assert ACCOUNT_ID not in accounts.repository.profiles

        edit_key = next(key for key in ui.root.snapshot().handler_keys if key.endswith("edit_page"))
        await ui.press(edit_key)
        assert overview._profile_editor is not None
        assert overview._profile_editor is not draft
        assert not overview._profile_editor.history.entries
        await ui.press(
            next(key for key in ui.root.snapshot().handler_keys if key.endswith("account-tabs.notifications"))
        )
        notifications.editor_snapshot.assert_awaited_with(context=ActingContext.direct(await edit_actor()))
        assert any("Your inbox is empty" in content for content in ui.texts())


async def test_identity_visibility_needs_a_selected_identity() -> None:
    # Without a selection the toggle would have no real state to show; Unlink stays, greyed out.
    panel, _, _, _ = _linked_panel()
    with sl.runtime.transaction():
        panel.selected_id = None

    tree = render_tree(panel)

    assert "identity_visibility" not in keys(tree)
    assert find(tree, sl.semantic.ActionControl, key="unlink").available is False


def test_linked_account_picker_uses_plain_names_and_visibility() -> None:
    panel = _overview(
        accounts=_seeded(DISCORD),
        account_id=ACCOUNT_ID,
        actor_id=AUTHOR_ID,
        discord_username="redbuilder",
    )
    panel._identities = (DISCORD, replace(JAVA, is_public=False))

    picker = find(render_tree(panel), sl.semantic.Choices, key="identity")

    assert [resolve_text(option.label, NEUTRAL).content for option in picker.choices] == [
        "Discord — redbuilder",
        "Minecraft (Java) — Notch",
    ]
    assert [resolve_text(option.description or "", NEUTRAL).content for option in picker.choices] == [
        "Shown on your profile",
        "Hidden from your profile",
    ]


async def test_account_focus_before_and_after_load_preserves_user_selection(mocker: MockerFixture) -> None:
    async def authorize(_node: PermissionNode) -> bool:
        return False

    accounts = _seeded(DISCORD, consent=None)
    account = accounts.repository.accounts[ACCOUNT_ID]
    notifications = mocker.Mock(spec=NotificationService)
    notifications.editor_snapshot.return_value = NotificationEditorSnapshot(NotificationPreferences(ACCOUNT_ID), ())
    notifications.inbox.return_value = Inbox(Page(items=(), total=0, next=None, prev=None), unread_count=0)
    workspace = _workspace(
        notifications=notifications,
        accounts=accounts,
        actor_id=AUTHOR_ID,
        account=account,
        request_link_confirmation=no_consent_request,
        grants=Grants(frozenset(), authorize),
    )
    focusable: sl.Focusable[AccountTab] = workspace
    assert isinstance(workspace, sl.Focusable)
    assert focusable.focus_targets() == frozenset({"overview", "accounts", "credits", "notifications"})
    with pytest.raises(ValueError, match="Unavailable account tab"):
        focusable.focus("review")
    focusable.focus("accounts")
    focusable.focus("credits")
    async with sd.testing.stage(workspace, owner=object(), user_id=AUTHOR_ID) as ui:
        assert workspace._tabs is not None
        assert workspace._tabs.machine_state.selected == "credits"
        await ui.press(
            next(key for key in ui.root.snapshot().handler_keys if key.endswith("account-tabs.notifications"))
        )
        await workspace._rebuild()
        assert workspace._tabs.machine_state.selected == "notifications"
        del accounts.repository.accounts[ACCOUNT_ID]
        await workspace._rebuild()
        assert workspace._tabs.machine_state.selected == "notifications"
        assert "Create account" in labels(workspace._tabs.render())
        await ui.root.refresh()


@pytest.mark.parametrize("failure", [RuntimeError("redemption failed"), asyncio.CancelledError()])
async def test_link_cleanup_survives_redemption_failure(failure: BaseException, mocker: MockerFixture) -> None:
    accounts = LinkAccountService()
    consent = ConsentCapture()

    async def authorize(_node: PermissionNode) -> bool:
        return False

    workspace = _workspace(
        accounts=accounts,
        actor_id=AUTHOR_ID,
        account=None,
        request_link_confirmation=consent,
        grants=Grants(frozenset(), authorize),
    )
    mocker.patch.object(accounts, "link_minecraft_account", side_effect=failure)
    await workspace._link(notice_source(code="abcd"))
    assert consent.answered is not None
    with pytest.raises(type(failure)):
        await consent.answered(notice_source(), PrivacyNoticeReceipt.grant_current())
    assert consent.on_abandon is not None
    await consent.on_abandon()
    assert accounts.released == ["abcd"]
