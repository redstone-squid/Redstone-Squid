"""Voting commands and current actor resolution for persisted ballot weights."""

import contextlib
from typing import TYPE_CHECKING, Self, cast, override

import discord
from discord import app_commands

import squid_ui_discord as sd
from squid.accounts.domain import IdentityProvider
from squid.bot._types import GuildMessageable
from squid.bot.consent import ensure_consented_account
from squid.bot.ui import error_node, info_node, text_node
from squid.bot.voting.actors import resolve_actor
from squid.bot.voting.poll_wizard import PollDraft, PollScreen
from squid.bot.voting.publisher import DiscordPollPublisher
from squid.bot.voting.reactions import VoteReactionHandler
from squid.bot.voting.sessions import start_delete_log_vote
from squid.core.i18n import tr
from squid.voting.domain import PollScope, VoteActor, VoteKind, VoteOption
from squid.voting.errors import InvalidVoteConfigurationError

if TYPE_CHECKING:
    import squid.bot.app


class VoteCog[BotT: "squid.bot.app.RedstoneSquid"](sd.Cog[BotT]):
    """Expose voting commands and own the reaction handler."""

    def __init__(self, bot: BotT):
        super().__init__(bot)
        self.vote_service = bot.services.votes
        self.publisher = DiscordPollPublisher(bot)
        self.reactions = VoteReactionHandler(bot, consumer=type(self).__qualname__)
        self.vote_service.set_actor_resolver(self)

    @override
    async def ui_load(self) -> None:
        self.reactions.start()

    @override
    async def ui_unload(self) -> None:
        await self.reactions.close()

    @sd.command(name="poll")
    @app_commands.guild_only()
    async def poll(self, request: sd.Request[Self]) -> sd.CommandResult:
        """Create a multi-option poll through an ephemeral preview wizard."""
        actor = request.user
        # A modal has to open on an unspent interaction, and showing the notice spends this one,
        # so an unconsented author is asked here and re-runs to get the editor.
        account = await self.bot.services.accounts.get_account_by_identity(IdentityProvider.DISCORD, str(actor.id))
        if account is None or account.id is None or account.needs_consent_refresh:
            if await ensure_consented_account(request, self.bot.services.accounts) is None:
                return None
            return sd.Response(text_node(tr(t"Thanks. Run `/poll` again to open the editor.")), audience="personal")
        # Bound out here because `publish` runs later: a narrowing of `account.id` does not survive
        # into a closure, and the identity the poll is attributed to is fixed at command time.
        author_account_id = account.id
        allow_network = isinstance(actor, discord.Member) and await self.publisher.may_create_network(actor)
        guild = request.guild
        channel = request.channel

        if guild is None or channel is None:
            return None

        async def resolve_options(lines: tuple[str, ...]) -> tuple[VoteOption, ...]:
            return await self.publisher.resolve_options(guild.id, lines)

        async def publish(draft: PollDraft, options: tuple[VoteOption, ...]) -> str:
            if draft.scope is PollScope.NETWORK and not (
                isinstance(actor, discord.Member) and await self.publisher.may_create_network(actor)
            ):
                raise InvalidVoteConfigurationError(tr(t"You may no longer publish a poll to every server."))
            publication = await self.publisher.create_and_publish(
                author_account_id=author_account_id,
                channel=cast(GuildMessageable, channel),
                question=draft.question,
                visibility=draft.visibility,
                duration_seconds=draft.duration_seconds,
                options=options,
                scope=draft.scope,
            )
            return publication.message.jump_url

        return PollScreen(resolve_options, publish, allow_network=allow_network)

    @sd.context_menu(name="Vote to Delete", defer="private")
    async def delete_vote_context_menu(self, request: sd.Request[Self], message: discord.Message) -> sd.CommandResult:
        """Open a vote on deleting the message that was right-clicked.

        This was `/vote delete <message>`, which in slash form meant pasting a link to a
        message you were already looking at (audit C4).
        """
        if request.guild is None or message.guild != request.guild:
            return error_node(tr(t"Cannot vote on this message"), tr(t"The message is not from this guild."))

        author_account_id = await ensure_consented_account(request, self.bot.services.accounts)

        if author_account_id is None:
            return None

        # The card is a public artifact of a public decision, so it goes in the channel rather
        # than into the ephemeral reply the right-click opened. The placeholder is adopted by the
        # reconciler, which replaces it with the vote card.
        placeholder = await self.ui.send(
            message.channel, info_node(tr(t"Working"), tr(t"Getting information...")), locale=request.locale
        )
        published = placeholder.delivery.message if isinstance(placeholder, sd.Sent) else None
        if published is None:
            detail = "a public vote needs the delivered Discord message"
            raise RuntimeError(detail)
        try:
            await start_delete_log_vote(
                self.bot,
                author_account_id=author_account_id,
                target_message=message,
                published_message=published,
            )
        except BaseException:
            with contextlib.suppress(discord.HTTPException):
                await published.delete()
            raise
        return text_node(tr(t"Deletion vote opened."))

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
        identity = None if account is None else account.most_recent_identity_for(IdentityProvider.DISCORD)
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
