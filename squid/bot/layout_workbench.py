"""A disposable build editor whose second message follows shared reactive state."""

import re
from dataclasses import dataclass, replace
from functools import partial
from typing import override
from uuid import uuid4

import anyio

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.edit_history import history_controls
from squid.bot.layout_inspector import InspectablePanel
from squid.bot.ui import DISCORD_BLUE, DISCORD_GREEN, tr
from squid_reactivity import transaction
from squid_replication import LoroBackend, Replica

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


class BuildDraft(sl.Reactive):
    """Own a disposable build and replicated tags until the workbench calls close."""

    build: BuildSample = sl.state(BuildSample())
    publication: sl.operations.OperationExecution[tuple[BuildSample, frozenset[str]], str] | None = sl.state(
        None, persist=False, opaque=True
    )

    def __init__(self) -> None:
        self.replica = Replica(str(uuid4()), backend=LoroBackend())
        self.document = self.replica.open("sample-build")
        self.tags = self.document.set("tags")
        with transaction():
            self.tags.add("Flush")

    def close(self) -> None:
        self.replica.close()

    def snapshot(self) -> tuple[BuildSample, frozenset[str]]:
        return self.build, self.tags.value

    def publication_text(self) -> str:
        execution = self.publication
        if execution is None:
            return "Unpublished · sample data only"
        match execution.status:
            case sl.operations.Pending(progress):
                return f"Publishing · {progress}"
            case sl.operations.Succeeded(value):
                return "Published · simulated" if value == self.snapshot() else "Draft changed · publish again"
            case sl.operations.Failed():
                return "Simulated connection failure · retry publishing"
            case sl.operations.Cancelled():
                return "Publication cancelled · retry publishing"

    @property
    def publishing(self) -> bool:
        return self.publication is not None and isinstance(self.publication.status, sl.operations.Pending)


class BuildForm(sl.forms.Form):
    """Keep the whole build edit in one validated action and history entry."""

    title = tr(t"layout-workbench-edit-sample-build = Edit sample build")
    name = sl.forms.TextField(label=tr(t"layout-workbench-build-title = Build title"), minimum=3, maximum=80)
    dimensions = sl.forms.TextField(
        label=tr(t"layout-workbench-dimensions-hint = Dimensions (width x height x depth)"),
        maximum=24,
    )
    ticks = sl.forms.IntField(
        label=tr(t"layout-workbench-opening-time-in-ticks = Opening time in ticks"), minimum=1, maximum=1200
    )
    description = sl.forms.TextAreaField(
        label=tr(t"layout-workbench-description = Description"), minimum=10, maximum=2000
    )

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
            return (
                sl.forms.FieldError(
                    BuildForm.dimensions,
                    message=tr(t"layout-workbench-dimensions-validation = Use three positive sizes, such as 4x4x3."),
                ),
            )
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
        self.history.record(tr(t"layout-workbench-edit-build = Edit build"))


class Workbench(InspectablePanel):
    policy = InspectablePanel.policy.overlay(sd.ResponseSpec(session=sd.SessionSpec("layout-workbench")))

    history: sl.runtime.History = sl.runtime.history(limit=10)
    notice: str = sl.state("Edit the build, then undo. Watch the preview follow both changes.", persist=False)
    fail_next: bool = sl.state(default=False)

    def __init__(self, draft: BuildDraft) -> None:
        self.draft = draft

    @override
    async def on_close(self, outcome: sd.Completed[None] | sd.Cancelled) -> None:
        self.history.clear()
        self.draft.close()

    @override
    def render(self) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request]:
        draft = self.draft
        heading = sl.heading(tr(t"layout-workbench-build-workbench = Build workbench"))
        children: tuple[sl.ChildLike[sl.ComponentsV2Target, sd.Request], ...] = (
            sl.paragraph(
                tr(
                    t"layout-workbench-demo-description = A form, an undo stack, and a live "
                    t"preview in another message. All data here is disposable."
                )
            ),
            sl.fields(
                sl.field(tr(t"layout-workbench-draft = Draft"), draft.build.title),
                sl.field(tr(t"layout-workbench-publication = Publication"), draft.publication_text()),
            ),
            sl.form(
                tr(t"layout-workbench-edit-build = Edit build"),
                BuildForm(draft, self.history),
                key="edit",
                tone=sl.Tone.SUCCESS,
            ),
            sl.action_controls(
                sl.action_control(
                    tr(t"layout-workbench-small-sample = Small sample"),
                    self._small,
                    key="small",
                    record=self.history,
                ),
                sl.action_control(
                    tr(t"layout-workbench-oversized-sample = Oversized sample"),
                    self._large,
                    key="large",
                    record=self.history,
                ),
                sl.action_control(
                    tr(t"layout-workbench-undo = Undo"), self._undo, key="undo", available=self.history.can_undo
                ),
                sl.action_control(
                    tr(t"layout-workbench-redo = Redo"), self._redo, key="redo", available=self.history.can_redo
                ),
                key="editing",
            ),
            sl.action_controls(
                sl.action_control(
                    tr(t"layout-workbench-publish-sample = Publish sample"),
                    self._publish,
                    key="publish",
                    available=not draft.publishing,
                ),
                sl.action_control(
                    tr(t"layout-workbench-failure-armed = Failure armed")
                    if self.fail_next
                    else tr(t"layout-workbench-fail-next-publish = Fail next publish"),
                    self._fail,
                    key="fail",
                    available=not draft.publishing,
                ),
                key="publishing",
            ),
            sl.note(self.notice),
            self.inspector(),
        )
        return sl.section(heading, *children, accent=DISCORD_BLUE)

    @override
    def inspection_state(self) -> str:
        return (
            f"Shared draft: {self.draft.build.title} · {self.draft.build.dimensions} · {self.draft.build.ticks} ticks\n"
            f"Local history: undo={self.history.can_undo}, redo={self.history.can_redo}\n"
            f"Last notice: {self.notice}\nOperation: {self.draft.publication_text()}\n"
            "The preview follows cells it reads; no handler pushes an edit to it."
        )

    async def _small(self, event: sl.PressEvent[sd.Request]) -> None:
        self.draft.build = BuildSample()

    async def _large(self, event: sl.PressEvent[sd.Request]) -> None:
        self.draft.build = BuildSample(description=_LONG_DESCRIPTION, oversized=True)

    async def _undo(self, event: sl.PressEvent[sd.Request]) -> None:
        self.notice = history_outcome("Undo", await self.history.undo())

    async def _redo(self, event: sl.PressEvent[sd.Request]) -> None:
        self.notice = history_outcome("Redo", await self.history.redo())

    async def _fail(self, event: sl.PressEvent[sd.Request]) -> None:
        if not self.draft.publishing:
            self.fail_next = not self.fail_next

    async def _publish(self, event: sl.PressEvent[sd.Request]) -> None:
        if self.draft.publishing:
            return
        # Capture before starting: edits during the operation must not change what was sent.
        build, fail = self.draft.snapshot(), self.fail_next
        self.fail_next = False

        async def publish(progress: sl.operations.ProgressReporter[str]) -> tuple[BuildSample, frozenset[str]]:
            progress.report("checking build")
            await anyio.sleep(0.6)
            progress.report("sending captured revision")
            await anyio.sleep(0.6)
            if fail:
                message = "Simulated connection failure"
                raise RuntimeError(message)
            return build

        self.draft.publication = sl.operations.OperationDefinition(
            publish, name="Publish sample build", initial="queued"
        ).start()


