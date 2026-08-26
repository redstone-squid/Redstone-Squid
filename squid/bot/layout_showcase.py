"""Public interactive showcase for the squid-layouts engine."""

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Sequence
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING, Literal, Never

from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Cog, Context, guild_only

import squid_discord as sd
import squid_layouts as sl
import squid_patterns as sp
from squid.bot.i18n import resolve_locale
from squid.bot.ui import (
    DISCORD_BLUE,
    DISCORD_GREEN,
    DISCORD_YELLOW,
    L,
    create_mount,
    destination,
    localization_for,
    send_component,
)
from squid.core.i18n import _
from squid_discord import SessionKey
from squid_discord.screens import Opener
from squid_discord.sessions import UserScope
from squid_replicated import ReplicatedDocument, ReplicatedScope

if TYPE_CHECKING:
    import squid.bot.app


type DemoSection = Literal[
    "pagination",
    "adaptation",
    "degradation",
    "data",
    "grid",
    "ownership",
    "forms",
    "composition",
    "localization",
    "history",
    "replication",
    "effects",
]

_ACCENTS = (DISCORD_BLUE, DISCORD_GREEN, DISCORD_YELLOW)

_SOURCE_EXAMPLES = {
    "pagination": """lines = sl.primitives.Lines(
    entries,
    join="\n\n",
    overflow=sl.primitives.Paginate(
        key="samples",          # independent cursor identity
        footer=page_footer,     # measured as part of each page
    ),
)

# No page size: the solver fills Discord's actual text budget.
return sl.section(sl.heading("Measured pagination"), lines)""",
    "adaptation": """actions = tuple(
    sl.action(
        L("Action {number}", number=number),
        on_action,
        key=f"action.{number}",
    )
    for number in range(1, 37)
)

# Declare intent once. Discord lowers this to pickers of 25 and 11.
return sl.actions(*actions, key="showcase-actions")""",
    "degradation": """return sl.primitives.Panel((
    sl.primitives.Heading("Deliberate degradation"),

    # Shorten this text, but keep as much as the budget permits.
    sl.primitives.Text(long_explanation, overflow=sl.primitives.Truncate()),

    # Keep entries atomic and say exactly how many were omitted.
    sl.primitives.Lines(audit_events, overflow=sl.primitives.Spill()),

    # Never sacrifice this audit promise.
    sl.primitives.Footer(audit_note, overflow=sl.primitives.Never()),
))""",
    "data": """return sl.section(
    sl.heading("Typed data"),

    # A quantity, a proportion, and an instant -- not three pre-formatted strings.
    sl.measure(len(rows), "Loaded samples", unit="rows"),
    sl.progress(clicks, label="Clicks toward ten", maximum=10),

    # One node; Discord draws it in each reader's own timezone.
    sl.timestamp(opened_at, style=sl.semantic.TimeStyle.RELATIVE),

    # AUTO, so the planner may fall back to records when a row will not fit.
    sl.table(columns, *rows, key="capability-table"),
)""",
    "grid": """cells = tuple(
    sp.GridCell(
        f"cell-{index}",
        str(index + 1),
        available=index not in blocked,
    )
    for index in range(12)
)

# Cells are variadic. Buttons preserve the board while it fits; larger shapes
# lower to coordinate or paged selects with the same SelectionEvent keys.
return sl.grid(*cells, key="showcase-grid", columns=4, on_pick=self._pick_grid)""",
    "ownership": """# The session owns this one, under its key. No component state backs it.
sl.toggle("Session-owned", key="ownership.managed")

# The component owns this one: self.subscribed wins every render, and the
# handler is the only path to a new value -- so it may refuse one.
sl.toggle(
    "Component-owned",
    key="ownership.controlled",
    on=sl.controlled(self.subscribed, self._set_subscribed),
)

sl.rating(key="ownership.rating", value=sl.controlled(self.rating, self._rate))""",
    "forms": """class FeedbackForm(sl.forms.Form):
    exhibit = sl.forms.ChoiceField(label="Which exhibit", options=EXHIBITS)
    headline = sl.forms.TextField(label="Headline", minimum=4, maximum=80)
    score = sl.forms.ScaleField(label="Score", minimum=1, maximum=5)

    def validate(self):
        # Runs only once every field has parsed, so these are typed values.
        if self.score <= 2 and not self.detail:
            return (sl.forms.FieldError("detail", "A low score needs a reason."),)
        return ()

# A schema, not a Discord modal. RETRY re-presents it with the errors
# and everything already typed; **prefill seeds it from state.
return sl.form("Open the feedback form", FeedbackForm(**prefill), key="feedback")""",
    "composition": """class Dashboard(sl.Component):
    def __init__(self):
        self.left = Counter("Left")
        self.right = Counter("Right")

    def render(self):
        return sl.stack(
            self.boundary(self.left, key="left"),
            self.boundary(self.right, key="right"),
        )

# Keys namespace state, lifecycle, and action ids for each child.""",
    "localization": """def render(self):
    build_title = self.build.title
    return sl.paragraph(
        # L preserves the msgid and value until the mount plans a locale.
        L(t"Build: {build_title}")
    )

async def switch_language(self, event: sl.PressEvent) -> None:
    mount = sd.responder(event).mount
    mount.localize(localization_for("zh-CN"))

# Interpolated values are Markdown-escaped unless wrapped in sl.raw_md().""",
    "history": """history: sl.runtime.History = sl.runtime.history(limit=5)

# The whole committed action becomes one conditional inverse plan.
sl.action("Rename project", self.rename, key="history.rename", record=self.history)

result = await self.history.undo()
match result.status:
    case sl.runtime.HistoryResultStatus.APPLIED:
        ...  # undo committed as a new action, with fresh versions
    case sl.runtime.HistoryResultStatus.CONFLICT:
        ...  # a later write is intact; nothing was partially restored

# Outcome hooks run after the old transaction is dead. Recovery is a new action.
def rolled_back(rollback, aftermath):
    with aftermath.start_action("Present failure"):
        self.notice = rollback.reason.value

sl.runtime.on_action_rollback(rolled_back)""",
    "replication": """scope = ReplicatedScope("browser-a")
document = scope.open("showcase")

# Reads are immutable snapshots; writes are semantic transaction participants.
document.counter("votes").increment(2)
document.set("reviewers").add("mine")
history.record("Add my review")

# Transport is application-owned. Import uses the same commit gate as local state.
peer.import_update(document.export_since())

# The backend plans this action's inverse at the current frontier, preserving
# a peer's later +3 and tagged-set insertion.
result = await history.undo()""",
    "effects": """@sl.operation(initial="queued")
async def publish(self, progress: sl.operations.Progress[str]) -> int:
    progress.set("sending")
    return 42

execution = self.publish.start()  # every start has a fresh execution id
with execution.start_action("Accept published revision"):
    self.published_revision = result

history.record(
    "Create demo channel",
    compensate=sl.runtime.CompensationSpec(
        operation=service.delete,
        idempotency_key=lambda commit: f"undo:{commit.context.action_id}",
    ),
)
# Failure and retry are inspectable saga outcomes, never described as rollback.
result = await history.undo()""",
}


