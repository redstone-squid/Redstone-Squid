"""Development-only mini-apps demonstrating the UI framework with disposable data."""

import uuid
from typing import TYPE_CHECKING, Literal

from discord.ext import commands
from discord.ext.commands import Context

import squid_ui_discord as sd
from squid.bot.layout_review import Reviewer, ReviewerPanel, ReviewRoom, ReviewSummary
from squid.bot.layout_workbench import BuildDraft, BuildPreview, Workbench
from squid_ui_discord.sessions import UserScope

if TYPE_CHECKING:
    import squid.bot.app


class LayoutShowcaseCog[BotT: squid.bot.app.RedstoneSquid](sd.Cog[BotT]):
    """Two working examples of reactive Discord applications."""

    @commands.hybrid_group(name="layout")
    async def layout_group(self, ctx: Context[BotT]) -> None:
        """Explore the build workbench or collaborative review room."""
        await ctx.send_help("layout")

    @layout_group.command(name="workbench")
    async def workbench(self, ctx: Context[BotT]) -> None:
        """Edit a sample build and watch a second message update live."""
        request = await self.ui.request(ctx)
        draft = BuildDraft(self.bot.topic_bus, UserScope(ctx.author.id))
        editor = await request.respond(
            Workbench(draft),
            access=sd.Owner(ctx.author.id),
            follow_topics=True,
            session=sd.SessionSpec("layout-workbench"),
            session_key=uuid.uuid4(),
        )
        if isinstance(editor, sd.Presented):
            await request.respond(
                BuildPreview(draft),
                access=sd.Owner(ctx.author.id),
                follow_topics=True,
                parent=editor.root,
                session=sd.SessionSpec("layout-preview"),
            )

    @layout_group.command(name="review")
    async def review(self, ctx: Context[BotT], mode: Literal["simulated", "live"] = "simulated") -> None:
        """Review together, or operate Alice and Bob yourself in simulated mode."""
        request = await self.ui.request(ctx)
        bus = self.bot.topic_bus
        room = ReviewRoom(bus, UserScope(ctx.author.id))
        if mode == "simulated":
            room.seats = tuple(Reviewer(bus, UserScope(ctx.author.id), name) for name in ("Alice", "Bob"))
        summary = await request.respond(
            ReviewSummary(room, bus, ctx.author.id, mode),
            access=sd.Everyone(),
            follow_topics=True,
            session=sd.SessionSpec("layout-review"),
            session_key=uuid.uuid4(),
        )
        if isinstance(summary, sd.Presented):
            for seat in room.seats:
                await request.respond(
                    ReviewerPanel(room, seat, simulated=True),
                    access=sd.Owner(ctx.author.id),
                    follow_topics=True,
                    parent=summary.root,
                    session=sd.SessionSpec("layout-reviewer"),
                )


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    """Load the development showcase."""
    await bot.add_cog(LayoutShowcaseCog(bot))
