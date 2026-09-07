"""Private recovery and explicit category selection for retained inferred candidates."""

from uuid import UUID

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.submission.ui.controls import draft_reopen
from squid.bot.utils.permissions import subject_for_interaction
from squid.builds.domain import BuildCategory
from squid.core.errors import SquidError
from squid.runtime import BotServices
from squid.submissions.application.inference_runs import InferenceCandidate
from squid.submissions.application.inferred_drafts import materialize_candidate


class InferenceCandidatesScreen(sd.Screen):
    """Review retained candidates and choose missing categories before opening a draft."""

    audience = "personal"
    timeout = 600
    selected: str | None = sl.state(None)
    category: str | None = sl.state(None)
    opened: str | None = sl.state(None)
    notice: str = sl.state("")

    def __init__(self, services: BotServices, run_id: UUID, candidates: tuple[InferenceCandidate, ...]) -> None:
        self.services = services
        self.run_id = run_id
        self.candidates = candidates

    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target], ...]:
        nodes: list[sl.LayoutNode[sl.ComponentsV2Target]] = [
            sl.section(
                sl.heading("Inferred candidates"),
                sl.paragraph(self.notice or "Choose a candidate. Missing facts and file assignments require review."),
            )
        ]
        if not self.candidates:
            nodes.append(sl.note("No retained candidates are available yet. Reopen this run to refresh."))
            return tuple(nodes)
        nodes.append(
            sl.choices(
                *(
                    sl.choice(
                        f"Candidate {index + 1}: {item.facts.category.value if item.facts.category else 'choose category'}",
                        key=str(item.id),
                    )
                    for index, item in enumerate(self.candidates[:25])
                ),
                key="candidate",
                selection=sl.controlled((self.selected,) if self.selected else (), self._select),
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
                sl.action_controls(sl.action_control("Open candidate draft", self._open, key="open"), key="actions")
            )
        if self.opened:
            nodes.append(
                sl.primitives.Section(
                    (sl.primitives.Text("Draft saved. Open it to review facts and supplied files."),),
                    sl.primitives.RoutedButton("Open draft", draft_reopen.id(draft_id=self.opened)),
                )
            )
        return tuple(nodes)

    async def _select(self, event: sl.ChoiceEvent) -> None:
        self.selected = event.selected[0]
        self.category = None
        self.opened = None

    async def _category(self, event: sl.ChoiceEvent) -> None:
        self.category = event.selected[0]

    async def _open(self, event: sl.PressEvent) -> None:
        actor = await subject_for_interaction(await sd.request(sd.responder(event).interaction))
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
            self.notice = error.public_detail()
        else:
            self.opened = str(draft.snapshot.id)