class DemoRollback(RuntimeError):
    """An expected showcase failure used to demonstrate a definitive rollback."""


class DemoChannelService:
    """A tiny external system whose compensation can fail once on demand."""

    def __init__(self) -> None:
        self.exists = False
        self.fail_next_delete = False

    async def create(self) -> None:
        self.exists = True

    async def delete(self, idempotency_key: str) -> None:
        del idempotency_key
        if self.fail_next_delete:
            self.fail_next_delete = False
            message = "the demo API rejected this compensation attempt"
            raise RuntimeError(message)
        self.exists = False


def _fail_demo_action() -> Never:
    message = "expected showcase failure"
    raise DemoRollback(message)


class DemoCounter(sl.Component):
    """A tiny child component used twice to demonstrate keyed composition."""

    count: int = sl.state(0)

    def __init__(self, label: sl.TextLike) -> None:
        self.label = label

    def render(self) -> sl.primitives.Node:
        return sl.primitives.Panel(
            (
                sl.primitives.Heading(self.label, level=3),
                sl.primitives.Text(L("Independent count: {count}", count=self.count)),
                sl.primitives.Row(
                    (
                        sl.primitives.Button(
                            L(t"Increment"),
                            self._increment,
                            "increment",
                            style=sl.primitives.ActionStyle.SUCCESS,
                        ),
                    )
                ),
            ),
            accent=DISCORD_GREEN,
        )

    async def _increment(self, event: sl.PressEvent) -> None:
        self.count += 1


_FEEDBACK_EXHIBITS = (
    sl.forms.ChoiceOption("pagination", L(t"Budget pagination"), "pagination"),
    sl.forms.ChoiceOption("adaptation", L(t"Structural adaptation"), "adaptation"),
    sl.forms.ChoiceOption("degradation", L(t"Graceful degradation"), "degradation"),
    sl.forms.ChoiceOption("data", L(t"Typed data"), "data"),
    sl.forms.ChoiceOption("ownership", L(t"Value ownership"), "ownership"),
)


class FeedbackForm(sl.forms.Form):
    """A typed schema, presented however the dispatching frontend can present it."""

    title = L(t"Tell us about an exhibit")

    exhibit = sl.forms.ChoiceField(label=L(t"Which exhibit"), options=_FEEDBACK_EXHIBITS)
    headline = sl.forms.TextField(label=L(t"Headline"), minimum=4, maximum=80)
    detail = sl.forms.TextAreaField(label=L(t"What stood out"), required=False, maximum=400)
    score = sl.forms.ScaleField(
        label=L(t"Score"),
        minimum=1,
        maximum=5,
        labels={1: L(t"Confusing"), 5: L(t"Clear")},
    )

    def __init__(self, on_recorded: Callable[[FeedbackForm, sl.SubmitEvent], Awaitable[None]], /, **prefill: object):
        super().__init__(**prefill)
        self._on_recorded = on_recorded

    def validate(self) -> Iterable[sl.forms.FormIssue]:
        # Cross-field validation runs only once every field has parsed, so these are the typed
        # values rather than the strings the reader typed.
        if self.score is not None and self.score <= 2 and not self.detail:
            return (sl.forms.FieldError("detail", L(t"A low score needs a sentence saying why.")),)
        return ()

    async def on_submit(self, event: sl.SubmitEvent) -> None:
        # The submitted values are already bound to this instance, so the handler reads them as
        # declared attributes instead of unpacking `event.values` by key.
        await self._on_recorded(self, event)


