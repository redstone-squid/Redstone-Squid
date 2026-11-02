"""Handles reaction-based voting for various purposes."""

import contextlib
import logging
from typing import TYPE_CHECKING, override

import discord
from discord import app_commands
from discord.ext.commands import Cog, Context, guild_only, hybrid_group

from squid.bot.i18n import resolve_locale, t
from squid.bot.reactions import ReactionClearEvent, ReactionEvent
from squid.bot.utils.components import no_mentions, text_layout
from squid.bot.utils.permissions import build_subject
from squid.bot.voting.poll_wizard import PollModal
from squid.bot.voting.publisher import DiscordPollPublisher
from squid.bot.voting.sessions import start_delete_log_vote
from squid.core.i18n import _
from squid.permissions.domain.catalogue import (
    VOTE_LOG_DELETE_CAST,
    VOTE_POLL_CLOSE_ANY,
    VOTE_WEIGHT_STAFF,
)
from squid.runtime import JobHandle
from squid.voting.domain import VoteActor, VoteKind, VoteRejection

if TYPE_CHECKING:
    import squid.bot.app


logger = logging.getLogger(__name__)

REJECTION_MESSAGES = {
    VoteRejection.NOT_FOUND: _("That message is not an open vote."),
    VoteRejection.CLOSED: _("That vote is already closed."),
    VoteRejection.NOT_ELIGIBLE: _("You do not have a trusted role."),
    VoteRejection.INVALID_OPTION: _("That option is not available on this vote."),
    VoteRejection.WRONG_GUILD: _("That vote belongs to a different server."),
    VoteRejection.NOT_AUTHORIZED: _("Only the poll creator or staff can do that."),
}
"""One localizable sentence per typed rejection.

Keyed by the enum rather than formatted from it, so adding a rejection to the domain
fails the lookup here instead of leaking `not_eligible` into a user's channel.
"""

_QUIET_REJECTIONS = frozenset({VoteRejection.NOT_FOUND, VoteRejection.CLOSED, VoteRejection.INVALID_OPTION})
"""Refusals a reaction should not be answered with.

Racing a close, or reacting with an emoji that is not an option, is ordinary and
would otherwise put a bot message in the channel for every stray reaction.
"""


def describe_rejection(locale: str | None, rejection: VoteRejection) -> str:
    """Render a typed rejection as a localized sentence."""
    return t(locale, REJECTION_MESSAGES[rejection])


