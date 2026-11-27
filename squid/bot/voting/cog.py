"""Voting commands, and the gateway listeners that keep voter memberships current."""

import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Literal, Self, cast, override

import discord
from discord import app_commands
from whenever import Instant

import squid_ui.ext.discord as sd
from squid.bot._types import GuildMessageable
from squid.bot.accounts.consent import ensure_consented_account
from squid.bot.actors import has_manage_server
from squid.bot.events.spec import DomainEventSubscriptions
from squid.bot.events.spec import extension as domain_events_extension
from squid.bot.ui import error_node, info_node, text_node
from squid.bot.voting.actors import resolve_actor
from squid.bot.voting.controls import close_poll
from squid.bot.voting.event_handlers import DeleteVotedMessageHandler, PostSubmittedBuildHandler
from squid.bot.voting.members import VoterMemberSync
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
from squid.bot.voting.spec import VotingExports, extension
from squid.core.accounts.domain import IdentityProvider
from squid.core.actors import ActingContext
from squid.core.i18n import tr
from squid.core.permissions.domain.catalogue import VOTE_POLL_CLOSE_ANY, VOTE_POLL_NETWORK_CREATE, VOTE_POLL_RECOUNT
from squid.core.voting.domain import PollScope, VoteMenu, VoteStatus, VoteVisibility
from squid.core.voting.errors import InvalidVoteConfigurationError
from squid_i18n import md_tr
from squid_ui.ext.discord.i18n import to_locale_str

if TYPE_CHECKING:
    import squid.bot.app


