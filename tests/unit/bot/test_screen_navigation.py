"""Navigation controls reference the same endpoint as typed calls and address dispatch."""

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

import squid_ui as sl
from squid.bot.accounts.workspace import AccountWorkspace
from squid.bot.routes import ScreenUnavailable, router
from squid.bot.submission.build_browse import BuildBrowseScreen
from squid.bot.submission.submit import SavedDraftsScreen
from squid.bot.submission.ui import drafts
from squid.bot.submission.ui.drafts import DraftEditorScreen
from squid.builds.domain import Status
from squid.submissions.errors import DraftAccessDeniedError, DraftNotFoundError
from squid_ui.errors import LayoutInvariantError


@pytest.mark.parametrize("address", ["/builds", "/builds/12", "/drafts", "/drafts?inbox=true", "/account#claims"])
def test_screen_addresses_roundtrip_through_the_application_registry(address: str) -> None:
    match = router.resolve(address)
    assert match.endpoint.address(**match.arguments) == address


def test_every_screen_endpoint_belongs_to_the_application_router() -> None:
    for endpoint in (
        BuildBrowseScreen.open_index,
        SavedDraftsScreen.open_drafts,
        DraftEditorScreen.open_draft,
        AccountWorkspace.open_account,
    ):
        assert endpoint.router is router
        assert endpoint in router.endpoints()


def test_persistent_button_addresses_the_screen_directly() -> None:
    button = sl.primitives.RoutedButton("Open", DraftEditorScreen.open_draft.custom_id(draft_id=UUID(int=1)))
    assert button.route_id == "ui:/drafts/00000000-0000-0000-0000-000000000001"
    assert router.resolve(button.route_id[3:]).endpoint is DraftEditorScreen.open_draft
    with pytest.raises(LayoutInvariantError, match="100"):
        BuildBrowseScreen.open_detail.custom_id(build_id=10**100)


@pytest.mark.parametrize("status", [None, Status.PENDING, Status.CONFIRMED])
@pytest.mark.parametrize("view_pending", [False, True])
async def test_build_visibility_is_checked_before_opening(
    monkeypatch: pytest.MonkeyPatch,
    status: Status | None,
    view_pending: bool,
) -> None:
    request = MagicMock()
    screen = MagicMock(_capabilities=MagicMock(view_pending=view_pending))
    build = None if status is None else MagicMock(submission_status=status)
    request.client.services.build_queries.get = AsyncMock(return_value=build)
    monkeypatch.setattr(BuildBrowseScreen, "create", AsyncMock(return_value=screen))
    allowed = status is Status.CONFIRMED or (status is Status.PENDING and view_pending)
    if allowed:
        opening = await BuildBrowseScreen.open_detail.function(BuildBrowseScreen, request, 12)
        assert opening is not None
        assert opening.screen is screen
    else:
        with pytest.raises(ScreenUnavailable):
            await BuildBrowseScreen.open_detail.function(BuildBrowseScreen, request, 12)


async def test_draft_consent_stops_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(drafts, "ensure_consented_account", AsyncMock(return_value=None))
    editor = AsyncMock()
    monkeypatch.setattr(drafts, "draft_editor", editor)
    assert await DraftEditorScreen.open_draft.function(DraftEditorScreen, MagicMock(), UUID(int=1)) is None
    editor.assert_not_awaited()


@pytest.mark.parametrize("error", [DraftNotFoundError(UUID(int=1)), DraftAccessDeniedError()])
async def test_draft_access_failure_does_not_open_editor(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    monkeypatch.setattr(drafts, "ensure_consented_account", AsyncMock(return_value=1))
    monkeypatch.setattr(drafts, "draft_editor", AsyncMock(side_effect=error))
    with pytest.raises(ScreenUnavailable):
        await DraftEditorScreen.open_draft.function(DraftEditorScreen, MagicMock(), UUID(int=1))


async def test_account_tab_validation_and_current_request(monkeypatch: pytest.MonkeyPatch) -> None:
    screen = MagicMock()
    screen.focus.side_effect = [ValueError("Unavailable tab"), None]
    create = AsyncMock(return_value=screen)
    monkeypatch.setattr(AccountWorkspace, "create", create)
    request = MagicMock()
    with pytest.raises(ScreenUnavailable):
        await AccountWorkspace.open_account.function(AccountWorkspace, request, "review")
    opening = await AccountWorkspace.open_account.function(AccountWorkspace, request, "identity")
    assert opening is not None
    assert opening.screen is screen
    screen.focus.assert_called_with("identity")
    create.assert_awaited_with(request)
