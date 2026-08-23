"""Public interactive showcase for the squid-layouts engine."""

from collections.abc import Sequence
from functools import partial
from typing import TYPE_CHECKING, Literal

from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Cog, Context

import squid_layouts as sl
from squid.bot.i18n import resolve_locale
from squid.bot.ui import DISCORD_BLUE, DISCORD_GREEN, DISCORD_YELLOW, L, localization_for, send_component
from squid.core.i18n import _

if TYPE_CHECKING:
    import squid.bot.app


type DemoSection = Literal["tour", "pagination", "adaptation", "degradation", "composition", "localization"]

_ACCENTS = (DISCORD_BLUE, DISCORD_GREEN, DISCORD_YELLOW)

_SOURCE_EXAMPLES = {
    "tour": """class Counter(sl.Component):
    count: int = sl.state(0)

    def render(self):
        # Assigning declared state invalidates the owning mount.
        return sl.section(
            sl.heading("Counter"),
            sl.paragraph(L("Count: {count}", count=self.count)),
            sl.actions(sl.action("+1", self.increment, key="add"), key="counter"),
        )

    async def increment(self, event: sl.PressEvent) -> None:
        self.count += 1""",
    "pagination": """lines = sl.primitives.Lines(
    entries,
    join="\\n\\n",
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


class LayoutShowcase(sl.Component):
    """One mounted tour of reactivity, planning, pagination, and composition."""

    section: str = sl.state("tour")
    accent_index: int = sl.state(0)
    clicks: int = sl.state(0)
    display_locale: str = sl.state("en", persist=False)

    def __init__(self, *, section: DemoSection, entries: int, locale: str | None) -> None:
        self.section = section
        self.entries = tuple(self._entry(index) for index in range(1, entries + 1))
        self.display_locale = locale or "en"
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
            case "pagination":
                exhibit = self._pagination()
            case "adaptation":
                exhibit = self._adaptation()
            case "degradation":
                exhibit = self._degradation()
            case "composition":
                exhibit = self._composition()
            case "localization":
                exhibit = self._localization()
            case _:
                exhibit = self._tour()
        return (*exhibit, self._source_example())

    def _tour(self) -> Sequence[sl.LayoutNode]:
        return (
            sl.section(
                sl.heading(L(t"What this message is doing")),
                # The body is the card's shock absorber: truncate lets it give up characters
                # under pressure before a field or the footer loses any.
                sl.truncate(
                    sl.paragraph(
                        L(
                            "The component renders semantic intent. The planner fits it to Discord's real "
                            "limits, the renderer draws Components V2, and the mount turns state changes into "
                            "safe edits."
                        )
                    )
                ),
                sl.fields(
                    sl.field(L(t"Reactivity"), L(t"Change state; the view rebuilds.")),
                    sl.field(
                        L(t"Pagination"),
                        L(t"Pages are measured from content, footer, and controls together."),
                    ),
                    sl.field(
                        L(t"Composition"),
                        L(t"Keyed child components keep state and handlers independent."),
                    ),
                ),
                sl.note(L(t"Use the selector above to switch exhibits in place.")),
            ),
        )

    def _pagination(self) -> Sequence[sl.primitives.Node]:
        return (
            sl.primitives.Panel(
                (
                    sl.primitives.Heading(L(t"Target-budget pagination")),
                    sl.primitives.Text(
                        L(
                            "There is no fixed entries-per-page value here. The solver fills the available "
                            "Discord text budget and includes the measured footer and navigation controls."
                        )
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
                sl.truncate(
                    sl.paragraph(
                        L(
                            "This declares 36 actions, not buttons or menus. The planner preserves all 36 as "
                            "two pickers of 25 and 11 options because that is the best legal Discord "
                            "representation."
                        )
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

    def _composition(self) -> Sequence[sl.LayoutNode]:
        return (
            sl.section(
                sl.heading(L(t"Keyed component composition")),
                sl.truncate(
                    sl.paragraph(
                        L(
                            "These are two instances of the same child class. Boundaries namespace their state, "
                            "actions, and lifecycle paths, so clicking one cannot cross-wire the other."
                        )
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
                sl.semantic.Paragraph(L(t"This is the author-facing declaration; planning chooses the legal Discord shape.")),
                sl.semantic.Code(_SOURCE_EXAMPLES.get(self.section, _SOURCE_EXAMPLES["tour"]), language="python"),
            ),
        )

    def _sections(self) -> tuple[tuple[str, sl.TextLike, sl.TextLike], ...]:
        return (
            ("tour", L(t"Guided tour"), L(t"Architecture and reactive state")),
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

    def _page_footer(self, page: int, pages: int) -> sl.text.Message:
        total = len(self.entries)
        return L(t"Measured page {page} of {pages} · {total} samples")

    async def _select_section(self, event: sl.SelectionEvent) -> None:
        self.section = event.values[0]

    async def _cycle_accent(self, event: sl.PressEvent) -> None:
        self.accent_index = (self.accent_index + 1) % len(_ACCENTS)

    async def _click(self, event: sl.PressEvent) -> None:
        self.clicks += 1

    async def _switch_language(self, event: sl.ActionEvent) -> None:
        self.display_locale = "en" if event.locale == "zh-CN" else "zh-CN"
        sl.discord.responder(event).mount.localize(localization_for(self.display_locale))

    async def _action_notice(self, event: sl.ActionEvent) -> None:
        await event.notice(L(t"The semantic action kept its own callback after adaptation."))


# --- Shared state ---------------------------------------------------------------------------


class Appearance(sl.runtime.Shared[int]):
    """View state two live panels agree on, scoped to one reader.

    Nothing outside the screen wants a theme name, so it is not a service and not a row: it
    is a namespace the panels hold. Writes join the action's transaction, and a change
    reaches the other panel through the bot's topic bus with nothing declared for it.
    """

    accent: int = sl.state(DISCORD_BLUE)
    density: str = sl.state("comfortable")


class Session(sl.runtime.Shared[int]):
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


class LayoutShowcaseCog[BotT: "squid.bot.app.RedstoneSquid"](Cog):
    """Public commands demonstrating the layout engine."""

    def __init__(self, bot: BotT) -> None:
        self.bot = bot
        # Retention state, per §3 of the shared-state plan: the cog outlives every panel, so
        # a reader's accent survives closing and reopening the demo. The dict is the retention
        # policy, written down where the lifetime is known.
        self._appearance: dict[int, Appearance] = {}

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
        section: DemoSection = "tour",
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
        appearance = self._appearance.setdefault(ctx.author.id, Appearance(self.bot.topic_bus, ctx.author.id))
        # Co-existence state: only the two panels hold it, so it is collected when the second
        # of them finishes. Nothing was looking at it, and that is the correct lifetime.
        session = Session(self.bot.topic_bus, ctx.author.id)
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


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    """Load the public layout showcase."""
    await bot.add_cog(LayoutShowcaseCog(bot))