class BuildPreview(InspectablePanel):
    history: sl.runtime.History = sl.runtime.history(limit=20)

    policy = InspectablePanel.policy.overlay(sd.ResponseSpec(session=sd.SessionSpec("layout-preview")))

    def __init__(self, draft: BuildDraft) -> None:
        self.draft = draft

    @override
    async def on_close(self, outcome: sd.Completed[None] | sd.Cancelled) -> None:
        self.history.clear()

    @override
    def render(self) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request]:
        build = self.draft.build
        tags = self.draft.tags.value
        count = 36 if build.oversized else 3
        heading = sl.heading(build.title)
        children: tuple[sl.ChildLike[sl.ComponentsV2Target, sd.Request], ...] = (
            sl.fields(
                sl.field(tr(t"layout-workbench-dimensions = Dimensions"), build.dimensions),
                sl.field(tr(t"layout-workbench-opening-time = Opening time"), f"{build.ticks} ticks"),
                sl.field(tr(t"layout-workbench-tags = Tags"), ", ".join(sorted(tags)) or "—"),
            ),
            sl.paragraph(build.description).truncate().budget(min=100, prefer=350),
            sl.note(self.draft.publication_text()),
            sl.heading(tr(t"layout-workbench-related-sample-records = Related sample records")),
            sd.v2.lines(
                *(
                    f"#{i + 1:03} · {_BUILD_KINDS[i % len(_BUILD_KINDS)]} · {2 + i // 6}x{2 + i // 6} · {8 + i} ticks"
                    for i in range(count)
                ),
                overflow=sl.primitives.Paginate(key="records", per=4),
            ),
            sl.heading(tr(t"layout-workbench-toggle-tags = Add or remove tags")),
            sl.action_controls(
                *(
                    sl.action_control(
                        tag,
                        partial(self._tag, tag=tag, selected=tag in tags),
                        key=f"tag-{i}",
                        tone=sl.Tone.SUCCESS if tag in tags else sl.Tone.NEUTRAL,
                    )
                    for i, tag in enumerate(_TAGS[:count], 1)
                ),
                key="tags",
            ),
            *history_controls(self.history),
            sl.note(
                tr(
                    t"layout-workbench-pagination-hint = Try the oversized "
                    t"sample: records paginate, text shortens, and tag buttons become pickers."
                )
            ),
            self.inspector(),
        )
        return sl.section(heading, *children, accent=DISCORD_GREEN)

    @override
    def inspection_state(self) -> str:
        return (
            f"Shared sample: {'oversized' if self.draft.build.oversized else 'small'} · tags={sorted(self.draft.tags.value)}\n"
            f"Operation: {self.draft.publication_text()}\n"
            "Pagination belongs to this message. The draft belongs to both panels."
        )

    async def _tag(self, event: sl.PressEvent[sd.Request], *, tag: str, selected: bool) -> None:
        # Preserve the rendered intent if another editor changes the set before this press arrives.
        if selected:
            self.draft.tags.discard(tag)
            self.history.record(tr(t"layout-workbench-remove-tag = Remove {tag}"))
        else:
            self.draft.tags.add(tag)
            self.history.record(tr(t"layout-workbench-add-tag = Add {tag}"))


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