class VoteCog[BotT: squid.bot.app.RedstoneSquid](sd.Cog[BotT]):
    """Expose voting commands and own voting's reaction and domain-event handlers."""

    def __init__(self, bot: BotT, domain_events: DomainEventSubscriptions) -> None:
        super().__init__(bot)
        self._domain_events = domain_events
        self._detach_domain_events: Callable[[], Awaitable[None]] | None = None
        self.vote_service = bot.services.votes
        self.publisher = DiscordPollPublisher(bot)
        self.members = VoterMemberSync(bot)
        self.reactions = VoteReactionHandler(bot, self.members)

    @override
    async def cog_load(self) -> None:
        self.reactions.start()
        self._detach_domain_events = self._domain_events.subscribe(
            {
                "build.submitted": PostSubmittedBuildHandler(self.bot).handle,
                "vote_session.closed": DeleteVotedMessageHandler(self.bot).handle,
            }
        )

    @override
    async def cog_unload(self) -> None:
        if self._detach_domain_events is not None:
            await self._detach_domain_events()
            self._detach_domain_events = None
        await self.reactions.close()

    @sd.Cog.listener()
    async def on_guild_available(self, guild: discord.Guild) -> None:
        await self.members.reconcile(guild)

    @sd.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        await self.members.record((member,))

    @sd.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if before.roles != after.roles or has_manage_server(before) != has_manage_server(after):
            await self.members.record((after,))

    @sd.Cog.listener()
    async def on_raw_member_remove(self, payload: discord.RawMemberRemoveEvent) -> None:
        await self.members.forget(payload.guild_id, payload.user.id)

    @sd.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User | discord.Member) -> None:
        """Void the banned member's ballots in the sessions this guild weighs."""
        account_id = await self.bot.account_ids.resolve(self.bot.services.accounts, user.id)
        if account_id is None:
            return
        for session in await self.vote_service.void_ballots(account_id, guild.id, context=ActingContext.current()):
            await self.bot.refresh_posts("vote_session", str(session.id))

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
        # ORM identity columns may still be None before flush.
        if (
            account is None
            or account.id is None  # pyright: ignore[reportUnnecessaryComparison]
            or account.needs_consent_refresh
        ):
            if await ensure_consented_account(request, self.bot.services.accounts) is None:
                return None
            return await request.respond(
                text_node(
                    md_tr(
                        t"vote-submitted-open-poll-editor = Thanks. Run `/poll` again to open the editor.",
                    )
                ),
                audience="personal",
            )
        allow_network = isinstance(actor, discord.Member) and await self.publisher.may_create_network(actor)
        guild = request.guild
        channel = request.channel

        if guild is None or channel is None:
            return None

        async def resolve_options(lines: tuple[str, ...]) -> VoteMenu:
            return await self.publisher.resolve_options(guild.id, lines)

        async def publish(draft: PollDraft, menu: VoteMenu) -> str:
            subject = await self.bot.actors.resolve(request, refresh=True)
            if subject.account_id != account.id:
                raise InvalidVoteConfigurationError(
                    tr(t"poll-account-changed = Your account changed. Open the poll again.")
                )
            if draft.scope is PollScope.NETWORK:
                if not (await self.bot.services.permissions.check(subject, VOTE_POLL_NETWORK_CREATE)).allowed:
                    raise InvalidVoteConfigurationError(
                        tr(
                            t"bot-voting-vote-you-may-no-longer-publish-a-poll = You may no longer publish a poll "
                            t"to every server."
                        )
                    )
                subject = replace(subject, capabilities=subject.capabilities | {VOTE_POLL_NETWORK_CREATE.name})
            publication = await self.publisher.create_and_publish(
                context=ActingContext.for_actor(subject),
                channel=cast(GuildMessageable, channel),
                question=draft.question,
                visibility=draft.visibility,
                duration_seconds=draft.duration_seconds,
                menu=menu,
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
                menu = await resolve_options(draft.option_lines)
                if not preview:
                    published_url = await publish(draft, menu)
                    return sd.Response(
                        text_node(md_tr(t"bot-voting-poll-wizard-poll-published = Poll published: {published_url}")),
                        audience="personal",
                    )
        except InvalidVoteConfigurationError as error:
            return sd.Response(text_node(error.message), audience="personal")
        return PollScreen(resolve_options, publish, allow_network=allow_network, draft=draft)

    @sd.command(
        name=tr(t"command-poll-review-name = poll-review"),
        description=tr(t"command-poll-review-help = Inspect a paused poll or confirm a review decision."),
        defer="private",
        options={
            "poll_id": sd.Option(
                name=tr(t"poll-review-poll-id-name = poll_id"),
                description=tr(t"poll-review-poll-id-help = Poll to review. Omit to list paused polls."),
            ),
            "action": sd.Option(
                name=tr(t"poll-review-action-name = action"),
                description=tr(t"poll-review-action-help = Review decision to inspect or confirm."),
                choices=[
                    sd.Choice("inspect", tr(t"poll-review-action-inspect = Inspect")),
                    sd.Choice("retry", tr(t"poll-review-action-retry = Retry")),
                    sd.Choice("finalize", tr(t"poll-review-action-finalize = Finalize")),
                    sd.Choice("reopen", tr(t"poll-review-action-reopen = Reopen")),
                    sd.Choice("void", tr(t"poll-review-action-void = Void")),
                ],
            ),
            "revision": sd.Option(
                name=tr(t"poll-review-revision-name = revision"),
                description=tr(t"poll-review-revision-help = Count revision shown by the inspection."),
            ),
            "reason": sd.Option(
                name=tr(t"poll-review-reason-name = reason"),
                description=tr(t"poll-review-reason-help = Reason for finalizing, reopening, or voiding the poll."),
            ),
            "duration": sd.Option(
                name=tr(t"poll-review-duration-name = duration"),
                description=tr(t"poll-review-duration-help = Voting time to add when reopening the poll."),
            ),
        },
    )
    @app_commands.guild_only()
    async def poll_review(
        self,
        request: sd.Request[Self, BotT],
        poll_id: int | None = None,
        action: Literal["inspect", "retry", "finalize", "reopen", "void"] = "inspect",
        revision: int | None = None,
        reason: str = "",
        duration: str = "",
    ) -> sd.CommandResult:
        if not isinstance(request.user, discord.Member):
            return text_node("Use this command in the poll's server.")
        account = await self.bot.account_ids.resolve(self.bot.services.accounts, request.user.id)
        actor = await resolve_actor(self.bot, request.user, account_id=account or 0)
        if poll_id is None:
            if (
                not account
                or actor.guild_id is None
                or not actor.capabilities.intersection({VOTE_POLL_CLOSE_ANY.name, VOTE_POLL_RECOUNT.name})
            ):
                return text_node("Poll review permission in this server is required.")
            pending = await self.vote_service.pending_reviews(actor.guild_id, context=ActingContext.current())
            ids = ", ".join(map(str, pending)) or "none"
            return text_node(f"Polls awaiting review: {ids}")
        snapshot = await self.vote_service.get_session(poll_id, context=ActingContext.current())
        if (
            not account
            or snapshot is None
            or snapshot.poll is None
            or not snapshot.can_review_poll(actor, recount_only=action in ("inspect", "retry"))
        ):
            return text_node("Poll management permission in the owning server is required.")
        poll = snapshot.poll
        if snapshot.status is not VoteStatus.REVIEW:
            return text_node("This poll is not awaiting review.")
        if action == "inspect" or revision != poll.count_revision:
            missing = ", ".join(str(card) for card in poll.unreadable_cards) or "none"
            readable = (
                ", ".join(
                    f"https://discord.com/channels/{card.guild_id}/{card.channel_id}/{card.id}"
                    for card in snapshot.messages
                    if card.id not in poll.unreadable_cards
                )
                or "none"
            )
            tallies = (
                ", ".join(f"{option}: {score:g}" for option, score in snapshot.weighted_tallies().items())
                or "no ballots"
            )
            return text_node(
                f"Poll {poll_id} · {poll.question}\nCollected weighted totals: {tallies}\n"
                f"Readable cards: {readable}\nUnreadable card IDs: {missing}. Changes visible only on those cards may be absent.\n"
                f"Confirm with revision {poll.count_revision}. Retry reads current reactions. Finalize seals "
                "these ballots. Reopen announces a new deadline (supply duration). Void discards the result. "
                "Supply a reason for finalize, reopen or void."
            )
        try:
            deadline = Instant.now().add(seconds=parse_poll_duration(duration)) if action == "reopen" else None
            result = await self.vote_service.review_poll(
                snapshot,
                action,
                reason=reason,
                deadline=deadline,
                recount=self.reactions.count_cards,
                context=ActingContext.for_actor(actor),
            )
        except InvalidVoteConfigurationError as error:
            return text_node(error.message)
        if result.rejection or result.session is None:
            return text_node("The poll changed. Inspect it again before confirming.")
        await self.bot.refresh_posts("vote_session", str(poll_id))
        return text_node(f"Poll {poll_id}: {result.session.status.value}.")

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
                context=ActingContext.current().with_actor(
                    replace(ActingContext.current().effective.require_user(), account_id=author_account_id)
                ),
                target_message=message,
                published_message=published,
            )
        except BaseException:
            with contextlib.suppress(discord.HTTPException):
                await published.delete()
            raise
        return await request.respond(text_node(tr(t"bot-voting-vote-deletion-vote-opened = Deletion vote opened.")))


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    ctx = bot.extension_manager.setup_context(extension)
    cog = VoteCog(bot, ctx.require(domain_events_extension))
    await bot.add_cog(cog)
    ctx.publish(VotingExports(recount=cog.reactions))
    bot.ui_router.mount(__name__, close_poll)


async def teardown(bot: squid.bot.app.RedstoneSquid) -> None:
    bot.ui_router.unmount(__name__)
