"""List retained submission drafts and inferred build candidates."""

from collections.abc import Sequence
from typing import Self, override

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.bot.accounts.consent import ensure_consented_account
from squid.bot.routes import AppRequest, AppRoutes, ScreenUnavailable
from squid.bot.submission.ui.drafts import DraftEditorScreen
from squid.bot.submission.ui.inference import reopen_inference
from squid.core.actors import ActingContext
from squid.core.i18n import tr
from squid.core.pagination import FIRST_PAGE, PageSelector
from squid.core.submissions.application import StoredDraft
from squid.core.submissions.domain import (
    DraftStatus,
)
from squid_i18n import Text, md_tr
from squid_ui.content.text import md
from squid_ui.ext.discord import primitives
from squid_ui.ext.discord.target_types import ComponentsV2Target

router = AppRoutes()


class SavedDraftsScreen(sd.Screen):
    """Keep saved drafts and inference runs reachable across Discord message pages."""

    def __init__(self, nodes: Sequence[sl.LayoutNode[ComponentsV2Target, sd.Request]]) -> None:
        self.nodes = tuple(nodes)

    @router.route("/drafts")
    @classmethod
    async def open_drafts(cls, request: AppRequest, inbox: bool = False) -> Self | None:
        """List the opener's drafts, or the staff correction inbox when `inbox` is set."""
        account_id = await ensure_consented_account(request, request.client.services.accounts)
        if account_id is None:
            return None
        context = ActingContext.for_actor(await request.client.actors.resolve(request))
        services = request.client.services
        if inbox:
            # The screen pages locally, so it needs the whole queue rather than the first API page.
            drafts: list[StoredDraft] = []
            selector: PageSelector | None = FIRST_PAGE
            while selector is not None:
                page = await services.submission_drafts.attention_inbox(
                    selector=selector, page_size=50, context=context
                )
                drafts.extend(page.items)
                selector = page.next.selector() if page.next is not None else None
        else:
            drafts = list(await services.submission_drafts.list_active(context=context))
        runs = await services.submission_inference.list_active(inbox=inbox, context=context)
        nodes: list[sl.LayoutNode[ComponentsV2Target, sd.Request]] = [
            primitives.Section(
                (primitives.Text(_draft_line(draft)),),
                primitives.RoutedButton(
                    tr(t"build-drafts-open = Open"),
                    DraftEditorScreen.open_draft.custom_id(draft_id=draft.snapshot.id),
                ),
            )
            for draft in drafts
        ]
        nodes.extend(
            primitives.Section(
                (primitives.Text(md_tr(t"build-drafts-detected = **Builds found in your build-log posts**")),),
                primitives.RoutedButton(tr(t"build-drafts-review = Review"), reopen_inference.custom_id(run_id=run_id)),
            )
            for run_id in runs
        )
        if nodes:
            return cls(nodes)
        if inbox:
            raise ScreenUnavailable(tr(t"build-drafts-inbox-empty = No drafts are waiting for staff correction."))
        raise ScreenUnavailable(
            md_tr(t"build-drafts-empty = You have no drafts in progress. Start one with `/build submit`.")
        )

    @override
    def render(self) -> sl.Document[ComponentsV2Target, sd.Request]:
        return sl.Document(self.nodes, key="saved-drafts")


def _draft_line(draft: StoredDraft) -> Text:
    """Describe a draft in the /build drafts list by category, a name, its state, and last edit."""
    answers = draft.snapshot.answers
    creators = answers.get("creators")
    name = answers.get("display_name") or (", ".join(map(str, creators)) if isinstance(creators, list) else None)
    category = draft.snapshot.category.capitalize()
    title = md(t"**{category}** · {name}") if name else md(t"**{category}**")
    match draft.snapshot.status:
        case DraftStatus.PROCESSING:
            state = tr(t"build-drafts-state-creating = Creating the build")
        case DraftStatus.NEEDS_ATTENTION if draft.preparation_retry_at is not None:
            state = tr(t"build-drafts-state-waiting = Waiting for files")
        case DraftStatus.NEEDS_ATTENTION:
            state = tr(t"build-drafts-state-needs-changes = Needs changes")
        case _:
            state = tr(t"build-drafts-state-editing = In progress")
    updated = draft.updated_at.to_stdlib()
    edited = md_tr(t"build-drafts-edited = Edited {updated}")
    return Text(t"{title}\n{state} · {edited}")
