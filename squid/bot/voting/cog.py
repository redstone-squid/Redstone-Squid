"""Voting commands and current actor resolution for persisted ballot weights."""

import contextlib
from typing import TYPE_CHECKING, Self, cast, override

import discord
from discord import app_commands

import squid_ui.ext.discord as sd
from squid.accounts.domain import IdentityProvider
from squid.bot._types import GuildMessageable
from squid.bot.accounts.consent import ensure_consented_account
from squid.bot.ui import error_node, info_node, text_node
from squid.bot.voting.actors import resolve_actor
from squid.bot.voting.poll_wizard import (
    DEFAULT_POLL_CHOICES,
    SCOPE_CHOICES,
    VISIBILITY_CHOICES,
    PollDraft,
    PollScreen,
    parse_poll_duration,
)
from squid.bot.voting.publisher import DiscordPollPublisher
from squid.bot.voting.reactions import VoteReactionHandler
from squid.bot.voting.sessions import start_delete_log_vote
from squid.core.actors import Actor
from squid.core.i18n import tr
from squid.voting.domain import PollScope, VoteKind, VoteOption, VoteVisibility
from squid.voting.errors import InvalidVoteConfigurationError
from squid_i18n import md_tr
from squid_ui.ext.discord.i18n import to_locale_str

if TYPE_CHECKING:
    import squid.bot.app


