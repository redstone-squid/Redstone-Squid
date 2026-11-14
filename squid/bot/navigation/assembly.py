"""Explicit registration for the bot's independently openable screens."""

from squid.bot.accounts.workspace import AccountWorkspace
from squid.bot.navigation.destinations import AccountDestination, BuildDetail, BuildIndex, DraftDetail, DraftIndex
from squid.bot.navigation.factories import (
    make_account,
    make_build_detail,
    make_build_index,
    make_draft_detail,
    make_draft_index,
)
from squid.bot.navigation.router import ScreenRouter
from squid.bot.submission.build_browse import BuildBrowseScreen
from squid.bot.submission.submit import SavedDraftsScreen
from squid.bot.submission.ui.drafts import DraftEditorScreen


def build_screen_router() -> ScreenRouter:
    router = ScreenRouter()
    router.register(BuildBrowseScreen, destination=BuildIndex, factory=make_build_index)
    router.register(BuildBrowseScreen, destination=BuildDetail, factory=make_build_detail)
    router.register(SavedDraftsScreen, destination=DraftIndex, factory=make_draft_index)
    router.register(DraftEditorScreen, destination=DraftDetail, factory=make_draft_detail)
    router.register(AccountWorkspace, destination=AccountDestination, factory=make_account)
    router.freeze()
    return router
