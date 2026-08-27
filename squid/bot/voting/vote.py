"""Handles reaction-based voting for various purposes."""

import contextlib
import logging
from typing import TYPE_CHECKING, override

import discord
from discord import app_commands
from discord.ext.commands import Cog

import squid_ui_discord as sd
from squid.accounts.domain import IdentityProvider
from squid.bot.consent import ensure_consented_account
from squid.bot.i18n import resolve_locale, t
from squid.bot.operations import run_command_operation
from squid.bot.reactions import ReactionClearEvent, ReactionEvent
from squid.bot.ui import error_node, info_node, render_payload, text_node
from squid.bot.voting.actors import describe_rejection, resolve_actor
from squid.bot.voting.poll_wizard import present_poll_form
from squid.bot.voting.publisher import DiscordPollPublisher
from squid.bot.voting.sessions import start_delete_log_vote
from squid.core.i18n import _
from squid.runtime import JobHandle
from squid.voting.domain import VoteActor, VoteKind, VoteRejection
from squid_ui_discord import send_to

if TYPE_CHECKING:
    import squid.bot.app


logger = logging.getLogger(__name__)

_QUIET_REJECTIONS = frozenset({VoteRejection.NOT_FOUND, VoteRejection.CLOSED, VoteRejection.INVALID_OPTION})
"""Refusals a reaction should not be answered with.

Racing a close, or reacting with an emoji that is not an option, is ordinary and
would otherwise put a bot message in the channel for every stray reaction.
"""


