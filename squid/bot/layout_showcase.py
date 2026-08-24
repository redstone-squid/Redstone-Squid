"""Public interactive showcase for the squid-layouts engine."""

from collections.abc import Awaitable, Callable, Iterable, Sequence
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING, Literal

from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Cog, Context, guild_only

import squid_layouts as sl
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
from squid_layouts.discord import SessionKey
from squid_layouts.discord.screens import Opener
from squid_layouts.discord.sessions import UserScope

if TYPE_CHECKING:
    import squid.bot.app


type DemoSection = Literal[
    "pagination",
    "adaptation",
    "degradation",
    "data",
    "ownership",
    "forms",
    "composition",
    "localization",
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
    mount = sl.discord.responder(event).mount
    mount.localize(localization_for("zh-CN"))

# Interpolated values are Markdown-escaped unless wrapped in sl.raw_md().""",
}


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
    rating: int | None = sl.state(None)
    subscribed: bool = sl.state(default=False)
    feedback_exhibit: str = sl.state("")
    feedback_headline: str = sl.state("")
    feedback_score: int = sl.state(0)
    display_locale: str = sl.state("en", persist=False)

    def __init__(self, *, section: DemoSection, entries: int, locale: str | None) -> None:
        self.section = section
        self.entries = tuple(self._entry(index) for index in range(1, entries + 1))
        self.display_locale = locale or "en"
        self.opened_at = datetime.now(UTC)
        self.left = DemoCounter(L(t"Left child"))
        self.right = DemoCounter(L(t"Right child"))

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
            case "ownership":
                exhibit = self._ownership()
            case "forms":
                exhibit = self._forms()
            case "composition":
                exhibit = self._composition()
            case "localization":
                exhibit = self._localization()
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
        sl.discord.responder(event).mount.localize(localization_for(self.display_locale))

    async def _action_notice(self, event: sl.ActionEvent) -> None:
        await event.notice(L(t"The semantic action kept its own callback after adaptation."))


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

    def __init__(self, sessions: sl.discord.SessionRegistry, host_id: int) -> None:
        self.sessions = sessions
        self.host_id = host_id
        self._mount: sl.discord.Mount | None = None

    def mount(self, *, locale: str | None = None) -> sl.discord.Mount:
        # Kept so the panel can find its own session; the mount cannot be handed to the
        # component that renders it any other way.
        self._mount = create_mount(self, access=sl.discord.Everyone(), locale=locale, timeout=None)
        return self._mount

    def render(self) -> sl.LayoutNode:
        session = self._session()
        if session is None:
            return sl.section(sl.heading(L(t"Lobby")), sl.paragraph(L(t"This lobby has closed.")))
        roster = "\n".join(f"- <@{user_id}>" for user_id in sorted(session.members)) or "_empty_"
        status = (
            L("Started with {count} players.", count=self.started_with)
            if self.started_with is not None
            else L("{remaining} seats left.", remaining=session.remaining_capacity)
        )
        return sl.section(
            sl.heading(L("Lobby ({count}/{capacity})", count=len(session.members), capacity=session.capacity)),
            sl.paragraph(roster),
            sl.paragraph(status),
            sl.actions(
                sl.action(L(t"Join"), self._join, key="join"),
                sl.action(L(t"Leave"), self._leave, key="leave"),
                sl.action(L(t"Start"), self._start, key="start"),
                key="lobby",
            ),
        )

    async def _join(self, event: sl.PressEvent) -> None:
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

    def _session(self) -> sl.discord.sessions.Session | None:
        return None if self._mount is None else self.sessions.session_for(self._mount)


_JOIN_NOTICES = {
    sl.discord.sessions.MembershipStatus.JOINED: L(t"You are in."),
    sl.discord.sessions.MembershipStatus.ALREADY_MEMBER: L(t"You had already joined."),
    sl.discord.sessions.MembershipStatus.AT_CAPACITY: L(t"This lobby is full."),
    sl.discord.sessions.MembershipStatus.REFUSED: L(t"The host has left, so the lobby is closed to newcomers."),
    sl.discord.sessions.MembershipStatus.CONFLICT: L(t"Somebody else moved first -- try again."),
    sl.discord.sessions.MembershipStatus.SESSION_FINISHED: L(t"This lobby has closed."),
}

_LEAVE_NOTICES = {
    sl.discord.sessions.MembershipStatus.LEFT: L(t"You have left."),
    sl.discord.sessions.MembershipStatus.NOT_MEMBER: L(t"You were not in this lobby."),
    sl.discord.sessions.MembershipStatus.CONFLICT: L(t"Somebody else moved first -- try again."),
    sl.discord.sessions.MembershipStatus.SESSION_FINISHED: L(t"This lobby has closed."),
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
            access=sl.discord.Everyone(),
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
                access=sl.discord.Owner(ctx.author.id),
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
            panel.mount(locale=locale),
            destination(ctx, locale=locale),
            key=SessionKey.guild("showcase-lobby", ctx.guild.id),
            actor_id=ctx.author.id,
            capacity=4,
        )


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    """Load the public layout showcase."""
    await bot.add_cog(LayoutShowcaseCog(bot))