class LayoutShowcase(sl.Component):
    """One mounted tour of planning, pagination, ownership, forms, and composition."""

    section: str = sl.state("pagination")
    accent_index: int = sl.state(0)
    clicks: int = sl.state(0)
    grid_pick: str = sl.state("No position selected.")
    rating: int | None = sl.state(None)
    subscribed: bool = sl.state(default=False)
    feedback_exhibit: str = sl.state("")
    feedback_headline: str = sl.state("")
    feedback_score: int = sl.state(0)
    display_locale: str = sl.state("en", persist=False)
    project_name: str = sl.state("Redstone Squid")
    history_result: str = sl.state("No history action yet.", persist=False)
    outcome_result: str = sl.state("No terminal outcome observed yet.", persist=False)
    replication_result: str = sl.state("Add a local review, then merge a peer edit.", persist=False)
    channel_present: bool = sl.state(default=False)
    compensation_result: str = sl.state("Create the external channel to begin.", persist=False)
    published_revision: int | None = sl.state(None)
    publication: sl.operations.OperationExecution[int, str] | None = sl.state(None, persist=False, opaque=True)

    action_history: sl.runtime.History = sl.runtime.history(limit=5)
    replication_history: sl.runtime.History = sl.runtime.history(limit=5)
    effect_history: sl.runtime.History = sl.runtime.history(limit=5)

    def __init__(self, *, section: DemoSection, entries: int, locale: str | None) -> None:
        self.section = section
        self.entries = tuple(self._entry(index) for index in range(1, entries + 1))
        self.display_locale = locale or "en"
        self.opened_at = datetime.now(UTC)
        self.left = DemoCounter(L(t"Left child"))
        self.right = DemoCounter(L(t"Right child"))
        self.channel_service = DemoChannelService()
        self.local_replication_scope = ReplicatedScope("showcase-local")
        self.peer_replication_scope = ReplicatedScope("showcase-peer")
        self.local_document: ReplicatedDocument = self.local_replication_scope.open("layout-showcase")
        self.peer_document: ReplicatedDocument = self.peer_replication_scope.open("layout-showcase")

    @sl.operation(initial="queued")
    async def publish_revision(self, progress: sl.operations.Progress[str]) -> int:
        """Simulate one repeatable external publication execution."""
        progress.set("sending")
        await asyncio.sleep(0)
        return (self.published_revision or 40) + 1

    @sl.computed
    def status(self) -> sl.text.Message:
        return L("Section: {section} · reactive clicks: {clicks}", section=self.section, clicks=self.clicks)

    def render(self) -> Sequence[sl.LayoutNode]:
        controls = (
            sl.primitives.SelectMenu(
                tuple(
                    sl.primitives.Option(label, value, description, default=self.section == value)
                    for value, label, description in self._sections()
                ),
                self._select_section,
                "section",
                placeholder=L(t"Choose an engine exhibit"),
            ),
            sl.primitives.ActionGroup(
                (
                    sl.primitives.Button(
                        L(t"Cycle accent"),
                        self._cycle_accent,
                        "accent",
                        style=sl.primitives.ActionStyle.PRIMARY,
                    ),
                    sl.primitives.Button(L(t"Reactive click"), self._click, "click"),
                )
            ),
        )
        header = sl.primitives.Panel(
            (
                sl.primitives.Heading(L(t"squid-layouts engine showcase")),
                sl.primitives.Text(self.status),
                *controls,
            ),
            accent=_ACCENTS[self.accent_index],
        )
        return (header, *self._render_section())

    def _render_section(self) -> Sequence[sl.LayoutNode]:
        match self.section:
            case "adaptation":
                exhibit = self._adaptation()
            case "degradation":
                exhibit = self._degradation()
            case "data":
                exhibit = self._data()
            case "grid":
                exhibit = self._grid()
            case "ownership":
                exhibit = self._ownership()
            case "forms":
                exhibit = self._forms()
            case "composition":
                exhibit = self._composition()
            case "localization":
                exhibit = self._localization()
            case "history":
                exhibit = self._history()
            case "replication":
                exhibit = self._replication()
            case "effects":
                exhibit = self._effects()
            case _:
                exhibit = self._pagination()
        return (*exhibit, self._source_example())

    def _pagination(self) -> Sequence[sl.primitives.Node]:
        return (
            sl.primitives.Panel(
                (
                    sl.primitives.Heading(L(t"Target-budget pagination")),
                    sl.primitives.Text(
                        L(
                            "There is no fixed entries-per-page value here. The solver fills the available "
                            "Discord text budget and includes the measured footer and navigation controls."
                        ),
                        overflow=sl.primitives.Never(),
                    ),
                    sl.primitives.Lines(
                        self.entries,
                        join="\n\n",
                        overflow=sl.primitives.Paginate(key="samples", footer=self._page_footer),
                    ),
                ),
                accent=DISCORD_BLUE,
            ),
        )

    def _adaptation(self) -> Sequence[sl.LayoutNode]:
        actions = tuple(
            sl.semantic.Action(f"action.{index}", L("Action {number}", number=index), self._action_notice)
            for index in range(1, 37)
        )
        return (
            sl.section(
                sl.heading(L(t"Structural adaptation")),
                sl.paragraph(
                    L(
                        "This declares 36 actions, not buttons or menus. The planner preserves all 36 as "
                        "two pickers of 25 and 11 options because that is the best legal Discord "
                        "representation."
                    )
                ),
                accent=DISCORD_YELLOW,
            ),
            sl.semantic.Actions(actions, key="showcase-actions"),
        )

    def _degradation(self) -> Sequence[sl.LayoutNode]:
        long_explanation = " ".join(
            [
                "This deliberately oversized explanation remains important, so Truncate shortens it instead of "
                "dropping the whole block."
            ]
            * 36
        )
        audit_lines = tuple(f"event {index:02d} · one indivisible audit record" for index in range(1, 33))
        return (
            sl.primitives.Panel(
                (
                    sl.primitives.Heading(L(t"Deliberate degradation")),
                    sl.primitives.Text(
                        L(
                            "Three overflow policies in one card. Only the block below is allowed to lose "
                            "characters; the audit lines stay whole or say how many were dropped, and the "
                            "footer keeps its promise whatever the budget costs."
                        ),
                        overflow=sl.primitives.Never(),
                    ),
                    sl.primitives.Text(long_explanation, overflow=sl.primitives.Truncate()),
                    sl.primitives.Lines(audit_lines, overflow=sl.primitives.Spill()),
                    sl.primitives.Footer(
                        L(t"The report records every compromise the planner made."),
                        overflow=sl.primitives.Never(),
                    ),
                ),
                accent=DISCORD_YELLOW,
            ),
        )

    def _data(self) -> Sequence[sl.LayoutNode]:
        return (
            sl.section(
                sl.heading(L(t"Typed data, not pre-formatted strings")),
                sl.paragraph(
                    L(
                        "A quantity, a proportion, and an instant are declared as what they are and "
                        "formatted once, at the end. That is why the one timestamp node below reaches "
                        "every reader in their own timezone without this component knowing any of them."
                    )
                ),
                sl.measure(len(self.entries), L(t"Loaded samples"), unit="rows"),
                sl.progress(self.clicks, label=L(t"Reactive clicks toward ten"), maximum=10),
                sl.timestamp(self.opened_at, style=sl.semantic.TimeStyle.RELATIVE, label=L(t"Showcase opened")),
                accent=DISCORD_BLUE,
            ),
            sl.table(
                sl.columns(
                    sl.column(L(t"Exhibit")),
                    sl.column(L(t"Declares")),
                    sl.column(L(t"Adapts by")),
                ),
                sl.table_row(L(t"Pagination"), L(t"One long list"), L(t"Measured pages"), key="row.pagination"),
                sl.table_row(L(t"Adaptation"), L(t"36 actions"), L(t"Pickers of 25 and 11"), key="row.adaptation"),
                sl.table_row(L(t"Degradation"), L(t"An overflow policy"), L(t"Truncate, spill, never"), key="row.deg"),
                sl.table_row(L(t"Ownership"), L(t"Who holds a value"), L(t"Session or component"), key="row.ownership"),
                sl.table_row(L(t"Forms"), L(t"A typed schema"), L(t"Modal, page, or prompt"), key="row.forms"),
                key="capability-table",
            ),
        )

    def _ownership(self) -> Sequence[sl.LayoutNode]:
        return (
            sl.section(
                sl.heading(L(t"Who owns a control's value")),
                sl.paragraph(
                    L(
                        "Every stateful control names its owner. A managed value lives in the presentation "
                        "session under the control's key, so the first toggle below keeps its state with no "
                        "component state behind it at all. A controlled value is authoritative on every "
                        "render: the component decides what the reader sees, and its handler is the only "
                        "path to a new one, which is what lets a handler refuse the change."
                    )
                ),
                sl.fields(
                    sl.field(L(t"Component-owned rating"), self._rating_text()),
                    sl.field(
                        L(t"Component-owned toggle"),
                        L(t"Following") if self.subscribed else L(t"Not following"),
                    ),
                    sl.field(L(t"Session-owned toggle"), L(t"Held under its key; this component never reads it.")),
                ),
                accent=DISCORD_GREEN,
            ),
            sl.toggle(
                L(t"Session-owned toggle"),
                key="ownership.managed",
                on_label=L(t"Session says on"),
                off_label=L(t"Session says off"),
            ),
            sl.toggle(
                L(t"Component-owned toggle"),
                key="ownership.controlled",
                on=sl.controlled(self.subscribed, self._set_subscribed),
                tone=sl.Tone.SUCCESS,
            ),
            sl.rating(key="ownership.rating", value=sl.controlled(self.rating, self._rate)),
        )

    def _grid(self) -> Sequence[sl.LayoutNode]:
        blocked = {5, 10}
        cells = tuple(
            sp.GridCell(
                f"cell-{index}",
                str(index + 1),
                available=index not in blocked,
                tone=sl.Tone.INFO if index % 2 == 0 else sl.Tone.NEUTRAL,
            )
            for index in range(12)
        )
        return (
            sl.section(
                sl.heading(L(t"Selectable grid")),
                sl.paragraph(
                    L(
                        "The component declares cells, stable keys, and four columns. This shape fits as a "
                        "button board; wider or larger boards retain the same callback through coordinate "
                        "and paged-select representations."
                    )
                ),
                sl.status(self.grid_pick),
                accent=DISCORD_BLUE,
            ),
            sl.grid(*cells, key="showcase-grid", columns=4, on_pick=self._pick_grid),
        )

    def _forms(self) -> Sequence[sl.LayoutNode]:
        prefill: dict[str, object] = {}
        if self.feedback_exhibit:
            prefill["exhibit"] = self.feedback_exhibit
        if self.feedback_headline:
            prefill["headline"] = self.feedback_headline
        if self.feedback_score:
            prefill["score"] = self.feedback_score
        return (
            sl.section(
                sl.heading(L(t"Portable typed forms")),
                sl.paragraph(
                    L(
                        "This is a schema of typed fields, not a Discord modal. Each field parses itself, "
                        "cross-field validation runs only once they all have, and a failure re-presents the "
                        "form carrying both the errors and everything already typed."
                    )
                ),
                sl.fields(
                    sl.field(L(t"Last headline"), self.feedback_headline or L(t"nothing submitted yet")),
                    sl.field(L(t"Exhibit"), self.feedback_exhibit or "--"),
                    sl.field(L(t"Score"), str(self.feedback_score) if self.feedback_score else "--"),
                ),
                sl.note(L(t"Reopening the form prefills it from that same state.")),
                accent=DISCORD_GREEN,
            ),
            sl.form(
                L(t"Open the feedback form"),
                FeedbackForm(self._record_feedback, **prefill),
                key="feedback",
                tone=sl.Tone.SUCCESS,
            ),
        )

    def _composition(self) -> Sequence[sl.LayoutNode]:
        return (
            sl.section(
                sl.heading(L(t"Keyed component composition")),
                sl.paragraph(
                    L(
                        "These are two instances of the same child class. Boundaries namespace their state, "
                        "actions, and lifecycle paths, so clicking one cannot cross-wire the other."
                    )
                ),
            ),
            self.boundary(self.left, key="left"),
            self.boundary(self.right, key="right"),
        )

    def _localization(self) -> Sequence[sl.LayoutNode]:
        unsafe = "*operator input* @everyone [not a link](https://example.com)"
        return (
            sl.section(
                sl.heading(L(t"Deferred localization and safe Markdown")),
                sl.paragraph(
                    L(t"Messages retain their catalogue key and interpolation values until this mount plans a frame.")
                ),
                sl.fields(
                    sl.field(L(t"Negotiated locale"), self.display_locale),
                    sl.field(L(t"Escaped interpolation"), L(t"Rendered safely: {unsafe}")),
                ),
                sl.note(L(t"Switching language invalidates this same mount; no component is rebuilt or replaced.")),
                accent=DISCORD_BLUE,
            ),
            sl.actions(
                sl.action(L(t"Switch language"), self._switch_language, key="switch-language"),
                key="localization-actions",
            ),
        )

    def _history(self) -> Sequence[sl.LayoutNode]:
        return (
            sl.section(
                sl.heading(L(t"Action outcomes and conflict-safe history")),
                sl.paragraph(
                    L(
                        "Rename records one immutable commit. Undo is another action and only applies if the "
                        "recorded lineage is still current. Use the sibling edit before undo to see a conflict "
                        "leave that later value untouched."
                    )
                ),
                sl.fields(
                    sl.field(L(t"Project name"), self.project_name),
                    sl.field(L(t"History result"), self.history_result),
                    sl.field(L(t"Outcome hook"), self.outcome_result),
                ),
                sl.note(
                    L(
                        "The rollback button stages a value, raises, then presents the structured failure from "
                        "a fresh recovery action. The staged project name never appears."
                    )
                ),
                accent=DISCORD_GREEN,
            ),
            sl.actions(
                sl.action(
                    L(t"Rename project"),
                    self._rename_project,
                    key="history.rename",
                    tone=sl.Tone.SUCCESS,
                    record=self.action_history,
                ),
                sl.action(L(t"Sibling edit"), self._sibling_edit, key="history.sibling"),
                key="history-write-actions",
            ),
            sl.actions(
                sl.action(L(t"Undo rename"), self._undo_rename, key="history.undo"),
                sl.action(L(t"Redo rename"), self._redo_rename, key="history.redo"),
                sl.action(L(t"Drop conflict"), self._drop_history_conflict, key="history.drop"),
                sl.action(L(t"Cause rollback"), self._cause_rollback, key="history.rollback", tone=sl.Tone.DANGER),
                key="history-outcome-actions",
            ),
        )

    def _replication(self) -> Sequence[sl.LayoutNode]:
        local_counter = self.local_document.counter("votes").value
        local_reviewers = self.local_document.set("reviewers").value
        peer_counter = self.peer_document.counter("votes").value
        peer_reviewers = self.peer_document.set("reviewers").value
        return (
            sl.section(
                sl.heading(L(t"Replicated state with semantic selective undo")),
                sl.paragraph(
                    L(
                        "The local action contributes +2 and a tagged-set member. The simulated peer first "
                        "receives it, then contributes +3 and its own member, and sends the converged update "
                        "back. Undo targets only the retained local action."
                    )
                ),
                sl.fields(
                    sl.field(L(t"Local snapshot"), self._replica_summary(local_counter, local_reviewers)),
                    sl.field(L(t"Peer snapshot"), self._replica_summary(peer_counter, peer_reviewers)),
                    sl.field(L(t"Last inverse"), self.replication_result),
                ),
                sl.note(
                    L(
                        "These public values are immutable Python snapshots. Encoded updates are transported "
                        "explicitly; mutable backend containers never enter component state."
                    )
                ),
                accent=DISCORD_BLUE,
            ),
            sl.actions(
                sl.action(
                    L(t"Add my +2 review"),
                    self._add_local_review,
                    key="replication.local",
                    record=self.replication_history,
                    tone=sl.Tone.SUCCESS,
                ),
                sl.action(L(t"Merge peer +3"), self._merge_peer_review, key="replication.peer"),
                sl.action(L(t"Undo my review"), self._undo_local_review, key="replication.undo"),
                key="replication-actions",
            ),
        )

    def _effects(self) -> Sequence[sl.LayoutNode]:
        return (
            sl.section(
                sl.heading(L(t"Operations and compensation sagas")),
                sl.paragraph(
                    L(
                        "Publication is a repeatable operation definition: every start gets a distinct execution "
                        "identity, and accepting its result creates a causally linked action. The channel is an "
                        "external effect, so undo runs an idempotent compensation before a conditional local inverse."
                    )
                ),
                sl.fields(
                    sl.field(L(t"Publication"), self._publication_status()),
                    sl.field(
                        L(t"Accepted revision"),
                        "--" if self.published_revision is None else str(self.published_revision),
                    ),
                    sl.field(L(t"External channel"), L(t"exists") if self.channel_service.exists else L(t"absent")),
                    sl.field(L(t"Local channel flag"), L(t"present") if self.channel_present else L(t"absent")),
                    sl.field(L(t"Compensation"), self.compensation_result),
                ),
                sl.note(
                    L(
                        "Arm one failure before undo. The first attempt reports FAILED and keeps local state; "
                        "the second is a new execution using the same idempotency key and completes honestly."
                    )
                ),
                accent=DISCORD_YELLOW,
            ),
            sl.actions(
                sl.action(L(t"Start publication"), self._start_publication, key="effects.publish"),
                sl.action(L(t"Accept result"), self._accept_publication, key="effects.accept"),
                sl.action(L(t"Create channel"), self._create_channel, key="effects.create", tone=sl.Tone.SUCCESS),
                sl.action(L(t"Fail next compensation"), self._fail_next_compensation, key="effects.fail"),
                sl.action(L(t"Undo / retry channel"), self._undo_channel, key="effects.undo", tone=sl.Tone.DANGER),
                key="effects-actions",
            ),
        )

    def _source_example(self) -> sl.semantic.Section:
        return sl.semantic.Section(
            sl.semantic.Heading(L(t"Declaration source")),
            (
                sl.semantic.Paragraph(
                    L(t"This is the author-facing declaration; planning chooses the legal Discord shape.")
                ),
                sl.semantic.Code(_SOURCE_EXAMPLES.get(self.section, _SOURCE_EXAMPLES["pagination"]), language="python"),
            ),
        )

    def _sections(self) -> tuple[tuple[str, sl.TextLike, sl.TextLike], ...]:
        return (
            (
                "pagination",
                L(t"Budget pagination"),
                L(t"Pages filled from measured limits"),
            ),
            (
                "adaptation",
                L(t"Structural adaptation"),
                L(t"A large action surface folds compactly"),
            ),
            (
                "degradation",
                L(t"Graceful degradation"),
                L(t"Explicit truncate, spill, and preservation policies"),
            ),
            (
                "data",
                L(t"Typed data"),
                L(t"Quantities and instants formatted once, at the end"),
            ),
            (
                "grid",
                L(t"Selectable grid"),
                L(t"One stable interaction across spatial fallbacks"),
            ),
            (
                "ownership",
                L(t"Value ownership"),
                L(t"Session-owned and component-owned controls"),
            ),
            (
                "forms",
                L(t"Portable forms"),
                L(t"Typed fields, cross-field validation, and prefill"),
            ),
            (
                "composition",
                L(t"Composition"),
                L(t"Two independently keyed children"),
            ),
            (
                "localization",
                L(t"Deferred localization"),
                L(t"Live locale changes and safe interpolation"),
            ),
            (
                "history",
                L(t"Action history"),
                L(t"Immutable outcomes, conditional undo, and rollback recovery"),
            ),
            (
                "replication",
                L(t"Replicated state"),
                L(t"Semantic counter and tagged-set selective undo"),
            ),
            (
                "effects",
                L(t"Effects and operations"),
                L(t"Causal executions and truthful compensation retries"),
            ),
        )

    def _entry(self, index: int) -> str:
        detail = " ".join(["adaptive layout sample"] * (1 + index % 4))
        return f"**#{index:03d}** · {detail}"

    def _rating_text(self) -> sl.TextLike:
        return L(t"Unrated") if self.rating is None else "\N{BLACK STAR}" * self.rating

    def _page_footer(self, page: int, pages: int) -> sl.text.Message:
        total = len(self.entries)
        return L(t"Measured page {page} of {pages} · {total} samples")

    async def _select_section(self, event: sl.SelectionEvent) -> None:
        self.section = event.values[0]

    async def _cycle_accent(self, event: sl.PressEvent) -> None:
        self.accent_index = (self.accent_index + 1) % len(_ACCENTS)

    async def _click(self, event: sl.PressEvent) -> None:
        self.clicks += 1

    async def _pick_grid(self, event: sl.SelectionEvent) -> None:
        self.grid_pick = f"Selected {event.values[0]}."

    async def _rate(self, event: sl.ScaleEvent) -> None:
        self.rating = event.value

    async def _set_subscribed(self, event: sl.ToggleEvent) -> None:
        self.subscribed = event.value

    async def _record_feedback(self, form: FeedbackForm, event: sl.SubmitEvent) -> None:
        self.feedback_exhibit = form.exhibit or ""
        self.feedback_headline = form.headline or ""
        self.feedback_score = form.score or 0
        await event.notice(L(t"Recorded; the panel behind the form is already showing it."))

    async def _switch_language(self, event: sl.ActionEvent) -> None:
        self.display_locale = "en" if event.locale == "zh-CN" else "zh-CN"
        sd.responder(event).mount.localize(localization_for(self.display_locale))

    async def _action_notice(self, event: sl.ActionEvent) -> None:
        await event.notice(L(t"The semantic action kept its own callback after adaptation."))

    async def _rename_project(self, event: sl.ActionEvent) -> None:
        del event
        self.project_name = "Action Ledger" if self.project_name == "Redstone Squid" else "Redstone Squid"

        def committed(commit: sl.runtime.ActionCommit, aftermath: sl.runtime.Aftermath) -> None:
            with aftermath.start_action("Present commit outcome"):
                self.outcome_result = (
                    f"COMMITTED · local sequence {commit.sequence.value} · action {str(commit.context.action_id)[-8:]}"
                )

        sl.runtime.on_action_commit(committed)

    async def _sibling_edit(self, event: sl.ActionEvent) -> None:
        del event
        self.project_name = "Squid after a sibling edit"
        self.history_result = "A later ordinary action wrote the same register. Undo will now conflict."

    async def _undo_rename(self, event: sl.ActionEvent) -> None:
        del event
        result = await self.action_history.undo()
        self.history_result = self._history_result_text("Undo", result)

    async def _redo_rename(self, event: sl.ActionEvent) -> None:
        del event
        result = await self.action_history.redo()
        self.history_result = self._history_result_text("Redo", result)

    async def _drop_history_conflict(self, event: sl.ActionEvent) -> None:
        del event
        dropped = self.action_history.delete_conflicted()
        self.history_result = (
            "Dropped the conflicted entry without changing state." if dropped else "No conflict to drop."
        )

    async def _cause_rollback(self, event: sl.ActionEvent) -> None:
        del event
        context = sl.runtime.ActionContext.create("Demonstrate rollback")
        try:
            with sl.runtime.fresh_action_transaction(action_context=context):
                self.project_name = "THIS STAGED VALUE MUST NOT APPEAR"

                def rolled_back(rollback: sl.runtime.ActionRollback, aftermath: sl.runtime.Aftermath) -> None:
                    with aftermath.start_action("Present rollback outcome"):
                        self.outcome_result = (
                            f"ROLLED BACK · {rollback.reason.value} · action "
                            f"{str(rollback.context.action_id)[-8:]} · recovery is a fresh action"
                        )

                sl.runtime.on_action_rollback(rolled_back)
                _fail_demo_action()
        except DemoRollback:
            pass

    async def _add_local_review(self, event: sl.ActionEvent) -> None:
        del event
        self.local_document.counter("votes").increment(2)
        self.local_document.set("reviewers").add("mine")

    async def _merge_peer_review(self, event: sl.ActionEvent) -> None:
        del event
        with sl.runtime.fresh_action_transaction(
            action_context=sl.runtime.ActionContext.create("Receive local update", kind=sl.runtime.ActionKind.REMOTE)
        ):
            self.peer_document.import_update(self.local_document.export_since())
        with sl.runtime.fresh_action_transaction(
            action_context=sl.runtime.ActionContext.create("Peer review", kind=sl.runtime.ActionKind.REMOTE)
        ):
            self.peer_document.counter("votes").increment(3)
            self.peer_document.set("reviewers").add("peer")
        with sl.runtime.fresh_action_transaction(
            action_context=sl.runtime.ActionContext.create("Receive peer update", kind=sl.runtime.ActionKind.REMOTE)
        ):
            self.local_document.import_update(self.peer_document.export_since())
        self.replication_result = "Replicas converged. Undo can now preserve the peer's later contribution."

    async def _undo_local_review(self, event: sl.ActionEvent) -> None:
        del event
        result = await self.replication_history.undo()
        self.replication_result = self._history_result_text("Selective undo", result)

    async def _start_publication(self, event: sl.ActionEvent) -> None:
        del event
        self.publication = self.publish_revision.start()

    async def _accept_publication(self, event: sl.ActionEvent) -> None:
        del event
        execution = self.publication
        if execution is None or not isinstance(execution.status, sl.operations.Succeeded):
            return
        with execution.start_action("Accept published revision"):
            self.published_revision = execution.status.value

    async def _create_channel(self, event: sl.ActionEvent) -> None:
        del event
        if self.channel_service.exists:
            self.compensation_result = "The external channel already exists."
            return
        await self.channel_service.create()
        self.channel_present = True
        self.effect_history.record(
            L(t"Create demo channel"),
            compensate=sl.runtime.CompensationSpec(
                operation=self.channel_service.delete,
                idempotency_key=lambda commit: f"layout-demo:undo:{commit.context.action_id}",
            ),
        )

    async def _fail_next_compensation(self, event: sl.ActionEvent) -> None:
        del event
        self.channel_service.fail_next_delete = True
        self.compensation_result = "The next external delete will fail once."

    async def _undo_channel(self, event: sl.ActionEvent) -> None:
        del event
        result = await self.effect_history.undo()
        self.compensation_result = self._history_result_text("Compensation", result)

    def _history_result_text(self, verb: str, result: sl.runtime.HistoryResult) -> str:
        match result.status:
            case sl.runtime.HistoryResultStatus.APPLIED:
                return f"{verb}: APPLIED as action {str(result.action_id)[-8:]}."
            case sl.runtime.HistoryResultStatus.CONFLICT:
                return f"{verb}: CONFLICT; no state changed ({result.conflict})."
            case sl.runtime.HistoryResultStatus.NEEDS_RECONCILIATION:
                return f"{verb}: NEEDS RECONCILIATION after the external effect succeeded."
            case sl.runtime.HistoryResultStatus.FAILED:
                return f"{verb}: FAILED ({result.error}). Retry is a new execution."
            case _:
                return f"{verb}: EMPTY; there is no retained action."

    def _replica_summary(self, votes: int, reviewers: frozenset[str]) -> str:
        names = ", ".join(sorted(reviewers)) or "none"
        return f"votes={votes} · reviewers={names}"

    def _publication_status(self) -> str:
        execution = self.publication
        if execution is None:
            return "not started"
        identity = str(execution.context.execution_id)[:8]
        match execution.status:
            case sl.operations.Pending(progress):
                return f"{identity} · PENDING · {progress}"
            case sl.operations.Succeeded(value):
                return f"{identity} · SUCCEEDED · revision {value}"
            case sl.operations.Failed(error):
                return f"{identity} · FAILED · {error}"
            case sl.operations.Cancelled(progress):
                return f"{identity} · CANCELLED · {progress}"

    def on_unmount(self) -> None:
        self.local_replication_scope.close()
        self.peer_replication_scope.close()


