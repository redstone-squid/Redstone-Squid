"""A disposable build editor whose second message follows shared reactive state."""

import re
from dataclasses import dataclass, replace
from functools import partial
from typing import override

import anyio

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.layout_inspector import InspectablePanel
from squid.bot.ui import DISCORD_BLUE, DISCORD_GREEN, tr

_TAGS = (
    "Flush",
    "Seamless",
    "Compact",
    "Expandable",
    "Tileable",
    "Fast",
    "Silent",
    "Hidden input",
    "Single input",
    "Double piston extender",
    "Triple piston extender",
    "Flying machine",
    "Observer based",
    "Quasi-connectivity",
    "Zero tick",
    "Slime",
    "Honey",
    "Glass",
    "Vault",
    "Hipster",
    "Trapdoor",
    "Bridge",
    "Spiral",
    "Shutter",
    "Scissor",
    "Horizontal",
    "Vertical",
    "Floor",
    "Ceiling",
    "Wall",
    "Waterproof",
    "Survival friendly",
    "Java Edition",
    "Bedrock Edition",
    "Synchronized",
    "Resource efficient",
)

_BUILD_KINDS = (
    "Flush piston door",
    "Hipster trapdoor",
    "Glass vault",
    "Retracting bridge",
    "Spiral door",
    "Floor hatch",
)

_LONG_DESCRIPTION = (
    "The mechanism fits behind the wall, leaving both sides of the doorway flush when closed. "
    "A single input below the threshold starts the top rows first, opening a head-height gap before the floor retracts. "
    "The observer chain stays outside the opening so the same control circuit can be reused in a wider frame. "
    "Honey and slime isolate the moving sections; the glass centre remains visible throughout the sequence. "
    "For the sample record, the maintenance notes include the piston order, wiring route, and reset procedure. "
    "Repeated pulses are ignored until retraction finishes. Leave access behind the lower extender for repairs. "
    "The related records below compare different frame sizes and mechanisms. These are illustrative measurements, "
    "not verified records from the production catalogue."
)


@dataclass(frozen=True)
class BuildSample:
    title: str = "Flush piston door"
    dimensions: str = "4x4x3"
    ticks: int = 16
    description: str = "A flush door with a hidden mechanism and a single input below the threshold."
    oversized: bool = False
    tag: str = "Flush"


class BuildDraft(sl.Reactive):
    """One invocation's draft and publication, retained only by its live panels."""

    build: BuildSample = sl.state(BuildSample())
    publication: sl.operations.OperationExecution[BuildSample, str] | None = sl.state(None, persist=False, opaque=True)

    def publication_text(self) -> str:
        execution = self.publication
        if execution is None:
            return "Unpublished · sample data only"
        match execution.status:
            case sl.operations.Pending(progress):
                return f"Publishing · {progress}"
            case sl.operations.Succeeded(value):
                return "Published · simulated" if value == self.build else "Draft changed · publish again"
            case sl.operations.Failed():
                return "Simulated connection failure · retry publishing"
            case sl.operations.Cancelled():
                return "Publication cancelled · retry publishing"

    @property
    def publishing(self) -> bool:
        return self.publication is not None and isinstance(self.publication.status, sl.operations.Pending)


class BuildForm(sl.forms.Form):
    """Keep the whole build edit in one validated action and history entry."""

    title = tr("Edit sample build")
    name = sl.forms.TextField(label=tr("Build title"), minimum=3, maximum=80)
    dimensions = sl.forms.TextField(label=tr("Dimensions (width x height x depth)"), maximum=24)
    ticks = sl.forms.IntField(label=tr("Opening time in ticks"), minimum=1, maximum=1200)
    description = sl.forms.TextAreaField(label=tr("Description"), minimum=10, maximum=2000)

    def __init__(self, draft: BuildDraft, history: sl.runtime.History) -> None:
        build = draft.build
        super().__init__(
            name=build.title, dimensions=build.dimensions, ticks=build.ticks, description=build.description
        )
        self.draft = draft
        self.history = history

    @override
    def validate(self) -> tuple[sl.forms.FormIssue, ...]:
        if re.fullmatch(r"[1-9]\d{0,2}\s*[x\u00d7]\s*[1-9]\d{0,2}\s*[x\u00d7]\s*[1-9]\d{0,2}", self.dimensions) is None:
            return (sl.forms.FieldError(BuildForm.dimensions, message=tr("Use three positive sizes, such as 4x4x3.")),)
        return ()

    @override
    async def on_submit(self, event: sl.SubmitEvent) -> None:
        self.draft.build = replace(
            self.draft.build,
            title=self.name,
            dimensions=self.dimensions,
            ticks=self.ticks,
            description=self.description,
        )
        self.history.record(tr("Edit build"))


