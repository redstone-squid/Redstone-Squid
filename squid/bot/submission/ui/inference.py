"""Private recovery and explicit category selection for retained inferred candidates."""

from typing import TYPE_CHECKING, Any, override
from uuid import UUID

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.bot.accounts.consent import ensure_consented_account
from squid.bot.routes import AppRequest
from squid.bot.submission.ingestion import admit_candidates
from squid.bot.submission.ui import controls as build_controls
from squid.bot.submission.ui.drafts import DraftEditorScreen
from squid.bot.ui import tr
from squid.core.actors import ActingContext
from squid.core.builds.domain import BuildCategory
from squid.core.errors import SquidError
from squid.core.submissions.application.inference_runs import InferenceCandidate
from squid.core.submissions.application.inferred_drafts import materialize_candidate
from squid.runtime import BotServices
from squid_i18n import md_tr
from squid_ui.ext.discord import primitives
from squid_ui.ext.discord.target_types import ComponentsV2Target
from squid_ui.selectors import select

if TYPE_CHECKING:
    from squid.bot.app import RedstoneSquid


class InferenceCandidatesScreen(sd.Screen[None, Any, "RedstoneSquid"]):
    """Review retained candidates and choose missing categories before opening a draft."""

    policy = sd.ResponseSpec(audience="personal", timeout=600)
    selected: str | None = sl.state(None)
    category: str | None = sl.state(None)
    opened: str | None = sl.state(None)
    notice: sl.TextLike = sl.state("")

    def __init__(self, services: BotServices, run_id: UUID, candidates: tuple[InferenceCandidate, ...]) -> None:
        self.services = services
        self.run_id = run_id
        self.candidates = candidates

    @override
    def render(self) -> list[sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]]]:
        nodes: list[sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]]] = [
            sl.section(
                sl.heading(tr(t"inference-candidates-heading = Inferred candidates")),
                sl.paragraph(
                    self.notice
                    or tr(
                        t"inference-candidates-hint = Choose a candidate. Missing facts and file assignments "
                        t"require review."
                    )
                ),
            )
        ]
        nodes.append(
            sl.action_controls(
                sl.action_control(
                    tr(t"inference-candidates-retry = Retry retained inference"), self._retry, key="retry"
                ),
                key="recovery",
            )
        )
        if not self.candidates:
            nodes.append(
                sl.note(
                    tr(
                        t"inference-candidates-empty = No retained candidates are available yet. "
                        t"Reopen this run to refresh."
                    )
                )
            )
            return nodes
        candidates = self.candidates
        selected = self.selected
        windows: list[sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]]] = []
        for start in range(0, len(candidates), 25):
            shown = candidates[start : start + 25]
            windows.append(
                sl.choices(
                    *(
                        sl.choice(_candidate_label(index, item), key=str(item.id))
                        for index, item in enumerate(shown, start + 1)
                    ),
                    key=f"candidate-{start}",
                    selection=(selected,)
                    if selected is not None and any(str(item.id) == selected for item in shown)
                    else (),
                    on_change=self._select,
                ).unbreakable()
            )
        nodes.append(sl.region(*windows, key="candidates", title=""))
        current = next((item for item in self.candidates if str(item.id) == self.selected), None)
        details: list[sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]]] = []
        if current is not None:
            if current.facts.category is None:
                details.append(
                    sl.choices(
                        *(sl.choice(item.value, key=item.value) for item in BuildCategory),
                        key="category",
                        selection=(self.category,) if self.category else (),
                        on_change=self._category,
                    )
                )
            details.append(
                sl.action_controls(
                    sl.action_control(tr(t"inference-candidates-open = Open candidate draft"), self._open, key="open"),
                    key="actions",
                )
            )

        def visible_details(
            ctx: sl.LayoutContext,
        ) -> tuple[sl.LayoutNode[ComponentsV2Target, sd.Request[Any, RedstoneSquid]], ...]:
            visible = ctx.visible.query(select(sl.semantic.Choices)).all()
            return (
                tuple(details)
                if selected is not None
                and any(choice.key == selected for match in visible for choice in match.node.choices)
                else ()
            )

        nodes.append(sl.context(visible_details, key="candidate-details"))
        if self.opened:
            nodes.append(
                primitives.Section(
                    (
                        primitives.Text(
                            tr(t"inference-candidates-saved = Draft saved. Open it to review facts and supplied files.")
                        ),
                    ),
                    primitives.RoutedButton(
                        tr(t"inference-candidates-open-draft = Open draft"),
                        DraftEditorScreen.open_draft.custom_id(draft_id=UUID(self.opened)),
                    ),
                )
            )
        return nodes

    async def _retry(self, event: sd.PressEvent[RedstoneSquid]) -> None:
        event.context.client.command_limits.check(event.context.user.id, cost=4)
        await event.acknowledge()
        actor = await event.context.client.actors.resolve(event.context)
        try:
            candidates = await self.services.submission_inference.resume(
                self.run_id, context=ActingContext.for_actor(actor)
            )
            if candidates is not None:
                self.candidates = candidates
                await admit_candidates(self.services, candidates, actor=actor)
                self.notice = tr(
                    t"inference-candidates-recovered = Retained inference recovered. Open a candidate to continue."
                )
        except SquidError as error:
            self.notice = error.public_text()

    async def _select(self, event: sl.ChoiceEvent) -> None:
        self.selected = event.selected[0]
        self.category = None
        self.opened = None

    async def _category(self, event: sl.ChoiceEvent) -> None:
        self.category = event.selected[0]

    async def _open(self, event: sd.PressEvent[RedstoneSquid]) -> None:
        actor = await event.context.client.actors.resolve(event.context)
        candidates = await self.services.submission_inference.get(self.run_id, context=ActingContext.for_actor(actor))
        current = next((item for item in candidates if str(item.id) == self.selected), None)
        if current is None:
            return
        try:
            draft = await materialize_candidate(
                current,
                self.services.submission_drafts,
                self.services.submission_forms,
                category=BuildCategory(self.category) if self.category else None,
                actor=actor,
            )
        except SquidError as error:
            self.notice = error.public_text()
        else:
            self.opened = str(draft.snapshot.id)


def _candidate_label(number: int, candidate: InferenceCandidate) -> sl.TextLike:
    if candidate.facts.category is None:
        return md_tr(t"inference-candidate-uncategorized = Candidate {number}: choose a category")
    category = candidate.facts.category.value
    return md_tr(t"inference-candidate = Candidate {number}: {category}")


@build_controls.builds.route("/inference/{run_id}/reopen")
async def reopen_inference(request: AppRequest, run_id: UUID) -> InferenceCandidatesScreen | None:
    """Recover candidates under current consent and owner or staff authority."""
    if await ensure_consented_account(request, request.client.services.accounts) is None:
        return None
    actor = await request.client.actors.resolve(request)
    candidates = await request.client.services.submission_inference.get(run_id, context=ActingContext.for_actor(actor))
    return InferenceCandidatesScreen(request.client.services, run_id, candidates)