# --- Shared state ---------------------------------------------------------------------------


class Appearance(sl.runtime.Shared[UserScope]):
    """View state two live panels agree on, scoped to one reader.

    Nothing outside the screen wants a theme name, so it is not a service and not a row: it
    is a namespace the panels hold. Writes join the action's transaction, and a change
    reaches the other panel through the bot's topic bus with nothing declared for it.
    """

    accent: int = sl.state(DISCORD_BLUE)
    density: str = sl.state("comfortable")


class Session(sl.runtime.Shared[UserScope]):
    """What one invocation's two panels are looking at, and only for as long as they are."""

    focus: str = sl.state("overview")


APPEARANCE = sl.ContextKey[Appearance]("showcase.appearance")

_DENSITIES = ("comfortable", "compact")


class AppearanceControls(sl.Component):
    """A leaf that never receives the namespace as an argument -- it injects it.

    `inject` is render-time, so the handlers close over the handle the render found rather
    than looking it up again. That is the same rule every injected dependency follows here,
    and a namespace is a dependency.
    """

    history: sl.runtime.History = sl.runtime.history(limit=5)

    def render(self) -> sl.LayoutNode:
        appearance = self.inject(APPEARANCE)
        return sl.primitives.ActionGroup(
            (
                sl.primitives.Button(
                    L(t"Cycle accent"),
                    partial(self._cycle, appearance=appearance),
                    "accent",
                    record=self.history,
                ),
                sl.primitives.Button(
                    L("Density: {density}", density=appearance.density),
                    partial(self._toggle_density, appearance=appearance),
                    "density",
                ),
                sl.primitives.Button(
                    L(t"Undo appearance change"),
                    self._undo,
                    "undo",
                    style=sl.primitives.ActionStyle.SECONDARY,
                ),
            )
        )

    async def _cycle(self, event: sl.PressEvent, *, appearance: Appearance) -> None:
        # A read and a write of the same cell, so this carries a commit precondition: if the
        # other panel moved the accent while this handler ran, the press fails rather than
        # writing a value computed from something that is no longer there.
        current = _ACCENTS.index(appearance.accent) if appearance.accent in _ACCENTS else 0
        appearance.accent = _ACCENTS[(current + 1) % len(_ACCENTS)]

    async def _toggle_density(self, event: sl.PressEvent, *, appearance: Appearance) -> None:
        # Records by hand, unlike the accent button: this control's label names the density
        # it is showing, which would make a confusing name for the entry that changes it.
        appearance.density = _DENSITIES[(_DENSITIES.index(appearance.density) + 1) % len(_DENSITIES)]
        self.history.record(L(t"Change density"))

    async def _undo(self, event: sl.PressEvent) -> None:
        await self.history.undo()


