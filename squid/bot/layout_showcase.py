"""Public interactive showcase for the squid-layouts engine."""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal

from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Cog, Context

import squid_layouts as sl
from squid.bot.i18n import resolve_locale, t
from squid.bot.ui import DISCORD_BLUE, DISCORD_GREEN, DISCORD_YELLOW, send_component
from squid.core.i18n import _

if TYPE_CHECKING:
    import squid.bot.app


type DemoSection = Literal["tour", "pagination", "adaptation", "composition"]

_ACCENTS = (DISCORD_BLUE, DISCORD_GREEN, DISCORD_YELLOW)

_SOURCE_EXAMPLES = {
    "tour": """class Counter(sl.Component):
    count: int = sl.state(0)

    def render(self):
        return sl.Section(
            (
                sl.Paragraph(f"Count: {self.count}"),
                sl.Actions((sl.Action("increment", "+1", self.increment),), key="counter"),
            ),
            heading="Counter",
        )""",
    "pagination": """return sl.List(
    tuple(sl.ListItem(str(index), entry) for index, entry in enumerate(entries)),
    key="samples",
)
# The target adapter chooses and measures the pages and controls.""",
    "adaptation": """actions = tuple(
    sl.Action(f"action.{index}", f"Action {index}", on_action)
    for index in range(1, 37)
)
return sl.Actions(actions, key="showcase-actions")
# Discord planning preserves them as pickers of 25 and 11.""",
    "composition": """def render(self):
    return sl.Stack(
        (
            self.embed(self.left, key="left"),
            self.embed(self.right, key="right"),
        )
    )""",
}


