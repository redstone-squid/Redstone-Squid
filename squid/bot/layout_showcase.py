"""Development-only mini-apps demonstrating the UI framework with disposable data."""

import uuid
from typing import TYPE_CHECKING, Literal

from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Context

import squid_ui_discord as sd
from squid.bot.layout_review import Reviewer, ReviewerPanel, ReviewRoom, ReviewSummary
from squid.bot.layout_workbench import BuildDraft, BuildPreview, Workbench
from squid_i18n import tr
from squid_ui_discord.i18n import to_locale_str

if TYPE_CHECKING:
    import squid.bot.app


class LayoutShowcaseCog[BotT: squid.bot.app.RedstoneSquid](sd.Cog[BotT]):
    """Two working examples of reactive Discord applications."""

    @commands.hybrid_group(
        to_locale_str(tr(t"command-layout-name = layout")),
        description=to_locale_str(
            tr(t"command-layout-help = Explore the build workbench or collaborative review room.")
        ),
    )
    async def layout_group(self, ctx: Context[BotT]) -> None:
        """Explore the build workbench or collaborative review room."""
        await ctx.send_help("layout")

    @layout_group.command(
        to_locale_str(tr(t"command-layout-workbench-name = workbench")),
        description=to_locale_str(
            tr(t"command-layout-workbench-help = Edit a sample build and watch a second message update live.")
        ),
    )
    async def workbench(self, ctx: Context[BotT]) -> None:
        """Edit a sample build and watch a second message update live."""
        request = await self.ui.request(ctx)
        draft = BuildDraft()
        editor = await request.respond(
            Workbench(draft),
            session_key=uuid.uuid4(),
        )
        if isinstance(editor, sd.Presented):
            # The preview shares the editor's session and lifetime; it does not return a result.
            await request.respond(
                BuildPreview(draft),
                parent=editor.root,
            )

    @layout_group.command(
        to_locale_str(tr(t"command-layout-review-name = review")),
        description=to_locale_str(
            tr(t"command-layout-review-help = Review together, or operate Alice and Bob yourself in simulated mode.")
        ),
    )
    @app_commands.rename(mode=to_locale_str(tr(t"command-layout-review-mode-name = mode")))
    @app_commands.describe(mode=to_locale_str(tr(t"command-layout-review-mode-help = …")))
    @app_commands.choices(
        mode=[
            app_commands.Choice(
                name=to_locale_str(tr(t"command-layout-review-choice-mode-simulated = simulated")), value="simulated"
            ),
            app_commands.Choice(name=to_locale_str(tr(t"command-layout-review-choice-mode-live = live")), value="live"),
        ]
    )
    async def review(self, ctx: Context[BotT], mode: Literal["simulated", "live"] = "simulated") -> None:
        """Review together, or operate Alice and Bob yourself in simulated mode."""
        request = await self.ui.request(ctx)
        room = ReviewRoom()
        if mode == "simulated":
            room.seats = tuple(Reviewer(ctx.author.id, name) for name in ("Alice", "Bob"))
        summary = await request.respond(
            ReviewSummary(room, ctx.author.id, mode),
            session_key=uuid.uuid4(),
        )
        if isinstance(summary, sd.Presented):
            for seat in room.seats:
                await request.respond(
                    ReviewerPanel(room, seat, simulated=True),
                    parent=summary.root,
                )


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    """Load the development showcase."""
    await bot.add_cog(LayoutShowcaseCog(bot))
