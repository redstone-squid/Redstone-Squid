"""Handles reaction-based voting for various purposes."""

import contextlib
import logging
from typing import TYPE_CHECKING, Literal, cast, override

import discord
from discord import app_commands
from discord.ext.commands import Cog, Context, guild_only, hybrid_group

from squid.bot._types import GuildMessageable
from squid.bot.i18n import resolve_locale, t
from squid.bot.reactions import ReactionClearEvent, ReactionEvent
from squid.bot.utils.components import no_mentions, text_layout
from squid.bot.utils.permissions import build_subject
from squid.bot.voting.base_session import AbstractVoteSession
from squid.bot.voting.build_session import BuildVoteSession
from squid.bot.voting.delete_log_session import DeleteLogVoteSession
from squid.bot.voting.generic_session import GenericVoteSession
from squid.bot.voting.poll_wizard import PollModal
from squid.core.i18n import _
from squid.permissions.domain.catalogue import (
    VOTE_LOG_DELETE_CAST,
    VOTE_POLL_CLOSE_ANY,
    VOTE_WEIGHT_STAFF,
)
from squid.runtime import JobHandle
from squid.voting.domain import VoteActor, VoteChoice, VoteKindLiteral, VoteOption
from squid.voting.errors import InvalidVoteConfigurationError

if TYPE_CHECKING:
    import squid.bot.app


logger = logging.getLogger(__name__)