class Workbench(InspectablePanel):
    history: sl.runtime.History = sl.runtime.history(limit=10)
    outcome: str = sl.state("Edit the build, then undo. Watch the preview follow both changes.", persist=False)
    fail_next: bool = sl.state(default=False)

    def __init__(self, draft: BuildDraft) -> None:
        self.draft = draft

    @override
    def render(self) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request]:
        draft = self.draft
        heading = sl.heading(tr("Build workbench"))
        children: tuple[sl.ChildLike[sl.ComponentsV2Target, sd.Request], ...] = (
            sl.paragraph(
                tr("A form, an undo stack, and a live preview in another message. All data here is disposable.")
            ),
            sl.fields(sl.field(tr("Draft"), draft.build.title), sl.field(tr("Publication"), draft.publication_text())),
            sl.form(tr("Edit build"), BuildForm(draft, self.history), key="edit", tone=sl.Tone.SUCCESS),
            sl.action_controls(
                sl.action_control(tr("Small sample"), self._small, key="small", record=self.history),
                sl.action_control(tr("Oversized sample"), self._large, key="large", record=self.history),
                sl.action_control(tr("Undo"), self._undo, key="undo", available=self.history.can_undo),
                sl.action_control(tr("Redo"), self._redo, key="redo", available=self.history.can_redo),
                key="editing",
            ),
            sl.action_controls(
                sl.action_control(tr("Publish sample"), self._publish, key="publish", available=not draft.publishing),
                sl.action_control(
                    tr("Failure armed") if self.fail_next else tr("Fail next publish"),
                    self._fail,
                    key="fail",
                    available=not draft.publishing,
                ),
                key="publishing",
            ),
            sl.note(self.outcome),
            self.inspector(),
        )
        return sl.section(heading, *children, accent=DISCORD_BLUE)

    @override
    def inspection_state(self) -> str:
        return (
            f"Shared draft: {self.draft.build.title} · {self.draft.build.dimensions} · {self.draft.build.ticks} ticks\n"
            f"Local history: undo={self.history.can_undo}, redo={self.history.can_redo}\n"
            f"Last outcome: {self.outcome}\nOperation: {self.draft.publication_text()}\n"
            "The preview follows cells it reads; no handler pushes an edit to it."
        )

    async def _small(self, event: sl.PressEvent[sd.Request]) -> None:
        self.draft.build = BuildSample()

    async def _large(self, event: sl.PressEvent[sd.Request]) -> None:
        self.draft.build = BuildSample(description=_LONG_DESCRIPTION, oversized=True)

    async def _undo(self, event: sl.PressEvent[sd.Request]) -> None:
        self.outcome = history_outcome("Undo", await self.history.undo())

    async def _redo(self, event: sl.PressEvent[sd.Request]) -> None:
        self.outcome = history_outcome("Redo", await self.history.redo())

    async def _fail(self, event: sl.PressEvent[sd.Request]) -> None:
        if not self.draft.publishing:
            self.fail_next = not self.fail_next

    async def _publish(self, event: sl.PressEvent[sd.Request]) -> None:
        if self.draft.publishing:
            return
        # Capture before starting: edits during the operation must not change what was sent.
        build, fail = self.draft.build, self.fail_next
        self.fail_next = False

        async def publish(progress: sl.operations.ProgressReporter[str]) -> BuildSample:
            progress.report("checking build")
            await anyio.sleep(0.6)
            progress.report("sending captured revision")
            await anyio.sleep(0.6)
            if fail:
                message = "Simulated connection failure"
                raise RuntimeError(message)
            return build

        self.draft.publication = sl.operations.OperationDefinition(
            self, publish, name="Publish sample build", initial="queued"
        ).start()


class BuildPreview(InspectablePanel):
    def __init__(self, draft: BuildDraft) -> None:
        self.draft = draft

    @override
    def render(self) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request]:
        build = self.draft.build
        count = 36 if build.oversized else 3
        heading = sl.heading(build.title)
        children: tuple[sl.ChildLike[sl.ComponentsV2Target, sd.Request], ...] = (
            sl.fields(
                sl.field(tr("Dimensions"), build.dimensions),
                sl.field(tr("Opening time"), f"{build.ticks} ticks"),
                sl.field(tr("Tag"), build.tag),
            ),
            sl.budget(sl.truncate(sl.paragraph(build.description)), min=100, prefer=350),
            sl.note(self.draft.publication_text()),
            sl.heading(tr("Related sample records")),
            sd.v2.lines(
                *(
                    f"#{i + 1:03} · {_BUILD_KINDS[i % len(_BUILD_KINDS)]} · {2 + i // 6}x{2 + i // 6} · {8 + i} ticks"
                    for i in range(count)
                ),
                overflow=sl.primitives.Paginate(key="records", per=4),
            ),
            sl.heading(tr("Choose a tag")),
            sl.action_controls(
                *(
                    sl.action_control(tag, partial(self._tag, tag=tag), key=f"tag-{i}")
                    for i, tag in enumerate(_TAGS[:count], 1)
                ),
                key="tags",
            ),
            sl.note(tr("Try the oversized sample: records paginate, text shortens, and tag buttons become pickers.")),
            self.inspector(),
        )
        return sl.section(heading, *children, accent=DISCORD_GREEN)

    @override
    def inspection_state(self) -> str:
        return (
            f"Shared sample: {'oversized' if self.draft.build.oversized else 'small'} · tag={self.draft.build.tag}\n"
            f"Operation: {self.draft.publication_text()}\n"
            "Pagination belongs to this message. The draft belongs to both panels."
        )

    async def _tag(self, event: sl.PressEvent[sd.Request], *, tag: str) -> None:
        self.draft.build = replace(self.draft.build, tag=tag)


def history_outcome(verb: str, result: sl.runtime.HistoryResult) -> str:
    match result.status:
        case sl.runtime.HistoryResultStatus.APPLIED:
            return f"{verb} applied."
        case sl.runtime.HistoryResultStatus.CONFLICT:
            return f"{verb} refused: a newer edit must be preserved."
        case sl.runtime.HistoryResultStatus.EMPTY:
            return f"Nothing to {verb.lower()}."
        case _:
            return f"{verb}: {result.status.value}"