class VoteCog[BotT: "squid.bot.app.RedstoneSquid"](Cog):
    def __init__(self, bot: BotT):
        self.bot = bot
        self.vote_service = bot.services.votes
        self.publisher = DiscordPollPublisher(bot)
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

    async def on_reaction_add(self, event: ReactionEvent) -> None:
        """Record a ballot, keeping it secret unless the poll publishes ballots."""
        payload = event.payload
        # This must be before the removal of the reaction to prevent the bot from removing its own reaction
        if self.bot.user is not None and payload.user_id == self.bot.user.id:
            return

        snapshot = await self.vote_service.get_session(payload.message_id)
        if snapshot is None or not snapshot.is_open:
            return

        message = await event.message()
        user = await event.resolve_member()
        if message is None or user is None or user.bot:
            return

        emoji_name = str(payload.emoji)
        if snapshot.option_by_emoji(emoji_name, payload.guild_id or 0) is None:
            return

        if snapshot.should_remove_reaction_on_cast():
            self._track(
                self.bot.background_tasks.start(
                    self._remove_reaction(message, payload.emoji, user),
                    name=f"remove-vote-reaction-{payload.message_id}-{payload.user_id}",
                )
            )

        if payload.guild_id is None:
            return  # Voting in DMs is not implemented.

        actor = await self._actor(user, snapshot.kind)
        previous = snapshot.selection_for(actor.account_id)
        result = await self.vote_service.cast_vote(payload.message_id, actor, emoji_name)
        if result.rejection is not None:
            await self._report_rejection(message, result.rejection)
            return
        if result.session is None:
            return

        # A public poll keeps reactions as the visible ballot, so a changed vote has
        # to have its previous reaction taken back or the message would show both.
        if not result.session.is_anonymous and previous is not None and previous.emoji != emoji_name:
            await self._remove_reaction(message, previous.emoji, user)
        await self.bot.refresh_posts("vote_session", str(snapshot.id))

    async def on_reaction_remove(self, event: ReactionEvent) -> None:
        """Synchronize reaction removal for polls that publicly retain reactions."""
        payload = event.payload
        snapshot = await self.vote_service.get_session(payload.message_id)
        if snapshot is None or not snapshot.is_open or snapshot.is_anonymous:
            return
        selection = next((item for item in snapshot.selections if item.discord_id == payload.user_id), None)
        if selection is None or selection.emoji != str(payload.emoji):
            return
        member = await event.resolve_member()
        if member is None or member.bot:
            return
        actor = await self._actor(member, snapshot.kind)
        # Re-casting the same option toggles it off, which is what removing the
        # reaction means for a poll whose reactions are the ballots.
        result = await self.vote_service.cast_vote(payload.message_id, actor, selection.emoji)
        if result.accepted and result.session is not None:
            await self.bot.refresh_posts("vote_session", str(result.session.id))

    async def on_reaction_clear(self, event: ReactionClearEvent) -> None:
        """Restore the offered options after a moderator clears a vote card."""
        await self._restore_reactions(event.payload.message_id)

    async def on_reaction_clear_emoji(self, event: ReactionClearEvent) -> None:
        """Restore the offered options after one emoji is cleared from a vote card."""
        await self._restore_reactions(event.payload.message_id)

    async def _restore_reactions(self, message_id: int) -> None:
        """Put the configured baseline reactions back, without inferring lost ballots.

        Ballots live in the database, so a clear costs the affordance and not the
        vote. Nothing here tries to reconstruct who had reacted: for an anonymous
        session that information was never on the message to begin with.
        """
        snapshot = await self.vote_service.get_session(message_id)
        if snapshot is None or not snapshot.is_open:
            return
        location = next((item for item in snapshot.messages if item.id == message_id), None)
        if location is None:
            return
        message = await self.bot.get_or_fetch_message(location.channel_id, message_id)
        if message is None:
            return
        for option in snapshot.options_for_guild(location.guild_id):
            with contextlib.suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
                await message.add_reaction(option.emoji)

    @staticmethod
    async def _remove_reaction(
        message: discord.Message, emoji: discord.PartialEmoji | str, user: discord.abc.Snowflake
    ) -> None:
        """Take one reaction off a message, tolerating a message or permission that is gone."""
        with contextlib.suppress(discord.NotFound, discord.Forbidden):
            await message.remove_reaction(emoji, user)

    async def _report_rejection(self, message: discord.Message, rejection: VoteRejection) -> None:
        """Tell a voter why their ballot was refused, in their server's language."""
        if rejection in _QUIET_REJECTIONS:
            return
        locale = await resolve_locale(message, self.bot.services.settings)
        with contextlib.suppress(discord.Forbidden, discord.NotFound):
            await message.channel.send(
                view=text_layout(describe_rejection(locale, rejection)),
                allowed_mentions=no_mentions(),
            )

    @hybrid_group(name="vote")
    async def vote_group(self, ctx: Context[BotT]) -> None:
        """Start and manage votes."""
        await ctx.send_help("vote")

    @hybrid_group(name="poll")
    @guild_only()
    async def poll_group(self, ctx: Context[BotT]) -> None:
        """Create and manage multi-option polls."""
        await ctx.send_help("poll")

    async def _open_poll_wizard(self, ctx: Context[BotT]) -> None:
        """Open the ephemeral wizard, shared by `/poll create` and its old alias."""
        if ctx.interaction is None:
            await ctx.send("Use the slash command `/poll create` to open the poll editor.")
            return
        await ctx.interaction.response.send_modal(PollModal(self.publisher))  # pyrefly: ignore[no-matching-overload]

    @poll_group.command(name="create")
    @guild_only()
    async def create_poll(self, ctx: Context[BotT]) -> None:
        """Create a multi-option poll through an ephemeral preview wizard."""
        await self._open_poll_wizard(ctx)

    @vote_group.command(name="poll")
    @guild_only()
    async def poll_alias(self, ctx: Context[BotT]) -> None:
        """Deprecated alias for `/poll create`."""
        await self._open_poll_wizard(ctx)

    @poll_group.command(name="close")
    @guild_only()
    async def close_poll(self, ctx: Context[BotT], message: discord.Message) -> None:
        """Close a poll early as its creator or a configured staff member."""
        assert ctx.guild is not None and isinstance(ctx.author, discord.Member)
        locale = await resolve_locale(ctx, self.bot.services.settings)
        result = await self.vote_service.close(message.id, await self._actor(ctx.author, VoteKind.GENERIC))
        if result.rejection is not None or result.session is None:
            await ctx.send(describe_rejection(locale, result.rejection or VoteRejection.NOT_FOUND), ephemeral=True)
            return
        await self.bot.refresh_posts("vote_session", str(result.session.id))
        await ctx.send(t(locale, _("Poll closed.")), ephemeral=True)

    @poll_group.command(name="refresh")
    @guild_only()
    async def refresh_poll(self, ctx: Context[BotT], message: discord.Message) -> None:
        """Refresh a poll's cached role weights."""
        assert ctx.guild is not None and isinstance(ctx.author, discord.Member)
        locale = await resolve_locale(ctx, self.bot.services.settings)
        snapshot = await self.vote_service.get_session(message.id)
        if snapshot is None:
            await ctx.send(describe_rejection(locale, VoteRejection.NOT_FOUND), ephemeral=True)
            return
        actor = await self._actor(ctx.author, VoteKind.GENERIC)
        # Refreshing recomputes the weights a close would act on, so it is gated by the
        # same rule rather than a second copy of it.
        rejection = snapshot.can_close(actor)
        if rejection is not None:
            await ctx.send(describe_rejection(locale, rejection), ephemeral=True)
            return
        result = await self.vote_service.refresh(message.id)
        if result.session is not None:
            await self.bot.refresh_posts("vote_session", str(result.session.id))
        suffix = "" if result.complete else f" Some accounts could not be resolved: {result.unresolved_account_ids}."
        await ctx.send(f"{t(locale, _('Poll weights refreshed.'))}{suffix}", ephemeral=True)

    async def _actor(self, member: discord.Member, kind: VoteKind, *, account_id: int | None = None) -> VoteActor:
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

    async def resolve(self, account_id: int, discord_id: int, guild_id: int, kind: VoteKind) -> VoteActor | None:
        """Resolve current member facts for a service-level weight refresh."""
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return None
        member = guild.get_member(discord_id)
        if member is None:
            try:
                member = await guild.fetch_member(discord_id)
            except discord.NotFound, discord.Forbidden:
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
            await start_delete_log_vote(
                self.bot,
                author_id=ctx.author.id,
                target_message=target_message,
                published_message=message,
            )


async def setup(bot: squid.bot.app.RedstoneSquid):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(VoteCog(bot))