class AppearancePanel(sl.Component):
    """The panel that writes. It provides the namespace rather than passing it down."""

    def __init__(self, appearance: Appearance, session: Session) -> None:
        self.appearance = appearance
        self.session = session
        self.controls = AppearanceControls()

    def render(self) -> sl.LayoutNode:
        self.provide(APPEARANCE, self.appearance)
        return sl.primitives.Panel(
            (
                sl.primitives.Heading(L(t"Appearance")),
                sl.primitives.Text(L("Focus: {focus}", focus=self.session.focus)),
                self.boundary(self.controls, key="controls"),
                sl.primitives.Row(
                    (sl.primitives.Button(L(t"Look at details"), self._focus_details, "focus"),),
                ),
            ),
            accent=self.appearance.accent,
        )

    async def _focus_details(self, event: sl.PressEvent) -> None:
        self.session.focus = "details" if self.session.focus == "overview" else "overview"


class PreviewPanel(sl.Component):
    """The panel that only reads. It declares no dependency and follows both cells anyway."""

    def __init__(self, appearance: Appearance, session: Session) -> None:
        self.appearance = appearance
        self.session = session

    def render(self) -> sl.LayoutNode:
        return sl.primitives.Panel(
            (
                sl.primitives.Heading(L(t"Preview")),
                sl.primitives.Text(
                    L(
                        "This panel re-renders because it read the cells the other one wrote. "
                        "Density: {density} · focus: {focus}",
                        density=self.appearance.density,
                        focus=self.session.focus,
                    )
                ),
            ),
            accent=self.appearance.accent,
        )