class VoteCog[BotT: "squid.bot.app.RedstoneSquid"](Cog):
    def __init__(self, bot: BotT):
        self.bot = bot
        self.vote_service = bot.services.votes
        self.publisher = DiscordPollPublisher(bot)
        self._background_tasks: set[JobHandle] = set()
        self.vote_service.set_actor_resolver(self)
        self.bot.reactions.subscribe(self)
        # A context menu cannot be declared with a decorator on a bound method, so it is
        # built here and taken back down on unload; the tree is the bot's, not the cog's.
        # https://github.com/Rapptz/discord.py/issues/7823#issuecomment-1086830458
        self.delete_ctx_menu = app_commands.ContextMenu(
            name="Vote to Delete",
            callback=self.delete_vote_context_menu,
        )
        self.bot.tree.add_command(self.delete_ctx_menu)

    @override
    async def cog_unload(self) -> None:
        self.bot.reactions.unsubscribe(self)
        self.bot.tree.remove_command(self.delete_ctx_menu.name, type=self.delete_ctx_menu.type)
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

        account_id = await self._consented_account_id(user.id)
        if account_id is None:
            await self._decline_unconsented_vote(message, user, payload.emoji)
            return

        actor = await resolve_actor(self.bot, user, account_id=account_id)
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
        # Read-only resolution: someone with no account cannot have a selection here,
        # and removing a reaction is no reason to write a row for them.
        account_id = await self.bot.account_ids.resolve(self.bot.services.accounts, payload.user_id)
        if account_id is None:
            return
        selection = snapshot.selection_for(account_id)
        if selection is None or selection.emoji != str(payload.emoji):
            return
        member = await event.resolve_member()
        if member is None or member.bot:
            return
        actor = await resolve_actor(self.bot, member, account_id=account_id)
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
            await send_to(message.channel)(render_payload([text_node(describe_rejection(locale, rejection))]))

    @app_commands.command(name="poll")
    @app_commands.guild_only()
    async def poll(self, interaction: discord.Interaction[BotT]) -> None:
        """Create a multi-option poll through an ephemeral preview wizard."""
        locale = await resolve_locale(interaction, self.bot.services.settings)
        # A modal has to open on an unspent interaction, and showing the notice spends this one,
        # so an unconsented author is asked here and re-runs to get the editor.
        account = await self.bot.services.accounts.get_account_by_identity(
            IdentityProvider.DISCORD, str(interaction.user.id)
        )
        if account is None or account.id is None or account.needs_consent_refresh:
            if await ensure_consented_account(interaction, self.bot.services.accounts, locale=locale) is None:
                return
            invocation = await sd.Invocation.of(interaction)
            await invocation.reply(
                text_node(t(locale, _("Thanks. Run `/poll` again to open the editor."))),
                visibility="personal",
            )
            return
        allow_network = isinstance(interaction.user, discord.Member) and await self.publisher.may_create_network(
            interaction.user
        )
        await present_poll_form(interaction, self.publisher, account.id, allow_network=allow_network)

    async def delete_vote_context_menu(self, interaction: discord.Interaction[BotT], message: discord.Message) -> None:
        """Open a vote on deleting the message that was right-clicked.

        This was `/vote delete <message>`, which in slash form meant pasting a link to a
        message you were already looking at (audit C4).
        """
        await interaction.response.defer(ephemeral=True)
        invocation = await sd.Invocation.of(interaction)
        locale = await resolve_locale(interaction, self.bot.services.settings)
        if interaction.guild is None or message.guild != interaction.guild:
            await invocation.reply(
                error_node(
                    t(locale, _("Cannot vote on this message")),
                    t(locale, _("The message is not from this guild.")),
                ),
                visibility="personal",
            )
            return

        author_account_id = await ensure_consented_account(interaction, self.bot.services.accounts, locale=locale)
        if author_account_id is None:
            return

        # The card is a public artifact of a public decision, so it goes in the channel
        # rather than into the ephemeral reply the right-click opened.
        async def publish(_progress, result):
            published = result.message
            if published is None:
                detail = "a public vote operation requires the delivered Discord message"
                raise RuntimeError(detail)
            await start_delete_log_vote(
                self.bot,
                author_account_id=author_account_id,
                target_message=message,
                published_message=published,
            )
            # The reconciler has replaced the progress card with the authoritative vote card.
            # Keeping the operation's terminal scene equal to its initial scene suppresses a
            # mount edit that would otherwise overwrite that adopted message.
            return info_node(t(locale, _("Working")), t(locale, _("Getting information...")))

        await run_command_operation(
            invocation,
            publish,
            destination=sd.send_to(message.channel),
        )
        await invocation.reply(text_node(t(locale, _("Deletion vote opened."))), visibility="personal")

    async def _consented_account_id(self, discord_id: int) -> int | None:
        """Resolve a voter's account without creating one.

        A reaction is not evidence that anybody asked to be remembered, and casting a ballot
        stores a row naming them. There is no ephemeral surface on a raw reaction to ask in, so
        the answer here is only ever "already agreed" or "not yet".
        """
        account = await self.bot.services.accounts.get_account_by_identity(IdentityProvider.DISCORD, str(discord_id))
        if account is None or account.id is None or account.needs_consent_refresh:
            return None
        return account.id

    async def _decline_unconsented_vote(
        self,
        message: discord.Message,
        user: discord.Member | discord.User,
        emoji: discord.PartialEmoji | str,
    ) -> None:
        """Take the reaction back and say, once and briefly, how to make it count.

        A raw reaction has no ephemeral surface, so this is in-channel and self-deleting. It is
        deliberately not a DM: an unsolicited consent prompt arriving from a bot is worse than the
        gap it closes.
        """
        await self._remove_reaction(message, emoji, user)
        locale = await resolve_locale(message, self.bot.services.settings)
        with contextlib.suppress(discord.HTTPException):
            await send_to(message.channel, delete_after=30)(
                render_payload(
                    [
                        text_node(
                            t(
                                locale,
                                _("{user}, voting stores your Discord user ID. Run `/account consent` first."),
                                user=user.mention,
                            )
                        )
                    ]
                )
            )

    async def resolve(self, account_id: int, guild_id: int, kind: VoteKind) -> VoteActor | None:
        """Resolve current member facts for a service-level weight refresh.

        A ballot records an account, so the snowflake to look the member up by is read
        here. Somebody who is definitely not in the guild resolves to an actor holding
        nothing, which weighs the default; `None` is kept for the questions we could
        not ask, so the caller leaves the cached weight alone instead of rewriting it
        from an answer we never got.
        """
        guild = self.bot.get_guild(guild_id)
        account = await self.bot.services.accounts.get_account_by_id(account_id)
        identity = None if account is None else account.identity(IdentityProvider.DISCORD)
        if identity is None or identity.discord_id is None:
            # No Discord identity at all, so no roles anywhere.
            return VoteActor(account_id, discord_id=0, guild_id=guild_id)
        discord_id = identity.discord_id
        if guild is None:
            return None
        member = guild.get_member(discord_id)
        if member is None:
            try:
                member = await guild.fetch_member(discord_id)
            except discord.NotFound:
                return VoteActor(account_id, discord_id, guild_id)
            except discord.Forbidden:
                return None
        return await resolve_actor(self.bot, member, account_id=account_id)


async def setup(bot: squid.bot.app.RedstoneSquid):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(VoteCog(bot))
