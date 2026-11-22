"""Navigation controls reference the same endpoint as typed calls and address dispatch."""

from uuid import UUID

import pytest

from squid.bot.accounts.workspace import AccountWorkspace
from squid.bot.catalogue.browse import BuildBrowseScreen
from squid.bot.routes import AppRequest
from squid.bot.submission.ui.drafts import DraftEditorScreen
from squid.bot.submission.ui.saved_drafts import SavedDraftsScreen
from squid_ui.errors import LayoutInvariantError
from squid_ui.ext.discord import primitives
from squid_ui.routing import MountedRouter


@pytest.fixture
def router() -> MountedRouter[AppRequest]:
    routes = MountedRouter[AppRequest]()
    routes.mount(
        "builds",
        BuildBrowseScreen.open_index,
        BuildBrowseScreen.open_detail,
        DraftEditorScreen.open_draft,
        SavedDraftsScreen.open_drafts,
    )
    routes.mount("accounts", AccountWorkspace.open_account)
    return routes


@pytest.mark.parametrize("address", ["/builds", "/builds/12", "/drafts", "/drafts?inbox=true", "/account#claims"])
def test_screen_addresses_roundtrip_through_the_application_registry(
    router: MountedRouter[AppRequest], address: str
) -> None:
    match = router.resolve(address)
    assert match.endpoint.address(**match.arguments) == address


def test_persistent_button_addresses_the_screen_directly(router: MountedRouter[AppRequest]) -> None:
    button = primitives.RoutedButton("Open", DraftEditorScreen.open_draft.custom_id(draft_id=UUID(int=1)))
    assert button.route_id == "ui:/drafts/00000000-0000-0000-0000-000000000001"
    assert router.resolve(button.route_id[3:]).endpoint is DraftEditorScreen.open_draft
    with pytest.raises(LayoutInvariantError, match="100"):
        BuildBrowseScreen.open_detail.custom_id(build_id=10**100)