class VoteCog[BotT: squid.bot.app.RedstoneSquid](sd.Cog[BotT]):
    """Expose voting commands and own the reaction handler."""

    def __init__(self, bot: BotT):
        super().__init__(bot)
        self.vote_service = bot.services.votes
        self.publisher = DiscordPollPublisher(bot)
        self.reactions = VoteReactionHandler(bot, self)
        self.vote_service.set_actor_resolver(self)

    @override
    async def cog_load(self) -> None:
        self.reactions.start()

    @override
    async def cog_unload(self) -> None:
        await self.reactions.close()

    @sd.command(
        name=to_locale_str(tr(t"command-poll-name = poll")),
        description=to_locale_str(tr(t"command-poll-help = Create a poll from options or open the poll editor.")),
        defer="private",
        options={
            "question": sd.Option(
                name=tr(t"poll-command-question-name = question"),
                description=tr(t"poll-command-question = Poll question; omit to open the editor."),
            ),
            "choices": sd.Option(
                name=tr(t"poll-command-choices-name = choices"),
                description=tr(t"poll-command-choices = 2-10 choices, separated by commas or \\n; default: Yes, No."),
            ),
            "duration": sd.Option(
                name=tr(t"poll-command-duration-name = duration"),
                description=tr(t"poll-command-duration = How long to vote: 2h15m, 2 days, etc. Default: 24h."),
            ),
            "visibility": sd.Option(
                name=tr(t"poll-command-visibility-name = visibility"),
                description=tr(t"poll-command-visibility = Who can see votes and when results are shown."),
                choices=[sd.Choice(value.value, label) for value, label, _ in VISIBILITY_CHOICES],
            ),
            "scope": sd.Option(
                name=tr(t"poll-command-scope-name = scope"),
                description=tr(t"poll-command-scope = This server, or every server if you have permission."),
                choices=[sd.Choice(value.value, label) for value, label, _ in SCOPE_CHOICES],
            ),
            "preview": sd.Option(
                name=tr(t"poll-command-preview-name = preview"),
                description=tr(
                    t"poll-command-preview = Review and edit before publishing. Default: publish immediately."
                ),
            ),
        },
    )
    @app_commands.guild_only()
    async def poll(
        self,
        request: sd.Request[Self, BotT],
        question: str | None = None,
        choices: str | None = None,
        duration: str = "24h",
        visibility: str = VoteVisibility.ANONYMOUS_LIVE,
        scope: str = PollScope.GUILD,
        preview: bool = False,
    ) -> sd.CommandResult:
        """Publish supplied poll content, or open an editor with the supplied defaults."""
        actor = request.user
        account = await self.bot.services.accounts.get_account_by_identity(IdentityProvider.DISCORD, str(actor.id))
        if account is None or account.id is None or account.needs_consent_refresh:
            if await ensure_consented_account(request, self.bot.services.accounts) is None:
                return None
            return sd.Response(
                text_node(
                    md_tr(
                        t"vote-submitted-open-poll-editor = Thanks. Run `/poll` again to open the editor.",
                    )
                ),
                audience="personal",
            )
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
                raise InvalidVoteConfigurationError(
                    tr(
                        t"bot-voting-vote-you-may-no-longer-publish-a-poll = You may no longer publish a poll "
                        t"to every server."
                    )
                )
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

        try:
            draft = PollDraft(
                question=question or "",
                options_text=DEFAULT_POLL_CHOICES if choices is None else choices,
                visibility=VoteVisibility(visibility),
                duration_seconds=parse_poll_duration(duration),
                scope=PollScope(scope),
            )
            if draft.scope is PollScope.NETWORK and not allow_network:
                return sd.Response(
                    text_node(
                        tr(t"poll-command-network-denied = You do not have permission to publish to every server.")
                    ),
                    audience="personal",
                )
            if question is not None:
                draft.validate()
                options = await resolve_options(draft.option_lines)
                if not preview:
                    published_url = await publish(draft, options)
                    return sd.Response(
                        text_node(md_tr(t"bot-voting-poll-wizard-poll-published = Poll published: {published_url}")),
                        audience="personal",
                    )
        except InvalidVoteConfigurationError as error:
            return sd.Response(text_node(error.message), audience="personal")
        return PollScreen(resolve_options, publish, allow_network=allow_network, draft=draft)

    @sd.context_menu(name=to_locale_str(tr(t"command-vote-to-delete-name = Vote to Delete")), defer="private")
    async def delete_vote_context_menu(
        self, request: sd.Request[Self, BotT], message: discord.Message
    ) -> sd.CommandResult:
        """Open a vote on deleting the message that was right-clicked."""
        if request.guild is None or message.guild != request.guild:
            return error_node(
                tr(t"bot-voting-vote-cannot-vote-on-this-message = Cannot vote on this message"),
                tr(t"vote-message-guild-mismatch = The message is not from this guild."),
            )

        author_account_id = await ensure_consented_account(request, self.bot.services.accounts)

        if author_account_id is None:
            return None

        # The card is a public artifact of a public decision, so it goes in the channel rather
        # than into the ephemeral reply the right-click opened. The placeholder is adopted by the
        # reconciler, which replaces it with the vote card.
        placeholder = await self.ui.send(
            message.channel,
            info_node(
                tr(t"bot-voting-vote-working = Working"),
                tr(t"bot-voting-vote-getting-information = Getting information..."),
            ),
            locale=request.locale,
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
        return text_node(tr(t"bot-voting-vote-deletion-vote-opened = Deletion vote opened."))

    async def resolve(self, account_id: int, guild_id: int, kind: VoteKind) -> Actor | None:
        """Resolve member roles and capabilities for a persisted voter's weight refresh.

        No linked Discord identity or a confirmed missing member produces an
        actor with no roles. An unavailable guild or denied member lookup returns
        `None`, which tells the service to retain the cached ballot weight.
        """
        guild = self.bot.get_guild(guild_id)
        account = await self.bot.services.accounts.get_account_by_id(account_id)
        identity = None if account is None else account.most_recent_identity_for(IdentityProvider.DISCORD)
        if identity is None or identity.discord_id is None:
            # No Discord identity at all, so no roles anywhere.
            return Actor(account_id=account_id, discord_user_id=None, guild_id=guild_id)
        discord_id = identity.discord_id
        if guild is None:
            return None
        member = guild.get_member(discord_id)
        if member is None:
            try:
                member = await guild.fetch_member(discord_id)
            except discord.NotFound:
                return Actor(account_id=account_id, discord_user_id=discord_id, guild_id=guild_id)
            except discord.Forbidden:
                return None
        return await resolve_actor(self.bot, member, account_id=account_id)


async def setup(bot: squid.bot.app.RedstoneSquid):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(VoteCog(bot))
