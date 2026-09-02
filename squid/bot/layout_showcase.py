"""Public interactive showcase for the squid-ui engine.

Written for whoever runs the command rather than for the engine's authors: each exhibit
names one problem a Discord message really runs into, makes it happen on screen, and says
which button to press. The declaration that produced it sits behind a disclosure, so the
message a reader gets is the demonstration and not a code listing.
"""

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Sequence
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING, Literal, Never

from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Context, guild_only

import squid_ui as sl
import squid_ui_discord as sd
import squid_ui_widgets as sp
from squid.bot.i18n import localization_for, resolve_locale
from squid.bot.ui import DISCORD_BLUE, DISCORD_GREEN, DISCORD_YELLOW, tr
from squid_replication import ReferenceBackend, Replica, ReplicatedDocument
from squid_ui_discord import SessionKey
from squid_ui_discord.session_specs import OpenContext
from squid_ui_discord.sessions import UserScope

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

_PAGE_SIZE = 6
"""Entries the pagination exhibit asks for per page.

Small on purpose. Letting the solver fill Discord's whole text budget is the more
impressive demonstration and produces a wall of sample rows nobody reads; a page a reader
can take in at a glance still shows the two things that matter — that nothing was dropped,
and that the footer and buttons were measured as part of the page.
"""

_DOOR_KINDS = ("piston door", "trapdoor", "hipster door", "bridge door", "vault door", "glass door")
_DOOR_SIZES = ("2x2", "3x3", "4x4", "5x5", "3x2", "4x3", "6x6")

_SAMPLE_BUILDS = (
    (tr(t"Flush piston door"), "4x4", "1.6s"),
    (tr(t"Hipster trapdoor"), "3x3", "0.9s"),
    (tr(t"Bridge door"), "2x2", "0.7s"),
    (tr(t"Vault door"), "5x5", "2.4s"),
)

_LONG_DESCRIPTION = (
    "This 4x4 flush piston door hides its whole mechanism inside the wall behind it, which is what "
    "makes the extra depth worth paying for: nothing protrudes, nothing is visible from either side "
    "once it is shut, and the observer chain that drives it never crosses the doorway. The opening "
    "sequence runs the top two rows first, so the head-height gap appears before the floor does, "
    "which is the difference between walking through and standing there waiting. Power is fed from a "
    "single line under the threshold, so the design tiles sideways without a second run. It has been "
    "tested against every piston-update quirk we know about, including the one where a "
    "quasi-connected dropper below the frame jams the retraction if the door is triggered twice "
    "inside one tick."
)

_AUDIT_LOG = (
    "09:41 - Kaboom submitted a 4x4 flush piston door",
    "09:44 - Vote opened, 3 of 5 approvals needed",
    "09:52 - Rin approved it",
    "10:03 - Sable approved it",
    "10:11 - Kaboom edited the door's width",
    "10:12 - Vote reset because the build changed",
    "10:30 - Rin approved the edited build",
    "10:41 - Sable approved the edited build",
    "10:58 - Juno approved the edited build",
    "10:58 - Build accepted into the records",
    "11:20 - Record time corrected to 1.6s",
    "11:41 - Screenshot attached by Kaboom",
    "12:05 - Juno flagged a duplicate submission",
    "12:19 - Duplicate merged into this record",
    "13:02 - Sable added the world download",
    "13:30 - Category changed to Flush",
    "14:15 - Record confirmed by a second timer",
    "14:16 - Entry locked for editing",
)