class DemoCounter(sl.Component):
    """A tiny child component used twice to demonstrate keyed composition."""

    count: int = sl.state(0)

    def __init__(self, label: str, locale: str | None) -> None:
        self.label = label
        self.locale = locale

    def render(self) -> sl.primitives.Node:
        return sl.primitives.Panel(
            (
                sl.primitives.Heading(self.label, level=3),
                sl.primitives.Text(t(self.locale, _("Independent count: {count}"), count=self.count)),
                sl.primitives.Row(
                    (
                        sl.primitives.Button(
                            t(self.locale, _("Increment")),
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

    def __init__(self, *, section: DemoSection, entries: int, locale: str | None) -> None:
        self.section = section
        self.entries = tuple(self._entry(index) for index in range(1, entries + 1))
        self.locale = locale
        self.left = DemoCounter(t(locale, _("Left child")), locale)
        self.right = DemoCounter(t(locale, _("Right child")), locale)

    @sl.computed
    def status(self) -> str:
        return t(
            self.locale,
            _("Section: {section} · reactive clicks: {clicks}"),
            section=self.section,
            clicks=self.clicks,
        )

    def render(self) -> Sequence[sl.LayoutNode]:
        controls = (
            sl.primitives.SelectMenu(
                tuple(
                    sl.primitives.Option(label, value, description, default=self.section == value)
                    for value, label, description in self._sections()
                ),
                self._select_section,
                "section",
                placeholder=t(self.locale, _("Choose an engine exhibit")),
            ),
            sl.primitives.ActionGroup(
                (
                    sl.primitives.Button(
                        t(self.locale, _("Cycle accent")),
                        self._cycle_accent,
                        "accent",
                        style=sl.primitives.ActionStyle.PRIMARY,
                    ),
                    sl.primitives.Button(t(self.locale, _("Reactive click")), self._click, "click"),
                )
            ),
        )
        header = sl.primitives.Panel(
            (
                sl.primitives.Heading(t(self.locale, _("squid-layouts engine showcase"))),
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
            case "composition":
                exhibit = self._composition()
            case _:
                exhibit = self._tour()
        return (*exhibit, self._source_example())

    def _tour(self) -> Sequence[sl.primitives.Node]:
        return (
            sl.primitives.card(
                t(self.locale, _("What this message is doing")),
                t(
                    self.locale,
                    _(
                        "The component renders semantic intent. The planner fits it to Discord's real limits, "
                        "the renderer draws Components V2, and the mount turns state changes into safe edits."
                    ),
                ),
                fields=(
                    sl.primitives.presets.Field(
                        t(self.locale, _("Reactivity")), t(self.locale, _("Change state; the view rebuilds."))
                    ),
                    sl.primitives.presets.Field(
                        t(self.locale, _("Pagination")),
                        t(self.locale, _("Pages are measured from content, footer, and controls together.")),
                    ),
                    sl.primitives.presets.Field(
                        t(self.locale, _("Composition")),
                        t(self.locale, _("Keyed child components keep state and handlers independent.")),
                    ),
                ),
                footer=t(self.locale, _("Use the selector above to switch exhibits in place.")),
            ),
        )

    def _pagination(self) -> Sequence[sl.primitives.Node]:
        return (
            sl.primitives.Panel(
                (
                    sl.primitives.Heading(t(self.locale, _("Target-budget pagination"))),
                    sl.primitives.Text(
                        t(
                            self.locale,
                            _(
                                "There is no fixed entries-per-page value here. The solver fills the available "
                                "Discord text budget and includes the measured footer and navigation controls."
                            ),
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
            sl.Action(f"action.{index}", t(self.locale, _("Action {number}"), number=index), self._action_notice)
            for index in range(1, 37)
        )
        return (
            sl.primitives.card(
                t(self.locale, _("Structural adaptation")),
                t(
                    self.locale,
                    _(
                        "This declares 36 actions, not buttons or menus. The planner preserves all 36 as two "
                        "pickers of 25 and 11 options because that is the best legal Discord representation."
                    ),
                ),
                accent=DISCORD_YELLOW,
            ),
            sl.Actions(actions, key="showcase-actions"),
        )

    def _composition(self) -> Sequence[sl.primitives.Node]:
        return (
            sl.primitives.card(
                t(self.locale, _("Keyed component composition")),
                t(
                    self.locale,
                    _(
                        "These are two instances of the same child class. Embed namespaces their state, actions, "
                        "and lifecycle paths, so clicking one cannot cross-wire the other."
                    ),
                ),
            ),
            self.embed(self.left, key="left"),
            self.embed(self.right, key="right"),
        )

    def _source_example(self) -> sl.Section:
        return sl.Section(
            (
                sl.Paragraph(
                    t(
                        self.locale,
                        _("This is the author-facing declaration; planning chooses the legal Discord shape."),
                    )
                ),
                sl.Code(_SOURCE_EXAMPLES.get(self.section, _SOURCE_EXAMPLES["tour"]), language="python"),
            ),
            heading=t(self.locale, _("Declaration source")),
        )

    def _sections(self) -> tuple[tuple[str, str, str], ...]:
        return (
            ("tour", t(self.locale, _("Guided tour")), t(self.locale, _("Architecture and reactive state"))),
            (
                "pagination",
                t(self.locale, _("Budget pagination")),
                t(self.locale, _("Pages filled from measured limits")),
            ),
            (
                "adaptation",
                t(self.locale, _("Structural adaptation")),
                t(self.locale, _("A large action surface folds compactly")),
            ),
            (
                "composition",
                t(self.locale, _("Composition")),
                t(self.locale, _("Two independently keyed children")),
            ),
        )

    def _entry(self, index: int) -> str:
        detail = " ".join(["adaptive layout sample"] * (1 + index % 4))
        return f"**#{index:03d}** · {detail}"

    def _page_footer(self, page: int, pages: int) -> str:
        return t(
            self.locale,
            _("Measured page {page} of {pages} · {total} samples"),
            page=page,
            pages=pages,
            total=len(self.entries),
        )

    async def _select_section(self, event: sl.SelectionEvent) -> None:
        self.section = event.values[0]

    async def _cycle_accent(self, event: sl.PressEvent) -> None:
        self.accent_index = (self.accent_index + 1) % len(_ACCENTS)

    async def _click(self, event: sl.PressEvent) -> None:
        self.clicks += 1

    async def _action_notice(self, event: sl.ActionEvent) -> None:
        await event.notice(t(self.locale, _("The semantic action kept its own callback after adaptation.")))


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
            locale=locale,
        )


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    """Load the public layout showcase."""
    await bot.add_cog(LayoutShowcaseCog(bot))
