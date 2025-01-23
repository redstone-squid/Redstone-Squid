"""Everything related to querying the database for information."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Cog, Context, hybrid_group
from openai import AsyncOpenAI
import vecs

from bot.submission.ui import DynamicBuildEditButton, BuildInfoView
from bot.utils import RunningMessage, check_is_staff, is_owner_server
from bot import utils
from database.builds import Build, get_all_builds
from database.schema import Status

if TYPE_CHECKING:
    from bot.main import RedstoneSquid
    from postgrest.base_request_builder import APIResponse
    from database.schema import RestrictionAliasRecord, RestrictionRecord, TypeRecord


class SearchCog(Cog):
    def __init__(self, bot: RedstoneSquid):
        self.bot = bot

    @commands.hybrid_command("search")
    async def search_builds(self, ctx: Context, query: str):
        """
        Searches for a build with natural language.

        Args:
            query: The query to search for.
        """
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
        await ctx.send(content=build.original_link, embed=await build.generate_embed())

    @commands.command("search_restrictions")
    @check_is_staff()
    @commands.check(is_owner_server)
    async def search_restrictions(self, ctx: Context, query: str | None):
        """This runs a substring search on the restriction names."""
        async with RunningMessage(ctx) as sent_message:
            if query:
                response: APIResponse[RestrictionRecord] = (
                    await self.bot.db.table("restrictions").select("*").ilike("name", f"%{query}%").execute()
                )
                response_alias: APIResponse[RestrictionAliasRecord] = (
                    await self.bot.db.table("restriction_aliases").select("*").ilike("alias", f"%{query}%").execute()
                )
            else:
                response = await self.bot.db.table("restrictions").select("*").execute()
                response_alias = await self.bot.db.table("restriction_aliases").select("*").execute()
            restrictions = response.data
            aliases = response_alias.data

            description = "\n".join([f"{restriction['id']}: {restriction['name']}" for restriction in restrictions])
            description += "\n"
            description += "\n".join([f"{alias['restriction_id']}: {alias['alias']} (alias)" for alias in aliases])
            await sent_message.edit(embed=utils.info_embed("Restrictions", description))

    @commands.hybrid_command()
    async def list_patterns(self, ctx: Context):
        """Lists all the available patterns."""
        async with RunningMessage(ctx) as sent_message:
            patterns: APIResponse[TypeRecord] = await self.bot.db.table("types").select("*").execute()
            names = [pattern["name"] for pattern in patterns.data]
            await sent_message.edit(
                content="Here are the available patterns:", embed=utils.info_embed("Patterns", ", ".join(names))
            )

    @hybrid_group(name="build", invoke_without_command=True)
    async def build_hybrid_group(self, ctx: Context):
        """Submit, view, confirm and deny submissions."""
        await ctx.send_help("build")

    @build_hybrid_group.command(name="pending")
    async def get_pending_submissions(self, ctx: Context):
        """Shows an overview of all submitted builds pending review."""
        async with utils.RunningMessage(ctx) as sent_message:
            pending_submissions = await get_all_builds(Status.PENDING)

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
    async def view_build(self, ctx: Context, build_id: int):
        """Displays a submission."""
        if ctx.interaction:
            interaction = ctx.interaction
            await interaction.response.defer()
            build = await Build.from_id(build_id)
            if build is None:
                error_embed = utils.error_embed("Error", "No build with that ID.")
                return await interaction.followup.send(embed=error_embed, ephemeral=True)

            view = BuildInfoView(build)
            await view.send(interaction)
        else:
            async with utils.RunningMessage(ctx) as sent_message:
                build = await Build.from_id(build_id)

                if build is None:
                    error_embed = utils.error_embed("Error", "No build with that ID.")
                    return await sent_message.edit(embed=error_embed)

                await sent_message.edit(content=build.original_link, embed=await build.generate_embed())


async def setup(bot: RedstoneSquid):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    bot.add_dynamic_items(DynamicBuildEditButton)
    await bot.add_cog(SearchCog(bot))