_SOURCE_EXAMPLES = {
    "pagination": """lines = sl.primitives.Lines(
    builds,
    overflow=sl.primitives.Paginate(
        key="samples",          # this list's own cursor, independent of any other
        per=6,                  # a page a reader can take in; fewer if six will not fit
        footer=page_footer,     # measured as part of every page, not added afterwards
    ),
)

# Nothing here counts characters. The page that ships is the one that fits.
return sl.section(sl.heading("A long list"), lines)""",
    "adaptation": """choices = tuple(
    sl.action_control(
        tr(t"Option {number}"),
        on_choice,
        key=f"action.{number}",
    )
    for number in range(1, 37)
)

# Declare 36 choices once. Discord gets pickers of 25 and 11; nothing here splits them.
return sl.action_controls(*choices, key="showcase-actions")""",
    "degradation": """return sl.section(
    sl.heading("What gets cut"),

    # May lose words. Shortened rather than dropped, and the cut is marked.
    sl.budget(sl.truncate(sl.paragraph(description)), min=140, prefer=340),

    # All-or-nothing entries: whole lines go, and it says how many.
    sl.budget(sl.spill(sl.bullets(*log_lines, key="log")), min=140, prefer=300),

    # A promise, so it is never the thing that gets cut.
    sl.note("Nothing here was shortened without telling you."),
)""",
    "data": """return sl.section(
    sl.heading("Numbers, bars and clocks"),

    # A quantity, a proportion and an instant -- not three pre-formatted strings.
    sl.metric(len(builds), "Sample builds"),
    sl.progress(clicks, label="Clicks toward ten", maximum=10),

    # One node; Discord draws it in each reader's own timezone.
    sl.timestamp(opened_at, style=sl.semantic.TimeStyle.RELATIVE),

    # AUTO, so planning may fall back to records when a row will not fit.
    sl.table(columns, *rows, key="builds-table"),
)""",
    "grid": """cells = tuple(
    sp.GridCell(
        f"cell-{index}",
        str(index + 1),
        available=index not in taken,
    )
    for index in range(12)
)

# Cells are variadic. Buttons hold the board while it fits; larger boards lower to
# coordinate or paged selects delivering the same SelectionEvent keys.
return sl.grid(*cells, key="showcase-grid", columns=4, on_pick=self._pick_grid)""",
    "ownership": """# The message owns this one, under its key. No component state backs it.
sl.toggle("First switch", key="ownership.managed")

# The component owns this one: self.subscribed wins every render, and the handler
# is the only route to a new value -- which is what lets a handler refuse one.
sl.toggle(
    "Second switch",
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

# A schema, not a Discord modal. A rejection re-presents it with the complaints
# and everything already typed; **prefill seeds it from state.
return sl.form("Open the feedback form", FeedbackForm(**prefill), key="feedback")""",
    "composition": """class Dashboard(sl.Component[sl.ComponentsV2Target]):
    def __init__(self):
        self.left = Counter("Left")
        self.right = Counter("Right")

    def render(self):
        return sl.stack(
            self.boundary(self.left, key="left"),
            self.boundary(self.right, key="right"),
        )

# Keys namespace state, lifecycle and action ids for each child.""",
    "localization": """def render(self):
    build_title = self.build.title
    return sl.paragraph(
        # tr keeps the catalogue key and the value until the mount plans a locale.
        tr(t"Build: {build_title}")
    )

async def switch_language(self, event: sl.PressEvent) -> None:
    mount = sd.responder(event).mount
    mount.localize(localization_for("zh-CN"))

# Interpolated values are Markdown-escaped unless wrapped in sl.raw_md().""",
    "history": """history: sl.runtime.History = sl.runtime.history(limit=5)

# The whole committed action becomes one conditional inverse plan.
sl.action_control("Rename project", self.rename, key="history.rename", record=self.history)

result = await self.history.undo()
match result.status:
    case sl.runtime.HistoryResultStatus.APPLIED:
        ...  # undo committed as a new action, with fresh versions
    case sl.runtime.HistoryResultStatus.CONFLICT:
        ...  # a later write is intact; nothing was partially restored

# Outcome hooks run after the old transaction is dead. Recovery is a new action.
def rolled_back(rollback, continuation):
    with continuation.start_action("Present failure"):
        self.notice = rollback.reason.value

sl.runtime.on_action_rollback(rolled_back)""",
    "replication": """scope = Replica("browser-a", backend=ReferenceBackend())
document = scope.open("showcase")

# Reads are immutable snapshots; writes are semantic transaction participants.
document.counter("votes").increment(2)
document.set("reviewers").add("you")
history.record("Add my votes")

# Transport is application-owned. Import uses the same commit gate as local state.
peer.import_update(document.export_since())

# The backend plans this action's inverse at the current frontier, preserving
# the peer's later +3 and its tagged-set insertion.
result = await history.undo()""",
    "effects": """@sl.operation(initial="queued")
async def publish(self, progress: sl.operations.ProgressReporter[str]) -> int:
    progress.report("sending")
    return 42

execution = self.publish.start()  # every start has a fresh execution id
with execution.start_action("Keep published revision"):
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


class DemoCounter(sl.Component[sl.ComponentsV2Target]):
    """A tiny child component used twice to demonstrate keyed composition."""

    count: int = sl.state(0)

    def __init__(self, label: sl.TextLike) -> None:
        self.label = label

    def render(self) -> sl.LayoutNode[sl.ComponentsV2Target]:
        count = self.count
        return sl.primitives.Panel(
            (
                sl.primitives.Heading(self.label, level=3),
                sl.primitives.Text(tr(t"Pressed {count} times")),
                sl.primitives.Row(
                    (
                        sl.primitives.Button(
                            tr(t"Add one"),
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
    sl.forms.ChoiceOption("pagination", tr(t"Long lists"), "pagination"),
    sl.forms.ChoiceOption("adaptation", tr(t"Too many choices"), "adaptation"),
    sl.forms.ChoiceOption("degradation", tr(t"Running out of room"), "degradation"),
    sl.forms.ChoiceOption("data", tr(t"Numbers and clocks"), "data"),
    sl.forms.ChoiceOption("ownership", tr(t"Who remembers a switch"), "ownership"),
)


class FeedbackForm(sl.forms.Form):
    """A typed schema, presented however the dispatching frontend can present it."""

    title = tr(t"Tell us about an exhibit")

    exhibit = sl.forms.ChoiceField(label=tr(t"Which exhibit"), options=_FEEDBACK_EXHIBITS)
    headline = sl.forms.TextField(label=tr(t"In one line, what did it show you"), minimum=4, maximum=80)
    detail = sl.forms.TextAreaField(label=tr(t"Anything else"), required=False, maximum=400)
    score = sl.forms.ScaleField(
        label=tr(t"How clear was it"),
        minimum=1,
        maximum=5,
        labels={1: tr(t"Confusing"), 5: tr(t"Clear")},
    )

    def __init__(self, on_recorded: Callable[[FeedbackForm, sl.SubmitEvent], Awaitable[None]], /, **prefill: object):
        super().__init__(**prefill)
        self._on_recorded = on_recorded

    def validate(self) -> Iterable[sl.forms.FormIssue]:
        # Cross-field validation runs only once every field has parsed, so these are the typed
        # values rather than the strings the reader typed.
        if self.score is not None and self.score <= 2 and not self.detail:
            return (sl.forms.FieldError("detail", tr(t"A low score needs a sentence saying why.")),)
        return ()

    async def on_submit(self, event: sl.SubmitEvent) -> None:
        # The submitted values are already bound to this instance, so the handler reads them as
        # declared attributes instead of unpacking `event.values` by key.
        await self._on_recorded(self, event)


class LayoutShowcase(sl.Component[sl.ComponentsV2Target]):
    """One mounted tour of the engine, told as twelve problems a message runs into."""

    section: str = sl.state("pagination")
    accent_index: int = sl.state(0)
    clicks: int = sl.state(0)
    grid_pick: str = sl.state("Nothing picked yet.")
    rating: int | None = sl.state(None)
    subscribed: bool = sl.state(default=False)
    feedback_exhibit: str = sl.state("")
    feedback_headline: str = sl.state("")
    feedback_score: int = sl.state(0)
    display_locale: str = sl.state("en", persist=False)
    project_name: str = sl.state("Redstone Squid")
    history_result: str = sl.state("Nothing recorded yet.", persist=False)
    outcome_result: str = sl.state("Nothing has finished yet.", persist=False)
    replication_result: str = sl.state("Nothing added yet.", persist=False)
    channel_present: bool = sl.state(default=False)
    compensation_result: str = sl.state("No channel yet.", persist=False)
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
        self.left = DemoCounter(tr(t"Left counter"))
        self.right = DemoCounter(tr(t"Right counter"))
        self.channel_service = DemoChannelService()
        self.local_replication_scope = Replica("showcase-local", backend=ReferenceBackend())
        self.peer_replication_scope = Replica("showcase-peer", backend=ReferenceBackend())
        self.local_document: ReplicatedDocument = self.local_replication_scope.open("layout-showcase")
        self.peer_document: ReplicatedDocument = self.peer_replication_scope.open("layout-showcase")

    @sl.operation(initial="queued")
    async def publish_revision(self, progress: sl.operations.ProgressReporter[str]) -> int:
        """Simulate one repeatable external publication execution."""
        progress.report("sending")
        await asyncio.sleep(0)
        return (self.published_revision or 40) + 1

    @sl.computed
    def status(self) -> sl.text.Message:
        clicks = self.clicks
        return tr(t"Redrawn {clicks} times, and each press rebuilt this whole message.")

    def render(self) -> Sequence[sl.LayoutNode[sl.ComponentsV2Target]]:
        controls = (
            sl.primitives.SelectMenu(
                tuple(
                    sl.primitives.Option(label, value, description, default=self.section == value)
                    for value, label, description in self._sections()
                ),
                self._select_section,
                "section",
                placeholder=tr(t"Pick something to look at"),
            ),
            sl.primitives.ControlGroup(
                (
                    sl.primitives.Button(
                        tr(t"Change colour"),
                        self._cycle_accent,
                        "accent",
                        style=sl.primitives.ActionStyle.PRIMARY,
                    ),
                    sl.primitives.Button(tr(t"Redraw"), self._click, "click"),
                )
            ),
        )
        header = sl.primitives.Panel(
            (
                sl.primitives.Heading(tr(t"How this bot draws its messages")),
                # Never: the exhibit below is what may be squeezed, and the "Running out of
                # room" one squeezes hard enough to eat this framing if it is allowed to.
                sl.primitives.Text(
                    tr(t"Each entry below is one problem a Discord message runs into. They are all live."),
                    overflow=sl.primitives.Never(),
                ),
                sl.primitives.Text(self.status, overflow=sl.primitives.Never()),
                *controls,
            ),
            accent=_ACCENTS[self.accent_index],
        )
        return (header, *self._render_section())

    def _render_section(self) -> Sequence[sl.LayoutNode[sl.ComponentsV2Target]]:
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

    def _exhibit(
        self,
        heading: sl.TextLike,
        lead: sl.TextLike,
        *body: sl.ChildLike[sl.ComponentsV2Target],
        steps: Sequence[sl.TextLike] = (),
        accent: int = DISCORD_BLUE,
    ) -> sl.semantic.Section[sl.ComponentsV2Target]:
        """The shape every exhibit shares: name the problem, explain it, say what to press.

        One instruction reads as an aside and becomes a note; several are an order that
        matters, so they become a numbered list. The uniform shape is the point — a reader
        who has understood one exhibit knows where to look in the next eleven.
        """
        match steps:
            case ():
                instructions: tuple[sl.ChildLike[sl.ComponentsV2Target], ...] = ()
            case (only,):
                instructions = (sl.note(only),)
            case _:
                instructions = (
                    sl.bullets(
                        *(sl.bullet(step, key=f"step.{index}") for index, step in enumerate(steps)),
                        key=f"{self.section}.steps",
                        ordered=True,
                    ),
                )
        return sl.section(sl.heading(heading), sl.paragraph(lead), *body, *instructions, accent=accent)

    def _pagination(self) -> Sequence[sl.LayoutNode[sl.ComponentsV2Target]]:
        total = len(self.entries)
        per = _PAGE_SIZE
        return (
            self._exhibit(
                tr(t"A list too long for one message"),
                tr(
                    t"{total} sample builds, {per} to a page. The footer and the two buttons below are "
                    t"measured as part of each page, so if {per} entries were ever too long to fit you "
                    t"would be given fewer -- never a page Discord refuses to send.",
                ),
                sl.primitives.Lines(
                    self.entries,
                    overflow=sl.primitives.Paginate(key="samples", per=_PAGE_SIZE, footer=self._page_footer),
                ),
                steps=(tr(t"Press Next."),),
            ),
        )

    def _adaptation(self) -> Sequence[sl.LayoutNode[sl.ComponentsV2Target]]:
        choices = tuple(
            sl.semantic.ActionControl(f"action.{index}", tr(t"Option {index}"), self._action_notice)
            for index in range(1, 37)
        )
        return (
            self._exhibit(
                tr(t"More choices than Discord has room for"),
                tr(
                    t"This exhibit offers 36 things you can pick, and Discord allows 25 options in one "
                    t"dropdown. No code here splits them up: it says *36 choices*, and what arrived on "
                    t"your screen is a dropdown of 25 and a dropdown of 11."
                ),
                steps=(tr(t"Pick anything from either dropdown -- both halves run the same handler."),),
                accent=DISCORD_YELLOW,
            ),
            sl.semantic.ActionControls(choices, key="showcase-actions"),
        )

    def _degradation(self) -> Sequence[sl.LayoutNode[sl.ComponentsV2Target]]:
        log = sl.bullets(
            *(sl.bullet(line, key=f"log.{index}") for index, line in enumerate(_AUDIT_LOG)),
            key="audit-log",
        )
        return (
            self._exhibit(
                tr(t"What gets cut when the room runs out"),
                tr(
                    t"Sometimes there is more to say than a message can hold. Every block below was told "
                    t"in advance how it wants to be treated when that happens, and this exhibit is "
                    t"squeezed on purpose so you can watch all three at once."
                ),
                sl.bullets(
                    sl.bullet(
                        tr(t"The description may lose words. It is shortened, and the cut is marked."), key="trim"
                    ),
                    sl.bullet(
                        tr(t"The log entries are all or nothing. Whole lines go, and it says how many."), key="cut"
                    ),
                    sl.bullet(tr(t"The last line is never the thing that gets cut, whatever that costs."), key="keep"),
                    key="degradation-policies",
                ),
                sl.budget(sl.truncate(sl.paragraph(_LONG_DESCRIPTION)), min=140, prefer=340),
                sl.budget(sl.spill(log), min=140, prefer=300),
                sl.note(tr(t"Nothing here was shortened without telling you.")),
                accent=DISCORD_YELLOW,
            ),
        )

    def _data(self) -> Sequence[sl.LayoutNode[sl.ComponentsV2Target]]:
        return (
            self._exhibit(
                tr(t"Numbers, bars and clocks"),
                tr(
                    t"None of these three were written out as text. The count is a number, the bar is a "
                    t"value out of ten, and the time is an instant -- Discord draws it in *your* "
                    t"timezone, which is why that one line says something different to every reader."
                ),
                sl.metric(len(self.entries), tr(t"Sample builds")),
                sl.progress(self.clicks, label=tr(t"Redraws toward ten"), maximum=10),
                sl.timestamp(self.opened_at, style=sl.semantic.TimeStyle.RELATIVE, label=tr(t"You opened this")),
                sl.table(
                    sl.columns(sl.column(tr(t"Door")), sl.column(tr(t"Size")), sl.column(tr(t"Fastest"))),
                    *(
                        sl.table_row(name, size, record, key=f"row.{index}")
                        for index, (name, size, record) in enumerate(_SAMPLE_BUILDS)
                    ),
                    key="builds-table",
                ),
                steps=(tr(t"Press Redraw at the top and watch the bar move."),),
            ),
        )

    def _ownership(self) -> Sequence[sl.LayoutNode[sl.ComponentsV2Target]]:
        return (
            self._exhibit(
                tr(t"Two switches that remember in different places"),
                tr(
                    t"The first switch is remembered by the message itself, and this exhibit's code "
                    t"never so much as looks at it. The second is remembered by the exhibit, which gets "
                    t"the last word every time the message is redrawn -- that is what would let it "
                    t"refuse to flip. The stars work the same way as the second switch."
                ),
                sl.fields(
                    sl.field(tr(t"Stars"), self._rating_text()),
                    sl.field(tr(t"Second switch"), tr(t"Following") if self.subscribed else tr(t"Not following")),
                    sl.field(tr(t"First switch"), tr(t"The message is holding this one; the exhibit cannot see it.")),
                ),
                steps=(tr(t"Flip both, move to another entry above, then come back."),),
                accent=DISCORD_GREEN,
            ),
            sl.toggle(
                tr(t"First switch"),
                key="ownership.managed",
                on_label=tr(t"on"),
                off_label=tr(t"off"),
            ),
            sl.toggle(
                tr(t"Second switch"),
                key="ownership.controlled",
                on=sl.controlled(self.subscribed, self._set_subscribed),
                on_label=tr(t"on"),
                off_label=tr(t"off"),
                tone=sl.Tone.SUCCESS,
            ),
            sl.rating(key="ownership.rating", value=sl.controlled(self.rating, self._rate)),
        )

    def _grid(self) -> Sequence[sl.LayoutNode[sl.ComponentsV2Target]]:
        taken = {5, 10}
        cells = tuple(
            sp.GridCell(
                f"cell-{index}",
                str(index + 1),
                available=index not in taken,
                tone=sl.Tone.INFO if index % 2 == 0 else sl.Tone.NEUTRAL,
            )
            for index in range(12)
        )
        return (
            self._exhibit(
                tr(t"A board you can click"),
                tr(
                    t"Twelve squares, four across, two of them already taken. At this size they fit as "
                    t"buttons. A bigger board becomes a pair of row-and-column dropdowns instead, and "
                    t"your pick still arrives at exactly the same place in the code."
                ),
                sl.status(self.grid_pick),
                steps=(tr(t"Press a square."),),
            ),
            sl.grid(*cells, key="showcase-grid", columns=4, on_pick=self._pick_grid),
        )

    def _forms(self) -> Sequence[sl.LayoutNode[sl.ComponentsV2Target]]:
        prefill: dict[str, object] = {}
        if self.feedback_exhibit:
            prefill["exhibit"] = self.feedback_exhibit
        if self.feedback_headline:
            prefill["headline"] = self.feedback_headline
        if self.feedback_score:
            prefill["score"] = self.feedback_score
        return (
            self._exhibit(
                tr(t"A form that checks its own answers"),
                tr(
                    t"The button opens a pop-up. It was written once as a list of questions and the kind "
                    t"of answer each one takes; Discord shows that as a modal. Answer it wrong and it "
                    t"comes back with the complaint on the offending question and everything you had "
                    t"already typed still in place."
                ),
                sl.fields(
                    sl.field(tr(t"You last said"), self.feedback_headline or tr(t"nothing yet")),
                    sl.field(tr(t"About"), self.feedback_exhibit or "--"),
                    sl.field(tr(t"Out of five"), str(self.feedback_score) if self.feedback_score else "--"),
                ),
                steps=(
                    tr(
                        t"Score it 1 and leave Anything else empty. It comes back complaining, with your typing intact."
                    ),
                    tr(t"Send it properly, then open it again -- it is already filled in from what you sent."),
                ),
                accent=DISCORD_GREEN,
            ),
            sl.form(
                tr(t"Open the feedback form"),
                FeedbackForm(self._record_feedback, **prefill),
                key="feedback",
                tone=sl.Tone.SUCCESS,
            ),
        )

    def _composition(self) -> Sequence[sl.LayoutNode[sl.ComponentsV2Target]]:
        return (
            self._exhibit(
                tr(t"Two copies that cannot get crossed"),
                tr(
                    t"Both counters below are the same handful of lines, used twice. Each keeps its own "
                    t"number and each button reaches only its own half, without anybody having to hand "
                    t"out unique names to keep them apart."
                ),
                steps=(tr(t"Press one Add one a few times."),),
            ),
            self.boundary(self.left, key="left"),
            self.boundary(self.right, key="right"),
        )

    def _localization(self) -> Sequence[sl.LayoutNode[sl.ComponentsV2Target]]:
        unsafe = "*shouty title* @everyone [not a link](https://example.com)"
        return (
            self._exhibit(
                tr(t"Switching language without redoing the message"),
                tr(
                    t"None of this was translated when it was written down. Each line keeps its "
                    t"dictionary entry and its values until the moment the message is drawn, so pressing "
                    t"the button redraws this same message in Chinese rather than replacing it."
                ),
                sl.fields(
                    sl.field(tr(t"Language"), self.display_locale),
                    sl.field(tr(t"A title someone typed"), tr(t"Shown safely: {unsafe}")),
                ),
                sl.paragraph(
                    tr(
                        t"That title is somebody else's text. Asterisks, @everyone and fake links are "
                        t"escaped on the way in, so a build title cannot reformat the message or ping "
                        t"the server."
                    )
                ),
                steps=(tr(t"Press Switch language, twice."),),
            ),
            sl.action_controls(
                sl.action_control(tr(t"Switch language"), self._switch_language, key="switch-language"),
                key="localization-actions",
            ),
        )

    def _history(self) -> Sequence[sl.LayoutNode[sl.ComponentsV2Target]]:
        return (
            self._exhibit(
                tr(t"Undo that knows when it is too late"),
                tr(
                    t"Renaming the project writes down what changed. Undo is not a rewind: it is a new "
                    t"action that first checks the name is still the one it recorded. If something else "
                    t"changed it in the meantime, undo refuses rather than trampling the newer value."
                ),
                sl.fields(
                    sl.field(tr(t"Project name"), self.project_name),
                    sl.field(tr(t"Last undo"), self.history_result),
                    sl.field(tr(t"Last outcome"), self.outcome_result),
                ),
                steps=(
                    tr(t"Rename it, then Undo. The name goes back."),
                    tr(
                        t"Rename it, then Someone else edits it, then Undo. This time undo refuses and the newer name stands."
                    ),
                    tr(t"Forget the refusal clears the stuck entry without changing anything."),
                    tr(t"Make it fail sets a new name and then throws. You never see the half-written name."),
                ),
                accent=DISCORD_GREEN,
            ),
            sl.action_controls(
                sl.action_control(
                    tr(t"Rename it"),
                    self._rename_project,
                    key="history.rename",
                    tone=sl.Tone.SUCCESS,
                    record=self.action_history,
                ),
                sl.action_control(tr(t"Someone else edits it"), self._sibling_edit, key="history.sibling"),
                key="history-write-actions",
            ),
            sl.action_controls(
                sl.action_control(tr(t"Undo"), self._undo_rename, key="history.undo"),
                sl.action_control(tr(t"Redo"), self._redo_rename, key="history.redo"),
                sl.action_control(tr(t"Forget the refusal"), self._drop_history_conflict, key="history.drop"),
                sl.action_control(
                    tr(t"Make it fail"), self._cause_rollback, key="history.rollback", tone=sl.Tone.DANGER
                ),
                key="history-outcome-actions",
            ),
        )

    def _replication(self) -> Sequence[sl.LayoutNode[sl.ComponentsV2Target]]:
        return (
            self._exhibit(
                tr(t"Your edit and somebody else's at the same time"),
                tr(
                    t"Two copies of one document, yours and a friend's. You add two votes; they receive "
                    t"that, add three of their own, and send it back, so both copies say five. Then undo "
                    t"yours -- and their three survive, because undo takes back *your* contribution "
                    t"rather than whatever happened last."
                ),
                sl.fields(
                    sl.field(tr(t"Your copy"), self._replica_summary(self.local_document)),
                    sl.field(tr(t"Their copy"), self._replica_summary(self.peer_document)),
                    sl.field(tr(t"Last undo"), self.replication_result),
                ),
                steps=(
                    tr(t"Add my two votes."),
                    tr(t"Let them add three -- both copies now say five."),
                    tr(t"Undo my votes. The total drops to three, not to zero."),
                ),
            ),
            sl.action_controls(
                sl.action_control(
                    tr(t"Add my two votes"),
                    self._add_local_review,
                    key="replication.local",
                    record=self.replication_history,
                    tone=sl.Tone.SUCCESS,
                ),
                sl.action_control(tr(t"Let them add three"), self._merge_peer_review, key="replication.peer"),
                sl.action_control(tr(t"Undo my votes"), self._undo_local_review, key="replication.undo"),
                key="replication-actions",
            ),
        )

    def _effects(self) -> Sequence[sl.LayoutNode[sl.ComponentsV2Target]]:
        return (
            self._exhibit(
                tr(t"Undo that has to go and ask somebody else"),
                tr(
                    t"Creating that channel happens outside the bot, so undo cannot simply forget it: it "
                    t"has to go and delete the thing, and that call can fail. When it does, undo says so "
                    t"and leaves everything as it was, instead of pretending."
                ),
                sl.fields(
                    sl.field(
                        tr(t"The channel out there"), tr(t"exists") if self.channel_service.exists else tr(t"gone")
                    ),
                    sl.field(tr(t"What this message thinks"), tr(t"exists") if self.channel_present else tr(t"gone")),
                    sl.field(tr(t"Last undo"), self.compensation_result),
                ),
                steps=(
                    tr(t"Create the channel."),
                    tr(t"Make the next undo fail, then Undo the channel. It reports the failure and changes nothing."),
                    tr(t"Undo the channel again. The retry carries the same request id, so it can never delete twice."),
                ),
                accent=DISCORD_YELLOW,
            ),
            sl.action_controls(
                sl.action_control(
                    tr(t"Create the channel"), self._create_channel, key="effects.create", tone=sl.Tone.SUCCESS
                ),
                sl.action_control(tr(t"Make the next undo fail"), self._fail_next_compensation, key="effects.fail"),
                sl.action_control(tr(t"Undo the channel"), self._undo_channel, key="effects.undo", tone=sl.Tone.DANGER),
                key="effects-channel-actions",
            ),
            self._exhibit(
                tr(t"A job that runs, and a result you choose to keep"),
                tr(
                    t"Publishing is a job rather than a button press. Every start is its own attempt with "
                    t"its own id, and the number it produces only reaches this message once you keep it."
                ),
                sl.fields(
                    sl.field(tr(t"Attempt"), self._publication_status()),
                    sl.field(
                        tr(t"Revision you kept"),
                        "--" if self.published_revision is None else str(self.published_revision),
                    ),
                ),
                steps=(tr(t"Press Start publishing, then Keep the result."),),
                accent=DISCORD_YELLOW,
            ),
            sl.action_controls(
                sl.action_control(tr(t"Start publishing"), self._start_publication, key="effects.publish"),
                sl.action_control(tr(t"Keep the result"), self._accept_publication, key="effects.accept"),
                key="effects-publish-actions",
            ),
        )

    def _source_example(self) -> sl.semantic.Details[sl.ComponentsV2Target]:
        """The engine's own disclosure, holding the part of the message only authors want.

        Collapsed, this costs one button; expanded, several hundred characters. Making it the
        reader's choice is what keeps every exhibit above short enough to read.
        """
        return sl.details(
            sl.summary(tr(t"Show the code behind this exhibit")),
            sl.paragraph(tr(t"This is what the author wrote. Everything above is what planning made of it.")),
            sl.code(_SOURCE_EXAMPLES.get(self.section, _SOURCE_EXAMPLES["pagination"]), language="python"),
            key="source",
        )

    def _sections(self) -> tuple[tuple[str, sl.TextLike, sl.TextLike], ...]:
        return (
            ("pagination", tr(t"Long lists"), tr(t"More entries than one message can hold")),
            ("adaptation", tr(t"Too many choices"), tr(t"36 options, and Discord allows 25")),
            ("degradation", tr(t"Running out of room"), tr(t"What gets cut, and what never does")),
            ("data", tr(t"Numbers and clocks"), tr(t"A count, a bar, and your own timezone")),
            ("grid", tr(t"A board you can click"), tr(t"Twelve squares that stay clickable")),
            ("ownership", tr(t"Who remembers a switch"), tr(t"The message, or the code behind it")),
            ("forms", tr(t"Forms"), tr(t"A pop-up that checks its own answers")),
            ("composition", tr(t"Two of the same thing"), tr(t"Two counters that cannot get crossed")),
            ("localization", tr(t"Other languages"), tr(t"Switch this message to Chinese, in place")),
            ("history", tr(t"Undo"), tr(t"Undo that refuses when it is too late")),
            ("replication", tr(t"Two people at once"), tr(t"Your edit and theirs, neither one lost")),
            ("effects", tr(t"Undoing something real"), tr(t"When undo has to call the outside world")),
        )

    def _entry(self, index: int) -> str:
        size = _DOOR_SIZES[index % len(_DOOR_SIZES)]
        kind = _DOOR_KINDS[index % len(_DOOR_KINDS)]
        seconds = 0.4 + (index % 17) * 0.1
        return f"**#{index:03d}** \N{MIDDLE DOT} {size} {kind} \N{MIDDLE DOT} {seconds:.1f}s"

    def _rating_text(self) -> sl.TextLike:
        return tr(t"none yet") if self.rating is None else "\N{BLACK STAR}" * self.rating

    def _page_footer(self, page: int, pages: int) -> sl.text.Message:
        total = len(self.entries)
        return tr(t"Page {page} of {pages} \N{MIDDLE DOT} {total} builds in total")

    async def _select_section(self, event: sl.SelectionEvent) -> None:
        self.section = event.values[0]

    async def _cycle_accent(self, event: sl.PressEvent) -> None:
        self.accent_index = (self.accent_index + 1) % len(_ACCENTS)

    async def _click(self, event: sl.PressEvent) -> None:
        self.clicks += 1

    async def _pick_grid(self, event: sl.SelectionEvent) -> None:
        square = int(event.values[0].removeprefix("cell-")) + 1
        self.grid_pick = f"You picked square {square}."

    async def _rate(self, event: sl.ScaleEvent) -> None:
        self.rating = event.value

    async def _set_subscribed(self, event: sl.ToggleEvent) -> None:
        self.subscribed = event.value

    async def _record_feedback(self, form: FeedbackForm, event: sl.SubmitEvent) -> None:
        self.feedback_exhibit = form.exhibit or ""
        self.feedback_headline = form.headline or ""
        self.feedback_score = form.score or 0
        await event.notice(tr(t"Got it -- the panel behind the form is already showing it."))

    async def _switch_language(self, event: sl.ActionEvent) -> None:
        self.display_locale = "en" if event.locale == "zh-CN" else "zh-CN"
        sd.responder(event).message_root.localize(localization_for(self.display_locale))

    async def _action_notice(self, event: sl.ActionEvent) -> None:
        await event.notice(tr(t"That option kept its own handler, whichever dropdown it ended up in."))

    async def _rename_project(self, event: sl.ActionEvent) -> None:
        del event
        self.project_name = "Action Ledger" if self.project_name == "Redstone Squid" else "Redstone Squid"

        def committed(commit: sl.runtime.ActionCommit, continuation: sl.runtime.ActionContinuation) -> None:
            with continuation.start_action("Present commit outcome"):
                self.outcome_result = f"Finished cleanly, as change #{commit.sequence.value}."

        sl.runtime.on_action_commit(committed)

    async def _sibling_edit(self, event: sl.ActionEvent) -> None:
        del event
        self.project_name = "Squid, renamed by somebody else"
        self.history_result = "Somebody else has since written the name. Undo will now refuse."

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
            "Forgot the refused undo. Nothing about the name changed."
            if dropped
            else "There is no refused undo to forget."
        )

    async def _cause_rollback(self, event: sl.ActionEvent) -> None:
        del event
        context = sl.runtime.ActionContext.create("Demonstrate rollback")
        try:
            with sl.runtime.fresh_action_transaction(action_context=context):
                self.project_name = "THIS NAME MUST NEVER APPEAR"

                def rolled_back(
                    rollback: sl.runtime.ActionRollback, continuation: sl.runtime.ActionContinuation
                ) -> None:
                    with continuation.start_action("Present rollback outcome"):
                        self.outcome_result = (
                            f"Failed and rolled back ({rollback.reason.value}). The half-written name never "
                            f"appeared, and this line was written afterwards by a fresh action."
                        )

                sl.runtime.on_action_rollback(rolled_back)
                _fail_demo_action()
        except DemoRollback:
            pass

    async def _add_local_review(self, event: sl.ActionEvent) -> None:
        del event
        self.local_document.counter("votes").increment(2)
        self.local_document.set("reviewers").add("you")

    async def _merge_peer_review(self, event: sl.ActionEvent) -> None:
        del event
        with sl.runtime.fresh_action_transaction(
            action_context=sl.runtime.ActionContext.create("Receive local update", kind=sl.runtime.ActionPurpose.REMOTE)
        ):
            self.peer_document.import_update(self.local_document.export_since())
        with sl.runtime.fresh_action_transaction(
            action_context=sl.runtime.ActionContext.create("Peer review", kind=sl.runtime.ActionPurpose.REMOTE)
        ):
            self.peer_document.counter("votes").increment(3)
            self.peer_document.set("reviewers").add("them")
        with sl.runtime.fresh_action_transaction(
            action_context=sl.runtime.ActionContext.create("Receive peer update", kind=sl.runtime.ActionPurpose.REMOTE)
        ):
            self.local_document.import_update(self.peer_document.export_since())
        self.replication_result = "Both copies agree. Undo can now keep their three and drop only your two."

    async def _undo_local_review(self, event: sl.ActionEvent) -> None:
        del event
        result = await self.replication_history.undo()
        self.replication_result = self._history_result_text("Undo", result)

    async def _start_publication(self, event: sl.ActionEvent) -> None:
        del event
        self.publication = self.publish_revision.start()

    async def _accept_publication(self, event: sl.ActionEvent) -> None:
        del event
        execution = self.publication
        if execution is None or not isinstance(execution.status, sl.operations.Succeeded):
            return
        with execution.start_action("Keep published revision"):
            self.published_revision = execution.status.value

    async def _create_channel(self, event: sl.ActionEvent) -> None:
        del event
        if self.channel_service.exists:
            self.compensation_result = "That channel already exists."
            return
        await self.channel_service.create()
        self.channel_present = True
        self.effect_history.record(
            tr(t"Create demo channel"),
            compensate=sl.runtime.CompensationSpec(
                operation=self.channel_service.delete,
                idempotency_key=lambda commit: f"layout-demo:undo:{commit.context.action_id}",
            ),
        )

    async def _fail_next_compensation(self, event: sl.ActionEvent) -> None:
        del event
        self.channel_service.fail_next_delete = True
        self.compensation_result = "Armed: the next undo will fail once."

    async def _undo_channel(self, event: sl.ActionEvent) -> None:
        del event
        result = await self.effect_history.undo()
        self.compensation_result = self._history_result_text("Undo", result)

    def _history_result_text(self, verb: str, result: sl.runtime.HistoryResult) -> str:
        """Say what happened, in the words a reader would use.

        The status names are the interesting part of this exhibit, so each one keeps its own
        sentence rather than collapsing into "it did not work".
        """
        match result.status:
            case sl.runtime.HistoryResultStatus.APPLIED:
                return f"{verb} worked."
            case sl.runtime.HistoryResultStatus.CONFLICT:
                return f"{verb} refused: this changed after it was recorded, so nothing was touched."
            case sl.runtime.HistoryResultStatus.NEEDS_RECONCILIATION:
                return f"{verb} half-finished: the outside system did its part, so this needs a look."
            case sl.runtime.HistoryResultStatus.FAILED:
                return f"{verb} failed ({result.error}). Pressing it again starts a fresh attempt."
            case _:
                return f"There is nothing left to {verb.lower()}."

    def _replica_summary(self, document: ReplicatedDocument) -> str:
        votes = document.counter("votes").value
        reviewers = ", ".join(sorted(document.set("reviewers").value)) or "nobody"
        return f"{votes} votes \N{MIDDLE DOT} added by {reviewers}"

    def _publication_status(self) -> str:
        execution = self.publication
        if execution is None:
            return "not started"
        identity = str(execution.context.execution_id)[:4]
        match execution.status:
            case sl.operations.Pending(progress):
                return f"{identity} \N{MIDDLE DOT} running \N{MIDDLE DOT} {progress}"
            case sl.operations.Succeeded(value):
                return f"{identity} \N{MIDDLE DOT} done \N{MIDDLE DOT} revision {value}"
            case sl.operations.Failed(error):
                return f"{identity} \N{MIDDLE DOT} failed \N{MIDDLE DOT} {error}"
            case sl.operations.Cancelled(progress):
                return f"{identity} \N{MIDDLE DOT} cancelled \N{MIDDLE DOT} {progress}"

    def on_unmount(self) -> None:
        self.local_replication_scope.close()
        self.peer_replication_scope.close()


# --- shared state ---------------------------------------------------------------------------


class Appearance(sl.runtime.SharedState[UserScope]):
    """View state two live panels agree on, scoped to one reader.

    Nothing outside the screen wants a theme name, so it is not a service and not a row: it
    is a namespace the panels hold. Writes join the action's transaction, and a change
    reaches the other panel through the bot's topic bus with nothing declared for it.
    """

    accent: int = sl.state(DISCORD_BLUE)
    density: str = sl.state("comfortable")


class Session(sl.runtime.SharedState[UserScope]):
    """What one invocation's two panels are looking at, and only for as long as they are."""

    focus: str = sl.state("overview")


APPEARANCE = sl.ContextKey[Appearance]("showcase.appearance")

_DENSITIES = ("comfortable", "compact")


class AppearanceControls(sl.Component[sl.ComponentsV2Target]):
    """A leaf that never receives the namespace as an argument -- it injects it.

    `inject` is render-time, so the handlers close over the handle the render found rather
    than looking it up again. That is the same rule every injected dependency follows here,
    and a namespace is a dependency.
    """

    history: sl.runtime.History = sl.runtime.history(limit=5)

    def render(self) -> sl.LayoutNode[sl.ComponentsV2Target]:
        appearance = self.inject(APPEARANCE)
        density = appearance.density
        return sl.primitives.ControlGroup(
            (
                sl.primitives.Button(
                    tr(t"Change colour"),
                    partial(self._cycle, appearance=appearance),
                    "accent",
                    record=self.history,
                ),
                sl.primitives.Button(
                    tr(t"Density: {density}"),
                    partial(self._toggle_density, appearance=appearance),
                    "density",
                ),
                sl.primitives.Button(
                    tr(t"Undo that"),
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
        self.history.record(tr(t"Change density"))

    async def _undo(self, event: sl.PressEvent) -> None:
        await self.history.undo()


class AppearancePanel(sl.Component[sl.ComponentsV2Target]):
    """The panel that writes. It provides the namespace rather than passing it down."""

    def __init__(self, appearance: Appearance, session: Session) -> None:
        self.appearance = appearance
        self.session = session
        self.controls = AppearanceControls()

    def render(self) -> sl.LayoutNode[sl.ComponentsV2Target]:
        self.provide(APPEARANCE, self.appearance)
        focus = self.session.focus
        return sl.primitives.Panel(
            (
                sl.primitives.Heading(tr(t"Appearance")),
                sl.primitives.Text(tr(t"Looking at: {focus}")),
                self.boundary(self.controls, key="controls"),
                sl.primitives.Row(
                    (sl.primitives.Button(tr(t"Look at details"), self._focus_details, "focus"),),
                ),
            ),
            accent=self.appearance.accent,
        )

    async def _focus_details(self, event: sl.PressEvent) -> None:
        self.session.focus = "details" if self.session.focus == "overview" else "overview"


class PreviewPanel(sl.Component[sl.ComponentsV2Target]):
    """The panel that only reads. It declares no dependency and follows both cells anyway."""

    def __init__(self, appearance: Appearance, session: Session) -> None:
        self.appearance = appearance
        self.session = session

    def render(self) -> sl.LayoutNode[sl.ComponentsV2Target]:
        density = self.appearance.density
        focus = self.session.focus
        return sl.primitives.Panel(
            (
                sl.primitives.Heading(tr(t"Preview")),
                sl.primitives.Text(
                    tr(
                        t"This panel redrew itself because it read the values the other one wrote. Nothing joins them but that. Density: {density}, looking at: {focus}."
                    )
                ),
            ),
            accent=self.appearance.accent,
        )


class Lobby(sd.Screen):
    """A guild lobby whose roster is session membership, not view state.

    Membership belongs to the logical session: it survives a redraw, it is what replacement
    protection reads, and it is what a durable runtime persists. The panel therefore holds no
    roster of its own -- it reads `session.members` and asks for a redraw after each change.
    """

    capacity = 4
    quota = 1
    session = sd.SessionSpec("showcase-lobby", scope=sd.ScopeKind.GUILD, capacity=capacity, quota=quota)
    access = sd.Everyone()
    audience = "public"
    timeout = None

    started_with: int | None = sl.state(None)
    """How many players the game began with. The only fact here that *is* view state."""

    def __init__(self, host_id: int) -> None:
        self.host_id = host_id

    def render(self) -> sl.LayoutNode[sl.ComponentsV2Target]:
        session = self._session()
        members = frozenset({self.host_id}) if session is None else session.members
        capacity = self.capacity if session is None else session.capacity
        placement = sp.place_roster(
            tuple(sp.RosterEntry(str(user_id), f"<@{user_id}>", "players") for user_id in sorted(members)),
            (sp.RosterSlot("players", tr(t"Players"), capacity),),
        )
        count = self.started_with
        remaining = max(0, capacity - len(members)) if capacity is not None else "∞"
        status = (
            tr(t"Started with {count} players.")
            if self.started_with is not None
            else tr(t"{remaining} seats still open.")
        )
        return sl.section(
            sl.heading(tr(t"Lobby")),
            sl.roster(placement, key="lobby-roster", on_join=self._join),
            sl.paragraph(status),
            sl.action_controls(
                sl.action_control(tr(t"Leave"), self._leave, key="leave"),
                sl.action_control(tr(t"Start"), self._start, key="start"),
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
            await event.notice(tr(t"Join the lobby first."))
            return
        self.started_with = len(session.members)

    def _session(self) -> sd.sessions.Session | None:
        guild = self.opening.guild
        if guild is None:
            return None
        session_spec = self.session
        assert session_spec is not None
        sessions = self.opening.runtime.sessions.get(SessionKey.guild(session_spec.name, guild.id))
        return sessions[0] if sessions else None


_JOIN_NOTICES = {
    sd.sessions.MembershipStatus.JOINED: tr(t"You are in."),
    sd.sessions.MembershipStatus.ALREADY_MEMBER: tr(t"You had already joined."),
    sd.sessions.MembershipStatus.AT_CAPACITY: tr(t"This lobby is full."),
    sd.sessions.MembershipStatus.QUOTA_REACHED: tr(t"You are already in a lobby on another server."),
    sd.sessions.MembershipStatus.REFUSED: tr(t"The host has left, so the lobby is closed to newcomers."),
    sd.sessions.MembershipStatus.CONFLICT: tr(t"Somebody else moved first -- try again."),
    sd.sessions.MembershipStatus.SESSION_FINISHED: tr(t"This lobby has closed."),
}

_LEAVE_NOTICES = {
    sd.sessions.MembershipStatus.LEFT: tr(t"You have left."),
    sd.sessions.MembershipStatus.NOT_MEMBER: tr(t"You were not in this lobby."),
    sd.sessions.MembershipStatus.CONFLICT: tr(t"Somebody else moved first -- try again."),
    sd.sessions.MembershipStatus.SESSION_FINISHED: tr(t"This lobby has closed."),
}


class LayoutShowcaseCog[BotT: "squid.bot.app.RedstoneSquid"](sd.Cog[BotT]):
    """Public commands demonstrating the layout engine."""

    def __init__(self, bot: BotT) -> None:
        super().__init__(bot)
        # Retention state, per §3 of the shared-state plan: the cog outlives every panel, so
        # a reader's accent survives closing and reopening the demo. The pool is the retention
        # policy, written down where the lifetime is known.
        self._appearance = sl.runtime.SharedStatePool(Appearance, bot.topic_bus)

    @commands.hybrid_group(name="layout")
    async def layout_group(self, ctx: Context[BotT]) -> None:
        """See how the bot decides what its messages look like."""
        await ctx.send_help("layout")

    @layout_group.command(name="demo")
    @app_commands.describe(
        section=app_commands.locale_str("Which one to open first."),
        entries=app_commands.locale_str("How many sample builds the long-list demo should hold."),
    )
    async def demo(
        self,
        ctx: Context[BotT],
        section: DemoSection = "pagination",
        entries: app_commands.Range[int, 12, 120] = 30,
    ) -> None:
        """Open an interactive tour of how this bot lays out its messages."""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        await self.ui.respond(
            ctx,
            LayoutShowcase(section=section, entries=entries, locale=locale),
            access=sd.Everyone(),
        )

    @layout_group.command(name="shared")
    async def shared(self, ctx: Context[BotT]) -> None:
        """Open two live panels that agree on settings neither of them owns."""
        scope = OpenContext(ctx.author.id, ctx.guild.id if ctx.guild else None).user()
        appearance = self._appearance.get(scope)
        # Co-existence state: only the two panels hold it, so it is collected when the second
        # of them finishes. Nothing was looking at it, so it wants no pool -- the lifetime the
        # handle already has is the correct one.
        session = Session(self.bot.topic_bus, scope)
        for component in (
            AppearancePanel(appearance, session),
            PreviewPanel(appearance, session),
        ):
            await self.ui.respond(
                ctx,
                component,
                access=sd.Owner(ctx.author.id),
                follow_topics=True,
            )

    @layout_group.command(name="lobby")
    @guild_only()
    async def lobby(self, ctx: Context[BotT]) -> None:
        """Open a four-seat lobby anyone can join, with the roster held by the session."""
        assert ctx.guild is not None
        await self.ui.respond(ctx, Lobby(ctx.author.id))


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    """Load the public layout showcase."""
    await bot.add_cog(LayoutShowcaseCog(bot))
