"""Public interactive showcase for the squid-layouts engine."""

from collections.abc import Sequence
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
            sl.paragraph(L("Count: {count}", count=self.count)),
            sl.actions(sl.action("+1", self.increment, key="add"), key="counter"),
            heading="Counter",
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
return sl.section(lines, heading="Measured pagination")""",
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

    @sl.computed(depends=(section, clicks))
    def status(self) -> sl.Message:
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
                heading=L(t"What this message is doing"),
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
            sl.Action(f"action.{index}", L("Action {number}", number=index), self._action_notice)
            for index in range(1, 37)
        )
        return (
            sl.section(
                sl.truncate(
                    sl.paragraph(
                        L(
                            "This declares 36 actions, not buttons or menus. The planner preserves all 36 as "
                            "two pickers of 25 and 11 options because that is the best legal Discord "
                            "representation."
                        )
                    )
                ),
                heading=L(t"Structural adaptation"),
                accent=DISCORD_YELLOW,
            ),
            sl.Actions(actions, key="showcase-actions"),
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
                sl.truncate(
                    sl.paragraph(
                        L(
                            "These are two instances of the same child class. Boundaries namespace their state, "
                            "actions, and lifecycle paths, so clicking one cannot cross-wire the other."
                        )
                    )
                ),
                heading=L(t"Keyed component composition"),
            ),
            self.boundary(self.left, key="left"),
            self.boundary(self.right, key="right"),
        )

    def _localization(self) -> Sequence[sl.LayoutNode]:
        unsafe = "*operator input* @everyone [not a link](https://example.com)"
        return (
            sl.section(
                sl.paragraph(
                    L(t"Messages retain their catalogue key and interpolation values until this mount plans a frame.")
                ),
                sl.fields(
                    sl.field(L(t"Negotiated locale"), self.display_locale),
                    sl.field(L(t"Escaped interpolation"), L(t"Rendered safely: {unsafe}")),
                ),
                sl.note(L(t"Switching language invalidates this same mount; no component is rebuilt or replaced.")),
                heading=L(t"Deferred localization and safe Markdown"),
                accent=DISCORD_BLUE,
            ),
            sl.actions(
                sl.action(L(t"Switch language"), self._switch_language, key="switch-language"),
                key="localization-actions",
            ),
        )

    def _source_example(self) -> sl.Section:
        return sl.Section(
            (
                sl.Paragraph(L(t"This is the author-facing declaration; planning chooses the legal Discord shape.")),
                sl.Code(_SOURCE_EXAMPLES.get(self.section, _SOURCE_EXAMPLES["tour"]), language="python"),
            ),
            heading=L(t"Declaration source"),
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

    def _page_footer(self, page: int, pages: int) -> sl.Message:
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


class LayoutShowcaseCog[BotT: "squid.bot.app.RedstoneSquid"](Cog):
    """Public commands demonstrating the layout engine."""

    def __init__(self, bot: BotT) -> None:
        self.bot = bot

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


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    """Load the public layout showcase."""
    await bot.add_cog(LayoutShowcaseCog(bot))
