"""Everything related to querying the database for information."""

import logging
from typing import TYPE_CHECKING

from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Cog, Context, hybrid_group, when_mentioned
from discord.utils import escape_markdown

from squid.bot.submission.search_view import SearchResultsView
from squid.bot.submission.ui.components import DynamicBuildEditButton
from squid.bot.submission.ui.views import BuildInfoView
from squid.bot.utils.components import (
    edit_layout,
    error_layout,
    info_layout,
    no_mentions,
    text_layout,
)
from squid.bot.utils.embeds import RunningMessage
from squid.search.domain import SearchMode, SearchRequest, SearchScope, SearchSort, SortDirection

if TYPE_CHECKING:
    import squid.bot.app


logger = logging.getLogger(__name__)


class SearchCog[BotT: "squid.bot.app.RedstoneSquid"](Cog):
    def __init__(self, bot: BotT):
        self.bot = bot
        self.queries = bot.services.build_queries
        self.search = bot.services.search

    @commands.hybrid_command("search_using_sucky_embeddings")
    @app_commands.describe(query="Whatever you want to search for.")
    async def search_builds(self, ctx: Context[BotT], query: str):
        """Searches builds semantically, with lexical fallback."""
        await ctx.defer()
        request = SearchRequest(query, scope=SearchScope.BUILDS, mode=SearchMode.SEMANTIC)
        page = await self.search.search(request)
        await ctx.send(
            view=SearchResultsView(self.search, request, page, author_id=ctx.author.id),
            allowed_mentions=no_mentions(),
        )

    @commands.hybrid_command("search")
    @app_commands.describe(
        query="Lucene-style text and filters.",
        scope="What to search.",
        mode="Lexical or semantic ranking.",
        sort="Numeric or text field to sort by, such as width or a data-tag query name.",
        direction="Sort low-to-high or high-to-low.",
    )
    async def search_records(
        self,
        ctx: Context[BotT],
        scope: SearchScope = SearchScope.RECORDS,
        mode: SearchMode = SearchMode.LEXICAL,
        sort: str | None = None,
        direction: SortDirection = SortDirection.ASCENDING,
        *,
        query: str,
    ) -> None:
        """Search records, builds, and metadata using text and field filters."""
        await ctx.defer()
        request = SearchRequest(
            query,
            scope=scope,
            mode=mode,
            sort=SearchSort(sort, direction) if sort is not None else None,
        )
        page = await self.search.search(request)
        await ctx.send(
            view=SearchResultsView(self.search, request, page, author_id=ctx.author.id),
            allowed_mentions=no_mentions(),
        )

    @commands.command("search_restrictions")
    async def search_restrictions(self, ctx: Context[BotT], query: str | None):
        """This runs a substring search on the restriction names."""
        async with RunningMessage(ctx) as sent_message:
            matches = await self.queries.restrictions(query)
            description = "\n".join(
                f"{item.restriction_id}: {item.name}{' (alias)' if item.is_alias else ''}" for item in matches
            )
            await edit_layout(
                sent_message,
                info_layout("Restrictions", description),
                allowed_mentions=no_mentions(),
            )

    @commands.hybrid_command()
    async def list_patterns(self, ctx: Context[BotT]):
        """Lists all the available patterns."""
        async with RunningMessage(ctx) as sent_message:
            names = await self.queries.patterns()
            await edit_layout(
                sent_message,
                info_layout("Patterns", ", ".join(names)),
                allowed_mentions=no_mentions(),
            )

    @commands.command("search_patterns")
    async def search_patterns(self, ctx: Context[BotT], query: str):
        """This runs a fuzzy search on the pattern names."""
        async with RunningMessage(ctx) as sent_message:
            matches = await self.queries.search_patterns(query)
            description = "\n".join(f"{name} (score: {score:.1f})" for name, score, _ in matches)
            await edit_layout(
                sent_message,
                info_layout("Patterns", description or "No patterns match that query."),
                allowed_mentions=no_mentions(),
            )

    @hybrid_group(name="build")
    async def build_hybrid_group(self, ctx: Context[BotT]):
        """Submit, view, confirm and deny submissions."""
        await ctx.send_help("build")

    @build_hybrid_group.command(name="pending")  # pyright: ignore[reportFunctionMemberAccess]
    async def get_pending_submissions(self, ctx: Context[BotT]):
        """Shows an overview of all submitted builds pending review."""
        async with self.bot.get_running_message(ctx) as sent_message:
            pending_submissions = await self.queries.pending()

            if len(pending_submissions) == 0:
                desc = "No open submissions."
            else:
                desc = []
                for sub in pending_submissions:
                    # ID - Title
                    # by Creators - submitted by Submitter
                    desc.append(
                        f"**{sub.id}** - {sub.title}\n_by {', '.join(sorted(sub.creators_ign))}_ - _submitted by {sub.submitter_id}_"
                    )
                desc = "\n\n".join(desc)

            await edit_layout(
                sent_message,
                info_layout(title="Open Records", description=desc),
                allowed_mentions=no_mentions(),
            )

    @build_hybrid_group.command(name="view")  # pyright: ignore[reportFunctionMemberAccess]
    @app_commands.describe(build_id="The ID of the build you want to see.")
    async def view_build(self, ctx: Context[BotT], build_id: int):
        """Displays a submission."""
        if ctx.interaction:
            interaction = ctx.interaction
            await interaction.response.defer()
            build = await self.queries.get(build_id)
            if build is None:
                await interaction.followup.send(
                    view=error_layout("Error", "No build with that ID."),
                    ephemeral=True,
                    allowed_mentions=no_mentions(),
                )
                return None

            view = BuildInfoView[BotT](build)
            await view.send(interaction)
            return None
        async with self.bot.get_running_message(ctx) as sent_message:
            build = await self.queries.get(build_id)

            if build is None:
                return await edit_layout(
                    sent_message,
                    error_layout("Error", "No build with that ID."),
                    allowed_mentions=no_mentions(),
                )

            await edit_layout(
                sent_message,
                await self.bot.for_build(build).render_layout(),
                allowed_mentions=no_mentions(),
            )
        return None

    @build_hybrid_group.command(name="debug")  # pyright: ignore[reportFunctionMemberAccess]
    @app_commands.describe(build_id="The ID of the build you want to see the debug info.")
    async def debug_build(self, ctx: Context[BotT], build_id: int):
        """Displays a submission's debug info."""
        async with self.bot.get_running_message(ctx) as sent_message:
            build = await self.queries.get(build_id)

            if build is None:
                return await edit_layout(
                    sent_message,
                    error_layout("Error", "No build with that ID."),
                    allowed_mentions=no_mentions(),
                )

            await edit_layout(
                sent_message,
                text_layout(escape_markdown(str(build.__dict__))),
                allowed_mentions=no_mentions(),
            )
        return None

    @Cog.listener("on_command_error")
    async def mention_fallback_search(self, ctx: Context[BotT], exception: commands.CommandError, /) -> None:  # type: ignore[override]
        """Fallback search when the bot is mentioned and no command is found."""

        assert ctx.command is None, "This listener should only handle non-commands."

        # Only handle CommandNotFound exceptions
        if not isinstance(exception, commands.CommandNotFound):
            return

        # Only handle messages that mention the bot
        content = ctx.message.content
        mention_variants = when_mentioned(ctx.bot, ctx.message)
        for mention in mention_variants:
            if content.startswith(mention):
                trimmed_content = content[len(mention) :].strip()
                break
        else:
            return  # Bot was not mentioned

        print(trimmed_content)

        # This should never happen, but just in case
        if ctx.invoked_parents or ctx.invoked_subcommand:  # pragma: no cover
            logger.warning("A CommandNotFound is being raised despite a subcommand being invoked.")
            return  # don't interfere with other commands

        # For some reason, empty messages are not caught by CommandNotFound
        assert trimmed_content != "", "Trimmed content should not be empty."

        try:
            build_id = int(trimmed_content)
        except ValueError:
            await ctx.invoke(self.search_records, query=trimmed_content)
            return
        await ctx.invoke(self.view_build, build_id=build_id)


async def setup(bot: "squid.bot.app.RedstoneSquid"):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    bot.add_dynamic_items(DynamicBuildEditButton)
    await bot.add_cog(SearchCog(bot))