class Lobby(sl.Component):
    """A guild lobby whose roster is session membership, not view state.

    Membership belongs to the logical session: it survives a redraw, it is what replacement
    protection reads, and it is what a durable runtime persists. The panel therefore holds no
    roster of its own -- it reads `session.members` and asks for a redraw after each change.
    """

    started_with: int | None = sl.state(None)
    """How many players the game began with. The only fact here that *is* view state."""

    def __init__(self, sessions: sd.SessionRegistry, host_id: int) -> None:
        self.sessions = sessions
        self.host_id = host_id
        self._mount: sd.Mount | None = None

    def mount(self, *, source: sd.host.HostSource, locale: str | None = None) -> sd.Mount:
        # Kept so the panel can find its own session; the mount cannot be handed to the
        # component that renders it any other way.
        self._mount = create_mount(self, source=source, access=sd.Everyone(), locale=locale, timeout=None)
        return self._mount

    def render(self) -> sl.LayoutNode:
        session = self._session()
        if session is None:
            return sl.section(sl.heading(L(t"Lobby")), sl.paragraph(L(t"This lobby has closed.")))
        placement = sp.place_roster(
            tuple(sp.RosterEntry(str(user_id), f"<@{user_id}>", "players") for user_id in sorted(session.members)),
            (sp.RosterSlot("players", L(t"Players"), session.capacity),),
        )
        status = (
            L("Started with {count} players.", count=self.started_with)
            if self.started_with is not None
            else L("{remaining} seats left.", remaining=session.remaining_capacity)
        )
        return sl.section(
            sl.heading(L(t"Lobby")),
            sl.roster(placement, key="lobby-roster", on_join=self._join),
            sl.paragraph(status),
            sl.actions(
                sl.action(L(t"Leave"), self._leave, key="leave"),
                sl.action(L(t"Start"), self._start, key="start"),
                key="lobby",
            ),
        )

    async def _join(self, event: sl.SelectionEvent) -> None:
        session = self._session()
        if session is None:
            return
        # Whether this presser is *allowed* to join is the caller's question, and an async
        # one; it is answered out here, where it cannot stall the session's lock. Only the
        # rule that depends on the roster goes inside, and it is a plain predicate.
        result = await session.join(int(event.actor.id), when=lambda members: self.host_id in members)
        await event.notice(_JOIN_NOTICES[result.status])
        self.invalidate()

    async def _leave(self, event: sl.PressEvent) -> None:
        session = self._session()
        if session is None:
            return
        result = await session.leave(int(event.actor.id))
        await event.notice(_LEAVE_NOTICES[result.status])
        self.invalidate()

    async def _start(self, event: sl.PressEvent) -> None:
        # Everyone may press Join, so the lobby is mounted for everyone; the controls that
        # only members may use consult the roster themselves.
        session = self._session()
        if session is None or not session.has_member(int(event.actor.id)):
            await event.notice(L(t"Join the lobby first."))
            return
        self.started_with = len(session.members)

    def _session(self) -> sd.sessions.Session | None:
        return None if self._mount is None else self.sessions.session_for(self._mount)


