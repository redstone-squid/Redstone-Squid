"""Private recovery and explicit category selection for retained inferred candidates."""

from typing import TYPE_CHECKING, Any, override
from uuid import UUID

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.submission.ingestion import admit_candidates
from squid.bot.submission.ui import controls as build_controls
from squid.bot.ui import tr
from squid.builds.domain import BuildCategory
from squid.core.errors import SquidError
from squid.runtime import BotServices
from squid.submissions.application.inference_runs import InferenceCandidate
from squid.submissions.application.inferred_drafts import materialize_candidate
from squid_i18n import md_tr

if TYPE_CHECKING:
    from squid.bot.app import RedstoneSquid


class InferenceCandidatesScreen(sd.Screen[None, Any, "RedstoneSquid"]):
    """Review retained candidates and choose missing categories before opening a draft."""

    policy = sd.ResponseSpec(audience="personal", timeout=600)
    selected: str | None = sl.state(None)
    category: str | None = sl.state(None)
    opened: str | None = sl.state(None)
    notice: sl.TextLike = sl.state("")
    page: int = sl.state(0)

    def __init__(self, services: BotServices, run_id: UUID, candidates: tuple[InferenceCandidate, ...]) -> None:
        self.services = services
        self.run_id = run_id
        self.candidates = candidates

    @property
    def visible_candidates(self) -> tuple[InferenceCandidate, ...]:
        """Return the Discord-sized candidate window for the current page."""
        start = self.page * 25
        return self.candidates[start : start + 25]

    @property
    def page_count(self) -> int:
        return max(1, (len(self.candidates) + 24) // 25)

    @override
    def render(self) -> list[sl.LayoutNode[sl.ComponentsV2Target, sd.Request[Any, RedstoneSquid]]]:
        nodes: list[sl.LayoutNode[sl.ComponentsV2Target, sd.Request[Any, RedstoneSquid]]] = [
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
        nodes.append(
            sl.choices(
                *(
                    sl.choice(_candidate_label(number, item), key=str(item.id))
                    for number, item in enumerate(self.visible_candidates, start=self.page * 25 + 1)
                ),
                key="candidate",
                selection=sl.controlled((self.selected,) if self.selected else (), self._select),
            )
        )
        if len(self.candidates) > 25:
            nodes.append(
                sl.action_controls(
                    sl.action_control(
                        tr(t"inference-candidates-previous = Previous candidates"),
                        self._previous,
                        key="previous",
                        available=self.page > 0,
                    ),
                    sl.action_control(
                        tr(t"inference-candidates-next = More candidates"),
                        self._next,
                        key="next",
                        available=self.page + 1 < self.page_count,
                    ),
                    key="candidate_pages",
                )
            )
        current = next((item for item in self.candidates if str(item.id) == self.selected), None)
        if current is not None:
            if current.facts.category is None:
                nodes.append(
                    sl.choices(
                        *(sl.choice(item.value, key=item.value) for item in BuildCategory),
                        key="category",
                        selection=sl.controlled((self.category,) if self.category else (), self._category),
                    )
                )
            nodes.append(
                sl.action_controls(
                    sl.action_control(tr(t"inference-candidates-open = Open candidate draft"), self._open, key="open"),
                    key="actions",
                )
            )
        if self.opened:
            nodes.append(
                sl.primitives.Section(
                    (
                        sl.primitives.Text(
                            tr(t"inference-candidates-saved = Draft saved. Open it to review facts and supplied files.")
                        ),
                    ),
                    sl.primitives.RoutedButton(
                        tr(t"inference-candidates-open-draft = Open draft"),
                        build_controls.reopen_draft.id(draft_id=self.opened),
                    ),
                )
            )
        return nodes

    async def _retry(self, event: sd.PressEvent[RedstoneSquid]) -> None:
        await event.acknowledge()
        actor = await event.context.client.actors.resolve(event.context)
        try:
            candidates = await self.services.submission_inference.resume(self.run_id, actor)
            if candidates is not None:
                self.candidates = candidates
                self.page = min(self.page, self.page_count - 1)
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

    async def _previous(self, event: sl.PressEvent) -> None:
        del event
        if self.page > 0:
            self.page -= 1
            self.selected = None
            self.category = None

    async def _next(self, event: sl.PressEvent) -> None:
        del event
        if self.page + 1 < self.page_count:
            self.page += 1
            self.selected = None
            self.category = None

    async def _open(self, event: sd.PressEvent[RedstoneSquid]) -> None:
        actor = await event.context.client.actors.resolve(event.context)
        candidates = await self.services.submission_inference.get(self.run_id, actor)
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
