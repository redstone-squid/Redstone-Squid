"""Everything related to querying the database for information."""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

import vecs
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Cog, Context, hybrid_group
from discord.utils import escape_markdown
from openai import AsyncOpenAI
from sqlalchemy import select

from squid.bot import utils
from squid.bot.submission.ui.components import DynamicBuildEditButton
from squid.bot.submission.ui.views import BuildInfoView
from squid.bot.utils import RunningMessage, check_is_owner_server, check_is_staff
from squid.db.builds import Build, get_builds_by_filter, search_smallest_door_records
from squid.db.schema import Restriction, RestrictionAlias, Status, Type

if TYPE_CHECKING:
    import squid.bot


class SearchCog[BotT: "squid.bot.RedstoneSquid"](Cog):
    def __init__(self, bot: BotT):
        self.bot = bot

    @commands.hybrid_command("search_using_sucky_embeddings")
    @app_commands.describe(query="Whatever you want to search for.")
    async def search_builds(self, ctx: Context[BotT], query: str):
        """Searches for a build with natural language."""
        await ctx.defer()
        client = AsyncOpenAI()
        response = await client.embeddings.create(input=query, model="text-embedding-3-small")
        query_vec = response.data[0].embedding
        vx = vecs.create_client(os.environ["DB_CONNECTION"])
        build_vecs = vx.get_or_create_collection(name="builds", dimension=1536)
        result: list[str] = build_vecs.query(query_vec, limit=1)  # type: ignore
        assert len(result) == 1
        build_id = int(result[0])
        build = await Build.from_id(build_id)
        assert build is not None
        await ctx.send(content=build.original_link, embed=await self.bot.for_build(build).generate_embed())

    @commands.hybrid_command("search")
    @app_commands.describe(query="The record's title.")
    async def search_records(self, ctx: Context[BotT], query: str):
        """Searches for a **record** by title."""
        async with RunningMessage(ctx) as sent_message:
            matches = await search_smallest_door_records(query)
            if not matches:
                return await sent_message.edit(
                    embed=utils.error_embed("No results found", "No records match that query.")
                )

            # Use the running message to display the top result
            top_door = matches[0][0]
            build = await Build.from_id(top_door.id)
            assert build is not None, "A record must have a build."
            embed = await self.bot.for_build(build).generate_embed()
            content = f"Top match: {top_door.title} (score: {matches[0][1]})"
            if build.original_link:
                content += f"\n{build.original_link}"
            await sent_message.edit(
                content=content,
                embed=embed,
            )
            other_results = matches[1:]
            await ctx.send(
                f"Found {len(matches) - 1} other records matching your query.\n"
                + "\n".join(f"{door.title} (ID: {door.id}) (score: {score})" for door, score, _ in other_results)
            )

    @commands.command("search_restrictions")
    @check_is_staff()
    @check_is_owner_server()
    async def search_restrictions(self, ctx: Context[BotT], query: str | None):
        """This runs a substring search on the restriction names."""
        async with RunningMessage(ctx) as sent_message:
            async with self.bot.db.async_session() as session:
                stmt = select(Restriction)
                alias_stmt = select(RestrictionAlias)

                if query:
                    stmt = stmt.where(Restriction.name.ilike(f"%{query}%"))
                    alias_stmt = alias_stmt.where(RestrictionAlias.alias.ilike(f"%{query}%"))

                restrictions_task = session.execute(stmt)
                aliases_task = session.execute(alias_stmt)

                restrictions, aliases = await asyncio.gather(restrictions_task, aliases_task)
                restrictions = restrictions.scalars().all()
                aliases = aliases.scalars().all()

                description = "\n".join([f"{r.id}: {r.name}" for r in restrictions])
                description += "\n"
                description += "\n".join([f"{a.restriction_id}: {a.alias} (alias)" for a in aliases])
                await sent_message.edit(embed=utils.info_embed("Restrictions", description))

    @commands.hybrid_command()
    async def list_patterns(self, ctx: Context[BotT]):
        """Lists all the available patterns."""
        async with RunningMessage(ctx) as sent_message:
            async with self.bot.db.async_session() as session:
                stmt = select(Type)
                patterns = (await session.execute(stmt)).scalars().all()
                names = [pattern.name for pattern in patterns]
                await sent_message.edit(
                    content="Here are the available patterns:", embed=utils.info_embed("Patterns", ", ".join(names))
                )

    @hybrid_group(name="build", invoke_without_command=True)
    async def build_hybrid_group(self, ctx: Context[BotT]):
        """Submit, view, confirm and deny submissions."""
        await ctx.send_help("build")

    @build_hybrid_group.command(name="pending")
    async def get_pending_submissions(self, ctx: Context[BotT]):
        """Shows an overview of all submitted builds pending review."""
        async with self.bot.get_running_message(ctx) as sent_message:
            pending_submissions = await get_builds_by_filter(filter={"submission_status": Status.PENDING})

            if len(pending_submissions) == 0:
                desc = "No open submissions."
            else:
                desc = []
                for sub in pending_submissions:
                    # ID - Title
                    # by Creators - submitted by Submitter
                    desc.append(
                        f"**{sub.id}** - {sub.get_title()}\n_by {', '.join(sorted(sub.creators_ign))}_ - _submitted by {sub.submitter_id}_"
                    )
                desc = "\n\n".join(desc)

            em = utils.info_embed(title="Open Records", description=desc)
            await sent_message.edit(embed=em)

    @build_hybrid_group.command(name="view")
    @app_commands.describe(build_id="The ID of the build you want to see.")
    async def view_build(self, ctx: Context[BotT], build_id: int):
        """Displays a submission."""
        if ctx.interaction:
            interaction = ctx.interaction
            await interaction.response.defer()
            build = await Build.from_id(build_id)
            if build is None:
                error_embed = utils.error_embed("Error", "No build with that ID.")
                await interaction.followup.send(embed=error_embed, ephemeral=True)
                return None

            view = BuildInfoView[BotT](build)
            await view.send(interaction)
            return None
        else:
            async with self.bot.get_running_message(ctx) as sent_message:
                build = await Build.from_id(build_id)

                if build is None:
                    error_embed = utils.error_embed("Error", "No build with that ID.")
                    return await sent_message.edit(embed=error_embed)

                await sent_message.edit(
                    content=build.original_link, embed=await self.bot.for_build(build).generate_embed()
                )
            return None

    @build_hybrid_group.command(name="debug")
    @app_commands.describe(build_id="The ID of the build you want to see the debug info.")
    async def debug_build(self, ctx: Context[BotT], build_id: int):
        """Displays a submission's debug info."""
        async with self.bot.get_running_message(ctx) as sent_message:
            build = await Build.from_id(build_id)

            if build is None:
                error_embed = utils.error_embed("Error", "No build with that ID.")
                return await sent_message.edit(embed=error_embed)

            await sent_message.edit(content=escape_markdown(str(build.__dict__)), embed=None)
        return None


async def setup(bot: "squid.bot.RedstoneSquid"):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    bot.add_dynamic_items(DynamicBuildEditButton)
    await bot.add_cog(SearchCog(bot))