_JOIN_NOTICES = {
    sd.sessions.MembershipStatus.JOINED: L(t"You are in."),
    sd.sessions.MembershipStatus.ALREADY_MEMBER: L(t"You had already joined."),
    sd.sessions.MembershipStatus.AT_CAPACITY: L(t"This lobby is full."),
    sd.sessions.MembershipStatus.QUOTA_REACHED: L(t"You are already in a lobby on another server."),
    sd.sessions.MembershipStatus.REFUSED: L(t"The host has left, so the lobby is closed to newcomers."),
    sd.sessions.MembershipStatus.CONFLICT: L(t"Somebody else moved first -- try again."),
    sd.sessions.MembershipStatus.SESSION_FINISHED: L(t"This lobby has closed."),
}

_LEAVE_NOTICES = {
    sd.sessions.MembershipStatus.LEFT: L(t"You have left."),
    sd.sessions.MembershipStatus.NOT_MEMBER: L(t"You were not in this lobby."),
    sd.sessions.MembershipStatus.CONFLICT: L(t"Somebody else moved first -- try again."),
    sd.sessions.MembershipStatus.SESSION_FINISHED: L(t"This lobby has closed."),
}


class LayoutShowcaseCog[BotT: "squid.bot.app.RedstoneSquid"](Cog):
    """Public commands demonstrating the layout engine."""

    def __init__(self, bot: BotT) -> None:
        self.bot = bot
        # Retention state, per §3 of the shared-state plan: the cog outlives every panel, so
        # a reader's accent survives closing and reopening the demo. The pool is the retention
        # policy, written down where the lifetime is known.
        self._appearance = sl.runtime.SharedPool(Appearance, bot.topic_bus)

    @commands.hybrid_group(name="layout")
    async def layout_group(self, ctx: Context[BotT]) -> None:
        """Explore the bot's adaptive interface engine."""
        await ctx.send_help("layout")

    @layout_group.command(name="demo")
    @app_commands.describe(
        section=app_commands.locale_str(_("The exhibit to open first.")),
        entries=app_commands.locale_str(_("How many sample entries the pagination exhibit should hold.")),
    )
    async def demo(
        self,
        ctx: Context[BotT],
        section: DemoSection = "pagination",
        entries: app_commands.Range[int, 20, 200] = 100,
    ) -> None:
        """Open an interactive showcase of squid-layouts."""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        await send_component(
            ctx,
            LayoutShowcase(section=section, entries=entries, locale=locale),
            access=sd.Everyone(),
            locale=locale,
        )

    @layout_group.command(name="shared")
    async def shared(self, ctx: Context[BotT]) -> None:
        """Open two live panels that share one namespace of view state."""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        scope = Opener(ctx.author.id, ctx.guild.id if ctx.guild else None).user()
        appearance = self._appearance.get(scope)
        # Co-existence state: only the two panels hold it, so it is collected when the second
        # of them finishes. Nothing was looking at it, so it wants no pool -- the lifetime the
        # handle already has is the correct one.
        session = Session(self.bot.topic_bus, scope)
        for component in (
            AppearancePanel(appearance, session),
            PreviewPanel(appearance, session),
        ):
            await send_component(
                ctx,
                component,
                access=sd.Owner(ctx.author.id),
                locale=locale,
                reactor=self.bot.layout_reactor,
            )

    @layout_group.command(name="lobby")
    @guild_only()
    async def lobby(self, ctx: Context[BotT]) -> None:
        """Open a four-seat lobby whose roster lives in the session, not the panel."""
        assert ctx.guild is not None
        locale = await resolve_locale(ctx, self.bot.services.settings)
        panel = Lobby(self.bot.mounts, ctx.author.id)
        await self.bot.mounts.open(
            panel.mount(source=ctx, locale=locale),
            destination(ctx, locale=locale),
            key=SessionKey.guild("showcase-lobby", ctx.guild.id),
            actor_id=ctx.author.id,
            capacity=4,
            # The dual of `capacity`: four players per lobby, and one lobby per player, so a
            # reader cannot hold a seat in two servers at once.
            quota=1,
        )


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    """Load the public layout showcase."""
    await bot.add_cog(LayoutShowcaseCog(bot))