class VoteCog[BotT: "squid.bot.app.RedstoneSquid"](Cog):
    def __init__(self, bot: BotT):
        self.bot = bot
        self.vote_service = bot.services.votes
        self._background_tasks: set[JobHandle] = set()
        self.vote_service.set_actor_resolver(self)
        self.bot.reactions.subscribe(self)

    @override
    async def cog_unload(self) -> None:
        self.bot.reactions.unsubscribe(self)
        await self.bot.background_tasks.cancel(*self._background_tasks)

    def _track(self, handle: JobHandle) -> None:
        """Hold a handle for cancellation on unload, dropping the settled ones.

        A JobHandle has no completion callback, so the set is swept on insert
        rather than pruned as each job finishes.
        """
        self._background_tasks = {tracked for tracked in self._background_tasks if not tracked.finished.is_set()}
        self._background_tasks.add(handle)

    async def get_vote_session(
        self, message_id: int, *, status: Literal["open", "closed"] | None = None
    ) -> AbstractVoteSession | GenericVoteSession | None:
        """Gets a vote session from the database.

        Args:
            message_id: The message ID of the vote session.
            status: The status of the vote session. If None, it will get any status.
        """
        snapshot = await self.vote_service.get_session(message_id)
        if snapshot is None or (status is not None and snapshot.status != status):
            return None
        if snapshot.kind == "build":
            return await BuildVoteSession.from_id(self.bot, snapshot.id)
        if snapshot.kind == "delete_log":
            return await DeleteLogVoteSession.from_id(self.bot, snapshot.id)
        if snapshot.kind == "generic":
            return await GenericVoteSession.from_id(self.bot, snapshot.id)
        logger.error("Unknown vote session kind: %s", snapshot.kind)
        msg = f"Unknown vote session kind: {snapshot.kind}"
        raise NotImplementedError(msg)

    async def on_reaction_add(self, event: ReactionEvent) -> None:
        """Handles reactions to update vote counts anonymously."""
        payload = event.payload
        # This must be before the removal of the reaction to prevent the bot from removing its own reaction
        if payload.user_id == self.bot.user.id:  # type: ignore
            return

        vote_session = await self.get_vote_session(payload.message_id, status="open")
        if vote_session is None:
            return

        message = await event.message()
        user = await event.resolve_member()
        if message is None or user is None:
            return
        channel = cast(GuildMessageable, message.channel)
        if user.bot:
            return  # Ignore bot reactions

        emoji_name = str(payload.emoji)
        snapshot = await self.vote_service.get_session(payload.message_id)
        if snapshot is None:
            return
        guild_options = snapshot.options_for_guild(payload.guild_id or 0)
        if emoji_name not in {option.emoji for option in guild_options}:
            return
        user_id = payload.user_id

        anonymous = snapshot.kind != "generic" or snapshot.poll is None or snapshot.poll.visibility != "visible_live"
        if anonymous:
            remove_reaction_task = self.bot.background_tasks.start(
                message.remove_reaction(payload.emoji, user),
                name=f"remove-vote-reaction-{payload.message_id}-{payload.user_id}",
            )
            self._track(remove_reaction_task)

        if isinstance(vote_session, DeleteLogVoteSession) and payload.guild_id is None:
            # Voting in DMs is not implemented
            return

        guild = self.bot.get_guild(payload.guild_id) if payload.guild_id is not None else None
        member = guild.get_member(user_id) if guild is not None else None
        if member is None:
            return
        actor = await self._actor(member, snapshot.kind)
        previous = next(
            (selection for selection in snapshot.selections if selection.account_id == actor.account_id), None
        )
        result = await self.vote_service.cast_vote(
            payload.message_id,
            actor,
            emoji_name,
        )
        if result.rejection == "not_eligible":
            locale = await resolve_locale(message, self.bot.services.settings)
            await channel.send(
                view=text_layout(t(locale, _("You do not have a trusted role."))),
                allowed_mentions=no_mentions(),
            )
            return
        if not result.accepted or result.session is None:
            return

        vote_session.apply_persisted_state(result.session)
        if (
            snapshot.kind == "generic"
            and snapshot.poll is not None
            and snapshot.poll.visibility == "visible_live"
            and previous is not None
            and previous.emoji != emoji_name
        ):
            with contextlib.suppress(discord.NotFound, discord.Forbidden):
                await message.remove_reaction(previous.emoji, user)
        await vote_session.update_messages()

    async def on_reaction_remove(self, event: ReactionEvent) -> None:
        """Synchronize reaction removal for polls that publicly retain reactions."""
        payload = event.payload
        snapshot = await self.vote_service.get_session(payload.message_id)
        if (
            snapshot is None
            or snapshot.kind != "generic"
            or snapshot.status != "open"
            or snapshot.poll is None
            or snapshot.poll.visibility != "visible_live"
        ):
            return
        selection = next((item for item in snapshot.selections if item.discord_id == payload.user_id), None)
        if selection is None or selection.emoji != str(payload.emoji):
            return
        member = await event.resolve_member()
        if member is None or member.bot:
            return
        actor = await self._actor(member, snapshot.kind)
        result = await self.vote_service.cast_vote(payload.message_id, actor, selection.emoji)
        if result.accepted and result.session is not None:
            session = GenericVoteSession(self.bot, result.session)
            await session.update_messages()

    async def on_reaction_clear(self, event: ReactionClearEvent) -> None:
        """Ignore reaction clears; vote sessions remove anonymous reactions eagerly."""

    async def on_reaction_clear_emoji(self, event: ReactionClearEvent) -> None:
        """Ignore emoji clears; vote sessions remove anonymous reactions eagerly."""

    @hybrid_group(name="vote")
    async def vote_group(self, ctx: Context[BotT]) -> None:
        """Start and manage votes."""
        await ctx.send_help("vote")

    @vote_group.command(name="poll")
    @guild_only()
    async def poll(self, ctx: Context[BotT]) -> None:
        """Create a multi-option poll through an ephemeral preview wizard."""
        if ctx.interaction is None:
            await ctx.send("Use the slash command `/vote poll` to open the poll editor.")
            return
        await ctx.interaction.response.send_modal(PollModal(self))  # pyrefly: ignore[no-matching-overload]

    @vote_group.command(name="close")
    @guild_only()
    async def close_poll(self, ctx: Context[BotT], message: discord.Message) -> None:
        """Close a poll early as its creator or a configured staff member."""
        assert ctx.guild is not None and isinstance(ctx.author, discord.Member)
        result = await self.vote_service.close(message.id, await self._actor(ctx.author, "generic"))
        if not result.accepted or result.session is None:
            await ctx.send(f"Could not close poll: {result.rejection}.", ephemeral=True)
            return
        await GenericVoteSession(self.bot, result.session).update_messages()
        await ctx.send("Poll closed.", ephemeral=True)

    @vote_group.command(name="refresh")
    @guild_only()
    async def refresh_poll(self, ctx: Context[BotT], message: discord.Message) -> None:
        """Refresh a poll's cached role weights."""
        assert ctx.guild is not None and isinstance(ctx.author, discord.Member)
        snapshot = await self.vote_service.get_session(message.id)
        if snapshot is None or snapshot.kind != "generic" or snapshot.poll is None:
            await ctx.send("That message is not a poll.", ephemeral=True)
            return
        actor = await self._actor(ctx.author, "generic")
        if snapshot.poll.guild_id != ctx.guild.id or (
            snapshot.author_account_id != actor.account_id and VOTE_POLL_CLOSE_ANY.name not in actor.capabilities
        ):
            await ctx.send("Only the poll creator or staff can refresh it.", ephemeral=True)
            return
        result = await self.vote_service.refresh(message.id)
        if result.session is not None:
            await GenericVoteSession(self.bot, result.session).update_messages()
        suffix = "" if result.complete else f" Some accounts could not be resolved: {result.unresolved_account_ids}."
        await ctx.send(f"Poll weights refreshed.{suffix}", ephemeral=True)

    async def parse_poll_options(self, interaction: discord.Interaction, value: str) -> tuple[VoteOption, ...]:
        """Validate wizard option lines and fill missing aliases from the guild palette."""
        if interaction.guild is None:
            msg = "Polls can only be created in a server."
            raise InvalidVoteConfigurationError(msg)
        lines = [line.strip() for line in value.splitlines() if line.strip()]
        if not 2 <= len(lines) <= 10:
            msg = "Enter between 2 and 10 option lines."
            raise InvalidVoteConfigurationError(msg)
        palette = (await self.vote_service.emoji_preset(interaction.guild.id, "generic")).options
        options: list[VoteOption] = []
        for index, line in enumerate(lines):
            if "|" in line:
                emoji, label = (part.strip() for part in line.split("|", 1))
            else:
                if index >= len(palette):
                    msg = "The configured generic emoji palette does not have enough entries for these options."
                    raise InvalidVoteConfigurationError(msg)
                emoji, label = palette[index].emoji, line
            if not emoji or not label:
                msg = "Each option needs a non-empty emoji and label."
                raise InvalidVoteConfigurationError(msg)
            parsed = discord.PartialEmoji.from_str(emoji)
            if parsed.is_custom_emoji():
                custom = interaction.guild.get_emoji(parsed.id or 0)
                if custom is None or not custom.is_usable():
                    msg = f"The custom emoji {emoji} is not accessible to this bot."
                    raise InvalidVoteConfigurationError(msg)
            options.append(
                VoteOption(
                    emoji,
                    VoteChoice.GENERIC,
                    identifier=str(index + 1),
                    guild_id=interaction.guild.id,
                    label=label,
                    position=index,
                )
            )
        if len({option.emoji for option in options}) != len(options):
            msg = "Poll option emojis must be unique."
            raise InvalidVoteConfigurationError(msg)
        return tuple(options)

    async def _actor(
        self, member: discord.Member, kind: VoteKindLiteral, *, account_id: int | None = None
    ) -> VoteActor:
        """Resolve one member's vote capabilities in a single permission load.

        The tiers this replaces cost up to four round trips here -- a global
        admin lookup, a guild lookup and a settings read, twice over.
        """
        del kind  # Every kind's nodes are resolved together; one load answers all.
        subject = await build_subject(self.bot, member, member.guild.id)
        capabilities = await self.bot.services.permissions.capabilities(
            subject,
            (VOTE_LOG_DELETE_CAST, VOTE_WEIGHT_STAFF, VOTE_POLL_CLOSE_ANY),
        )
        if account_id is None:
            account = await self.bot.services.accounts.get_or_create_account(member.id)
            assert account.id is not None
            account_id = account.id
        return VoteActor(
            account_id,
            member.id,
            member.guild.id,
            frozenset(role.id for role in member.roles),
            capabilities=capabilities,
        )

    async def resolve(self, account_id: int, discord_id: int, guild_id: int, kind: VoteKindLiteral) -> VoteActor | None:
        """Resolve current member facts for a service-level weight refresh."""
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return None
        member = guild.get_member(discord_id)
        if member is None:
            try:
                member = await guild.fetch_member(discord_id)
            except (discord.NotFound, discord.Forbidden):
                return None
        return await self._actor(member, kind, account_id=account_id)

    @vote_group.command(name="delete")
    @app_commands.rename(target_message="message")
    @app_commands.describe(target_message=app_commands.locale_str(_("The message to hold a deletion vote for.")))
    async def start_vote(self, ctx: Context[BotT], target_message: discord.Message):
        """Start a vote to delete a message."""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        # Check if guild_id matches the current guild
        if ctx.guild != target_message.guild:
            await ctx.send(
                view=text_layout(t(locale, _("The message is not from this guild."))),
                allowed_mentions=no_mentions(),
            )
            return

        async with self.bot.get_running_message(ctx, locale=locale) as message:
            await DeleteLogVoteSession.create(
                self.bot, [message], author_id=ctx.author.id, target_message=target_message
            )


async def setup(bot: "squid.bot.app.RedstoneSquid"):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(VoteCog(bot))
