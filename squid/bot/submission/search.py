"""Everything related to querying the database for information."""

import asyncio
import logging
from enum import StrEnum
from typing import TYPE_CHECKING, override

import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Cog, Context, when_mentioned
from discord.utils import escape_markdown
from rapidfuzz import process

from squid.bot.i18n import resolve_locale, t
from squid.bot.submission.edit import BuildEditCommands
from squid.bot.submission.groups import BuildCommandGroup
from squid.bot.submission.schematics import BuildSchematicCommands
from squid.bot.submission.search_view import SearchResultsView
from squid.bot.submission.submit import BuildSubmitCommands
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
from squid.bot.utils.permissions import check_is_global_admin
from squid.builds.errors import AliasAlreadyAddedError
from squid.core.i18n import _
from squid.search.domain import SearchMode, SearchRequest, SearchScope, SearchSort, SortDirection

if TYPE_CHECKING:
    import squid.bot.app


logger = logging.getLogger(__name__)


class SearchModeChoice(StrEnum):
    """User-facing names for search ranking modes."""

    keyword = "keyword"
    smart = "smart"


class SearchCog[
    BotT: "squid.bot.app.RedstoneSquid",
](
    BuildEditCommands[BotT],
    BuildSubmitCommands[BotT],
    BuildSchematicCommands[BotT],
):
    def __init__(self, bot: BotT):
        self.bot = bot
        self.queries = bot.services.build_queries
        self.search = bot.services.search
        self.builds = bot.services.builds
        self.inference = bot.services.build_inference
        self.messages = bot.services.messages
        self.restrictions = bot.services.restrictions
        self._schematic_render_tasks: set[asyncio.Task[None]] = set()
        self.register_edit_context_menu()

    @override
    async def cog_unload(self) -> None:
        """Cancel background renders when the owning submission cog is unloaded."""
        for task in self._schematic_render_tasks:
            task.cancel()
        await asyncio.gather(*self._schematic_render_tasks, return_exceptions=True)

    @commands.hybrid_command("search")
    @app_commands.describe(
        query=app_commands.locale_str(_("Search text and filters, e.g. `width:5`.")),
        scope=app_commands.locale_str(_("Search records, builds, tags and fields, or everything.")),
        mode=app_commands.locale_str(_("Use keyword matching or smart meaning-based matching.")),
        sort=app_commands.locale_str(_("Field to sort by, such as width or closing_delay.")),
        direction=app_commands.locale_str(_("Sort low-to-high or high-to-low.")),
    )
    async def search_records(
        self,
        ctx: Context[BotT],
        scope: SearchScope = SearchScope.RECORDS,
        mode: SearchModeChoice = SearchModeChoice.keyword,
        sort: str | None = None,
        direction: SortDirection = SortDirection.ASCENDING,
        *,
        query: str,
    ) -> None:
        """Search records, builds, and metadata using text and field filters."""
        await ctx.defer()
        locale = await resolve_locale(ctx, self.bot.services.settings)
        request = SearchRequest(
            query,
            scope=scope,
            mode=SearchMode.LEXICAL if mode is SearchModeChoice.keyword else SearchMode.SEMANTIC,
            sort=SearchSort(sort, direction) if sort is not None else None,
        )
        page = await self.search.search(request)
        await ctx.send(
            view=SearchResultsView(self.search, request, page, author_id=ctx.author.id, locale=locale),
            allowed_mentions=no_mentions(),
        )

    @commands.hybrid_group(name="restrictions")
    async def restrictions_group(self, ctx: Context[BotT]) -> None:
        """Find restrictions and manage their aliases."""
        await ctx.send_help("restrictions")

    @restrictions_group.command(name="search")
    @app_commands.describe(
        query=app_commands.locale_str(_("Part of a restriction name. Leave blank to list all restrictions."))
    )
    async def search_restrictions(self, ctx: Context[BotT], query: str | None = None):
        """Search restriction names."""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        async with RunningMessage(ctx, locale=locale) as sent_message:
            matches = await self.queries.restrictions(query)
            description = "\n".join(
                f"{item.restriction_id}: {item.name}{' (alias)' if item.is_alias else ''}" for item in matches
            )
            await edit_layout(
                sent_message,
                info_layout(t(locale, _("Restrictions")), description),
                allowed_mentions=no_mentions(),
            )

    @restrictions_group.command(name="add-alias")
    @check_is_global_admin()
    @app_commands.describe(
        restriction=app_commands.locale_str(_("The restriction to add another name for.")),
        alias=app_commands.locale_str(_("The additional name.")),
    )
    async def add_restriction_alias(self, ctx: Context[BotT], restriction: str, alias: str):
        """Add another name for a restriction."""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        async with self.bot.get_running_message(ctx, locale=locale) as sent_message:
            try:
                await self.restrictions.add_alias(restriction, alias)
            except AliasAlreadyAddedError:
                await edit_layout(
                    sent_message,
                    info_layout(t(locale, _("Already added")), t(locale, _("Alias already on this restriction."))),
                    allowed_mentions=no_mentions(),
                )
            else:
                await edit_layout(
                    sent_message,
                    info_layout(t(locale, _("Success")), t(locale, _("Alias added."))),
                    allowed_mentions=no_mentions(),
                )

    @add_restriction_alias.autocomplete("restriction")
    async def restriction_autocomplete(
        self, _interaction: discord.Interaction[BotT], current: str
    ) -> list[app_commands.Choice[str]]:
        """Provide autocomplete for restriction names."""
        if not current:
            return []

        restriction_names = await self.restrictions.names()
        matches = process.extract(
            current,
            restriction_names,
            limit=25,
            score_cutoff=30,
        )
        return [app_commands.Choice(name=match[0], value=match[0]) for match in matches]

    @commands.hybrid_group(name="patterns")
    async def patterns_group(self, ctx: Context[BotT]) -> None:
        """List and search build patterns."""
        await ctx.send_help("patterns")

    @patterns_group.command(name="list")
    async def list_patterns(self, ctx: Context[BotT]):
        """List all available build patterns."""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        async with RunningMessage(ctx, locale=locale) as sent_message:
            names = await self.queries.patterns()
            await edit_layout(
                sent_message,
                info_layout(t(locale, _("Patterns")), ", ".join(names)),
                allowed_mentions=no_mentions(),
            )

    @patterns_group.command(name="search")
    @app_commands.describe(query=app_commands.locale_str(_("A full or partial pattern name.")))
    async def search_patterns(self, ctx: Context[BotT], query: str):
        """Search build pattern names."""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        async with RunningMessage(ctx, locale=locale) as sent_message:
            matches = await self.queries.search_patterns(query)
            description = "\n".join(f"{name} (score: {score:.1f})" for name, score, _ in matches)
            await edit_layout(
                sent_message,
                info_layout(t(locale, _("Patterns")), description or t(locale, _("No patterns match that query."))),
                allowed_mentions=no_mentions(),
            )

    @BuildCommandGroup.build_hybrid_group.command(name="queue")  # type: ignore
    async def get_pending_submissions(self, ctx: Context[BotT]):
        """Shows an overview of all submitted builds pending review."""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        async with self.bot.get_running_message(ctx, locale=locale) as sent_message:
            pending_submissions = await self.queries.pending()

            if len(pending_submissions) == 0:
                desc = t(locale, _("No open submissions."))
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
                info_layout(title=t(locale, _("Open Records")), description=desc),
                allowed_mentions=no_mentions(),
            )

    @BuildCommandGroup.build_hybrid_group.command(name="view")  # type: ignore
    @app_commands.rename(build_id="id")
    @app_commands.describe(build_id=app_commands.locale_str(_("The ID of the build you want to see.")))
    async def view_build(self, ctx: Context[BotT], build_id: int):
        """Displays a submission."""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        if ctx.interaction:
            interaction = ctx.interaction
            await interaction.response.defer()
            build = await self.queries.get(build_id)
            if build is None:
                await interaction.followup.send(
                    view=error_layout(t(locale, _("Error")), t(locale, _("No build with that ID."))),
                    ephemeral=True,
                    allowed_mentions=no_mentions(),
                )
                return None

            view = BuildInfoView[BotT](build)
            await view.send(interaction)
            return None
        async with self.bot.get_running_message(ctx, locale=locale) as sent_message:
            build = await self.queries.get(build_id)

            if build is None:
                return await edit_layout(
                    sent_message,
                    error_layout(t(locale, _("Error")), t(locale, _("No build with that ID."))),
                    allowed_mentions=no_mentions(),
                )

            await edit_layout(
                sent_message,
                await self.bot.for_build(build).render_layout(),
                allowed_mentions=no_mentions(),
            )
        return None

    @BuildCommandGroup.build_hybrid_group.command(name="approve")  # type: ignore
    @check_is_global_admin()
    @app_commands.rename(build_id="id")
    @app_commands.describe(build_id=app_commands.locale_str(_("The ID of the build you want to confirm.")))
    async def confirm_build(self, ctx: Context[BotT], build_id: int):
        """Mark a submission as confirmed and publish it."""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        async with self.bot.get_running_message(ctx, locale=locale) as sent_message:
            await self.builds.confirm(build_id)

            await edit_layout(
                sent_message,
                info_layout(t(locale, _("Success")), t(locale, _("Submission has been confirmed."))),
                allowed_mentions=no_mentions(),
            )

    @BuildCommandGroup.build_hybrid_group.command(name="reject")  # type: ignore
    @check_is_global_admin()
    @app_commands.rename(build_id="id")
    @app_commands.describe(build_id=app_commands.locale_str(_("The ID of the build you want to deny.")))
    async def deny_build(self, ctx: Context[BotT], build_id: int):
        """Mark a submission as denied."""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        async with self.bot.get_running_message(ctx, locale=locale) as sent_message:
            build = await self.builds.deny(build_id)

            await self.bot.for_build(build).update_messages()

            await edit_layout(
                sent_message,
                info_layout(t(locale, _("Success")), t(locale, _("Submission has been denied."))),
                allowed_mentions=no_mentions(),
            )

    @BuildCommandGroup.build_hybrid_group.command(name="debug")  # type: ignore
    @check_is_global_admin()
    @app_commands.rename(build_id="id")
    @app_commands.describe(
        build_id=app_commands.locale_str(_("The ID of the build whose debug details you want to see."))
    )
    async def debug_build(self, ctx: Context[BotT], build_id: int):
        """Display internal details for a build."""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        async with self.bot.get_running_message(ctx, locale=locale) as sent_message:
            build = await self.queries.get(build_id)

            if build is None:
                return await edit_layout(
                    sent_message,
                    error_layout(t(locale, _("Error")), t(locale, _("No build with that ID."))),
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
