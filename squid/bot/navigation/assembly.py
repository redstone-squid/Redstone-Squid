"""Explicit registration for the bot's independently openable screens."""

from squid.bot.accounts.workspace import AccountWorkspace
from squid.bot.navigation.router import ScreenRouter
from squid.bot.submission.build_browse import BuildBrowseScreen
from squid.bot.submission.submit import SavedDraftsScreen
from squid.bot.submission.ui.drafts import DraftEditorScreen


def build_screen_router() -> ScreenRouter:
    router = ScreenRouter()
    router.register(BuildBrowseScreen, SavedDraftsScreen, DraftEditorScreen, AccountWorkspace)
    router.freeze()
    return router
