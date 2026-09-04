"""Handles reaction-based voting for various purposes."""

import contextlib
import logging
from typing import TYPE_CHECKING, Self, cast, override

import discord
from discord import app_commands

import squid_ui_discord as sd
from squid.accounts.domain import IdentityProvider
from squid.bot._types import GuildMessageable
from squid.bot.consent import ensure_consented_account
from squid.bot.i18n import localization_for, resolve_locale
from squid.bot.reactions import ReactionClearEvent, ReactionEvent
from squid.bot.ui import error_node, info_node, render_payload, text_node
from squid.bot.voting.actors import describe_rejection, resolve_actor
from squid.bot.voting.poll_wizard import PollDraft, PollScreen
from squid.bot.voting.publisher import DiscordPollPublisher
from squid.bot.voting.sessions import start_delete_log_vote
from squid.core.i18n import tr
from squid.runtime import JobHandle
from squid.voting.domain import (
    PollScope,
    VoteActor,
    VoteKind,
    VoteMessage,
    VoteOption,
    VoteRejection,
    VoteSessionSnapshot,
)
from squid.voting.errors import InvalidVoteConfigurationError
from squid_ui.text import localization_scope
from squid_ui_discord import send_to

if TYPE_CHECKING:
    import squid.bot.app


logger = logging.getLogger(__name__)

_QUIET_REJECTIONS = frozenset({VoteRejection.NOT_FOUND, VoteRejection.CLOSED, VoteRejection.INVALID_OPTION})
"""Refusals a reaction should not be answered with.

Racing a close, or reacting with an emoji that is not an option, is ordinary and
would otherwise put a bot message in the channel for every stray reaction.
"""


class VoteCog[BotT: "squid.bot.app.RedstoneSquid"](sd.Cog[BotT]):
    def __init__(self, bot: BotT):
        super().__init__(bot)
        self.vote_service = bot.services.votes
        self.publisher = DiscordPollPublisher(bot)
        self._background_tasks: set[JobHandle] = set()
        self.vote_service.set_actor_resolver(self)
        self._reaction_subscription = self.bot.reactions.subscribe(
            type(self).__qualname__,
            add=self.on_reaction_add,
            remove=self.on_reaction_remove,
            clear=self.on_reaction_clear,
            clear_emoji=self.on_reaction_clear_emoji,
            recover_add=self.recover_reaction_action,
            recover_remove=self.recover_reaction_action,
            recover_clear=self.recover_reaction_clear,
            recover_clear_emoji=self.recover_reaction_clear,
        )

    @override
    async def ui_load(self) -> None:
        self._track(
            self.bot.background_tasks.start_periodic(
                self.reconcile_open_reactions,
                name="vote-reaction-reconciliation",
                interval=60,
            )
        )

    @override
    async def ui_unload(self) -> None:
        self._reaction_subscription.detach()
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

        # The reaction is the durable recovery source until the ballot commits. If shutdown
        # interrupts this handler first, the periodic reconciliation sees it on the next run.
        if result.session.should_remove_reaction_on_cast():
            self._track(
                self.bot.background_tasks.start(
                    self._remove_reaction(message, payload.emoji, user),
                    name=f"remove-vote-reaction-{payload.message_id}-{payload.user_id}",
                )
            )

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

    async def recover_reaction_action(self, event: ReactionEvent) -> None:
        """Reconcile one interrupted add/remove from Discord's durable message state."""
        await self.reconcile_message_reactions(event.payload.message_id)

    async def recover_reaction_clear(self, event: ReactionClearEvent) -> None:
        """Reconcile one interrupted clear from Discord's durable message state."""
        await self.reconcile_message_reactions(event.payload.message_id)

    async def reconcile_open_reactions(self) -> None:
        """Periodically converge every open vote from its Discord reactions.

        The job ends when the cog unloads. Public reactions are the desired ballot state;
        anonymous reactions remain only until their corresponding database mutation commits,
        so a reaction left behind is a retryable ballot without exposing stored selections.
        """
        await self.bot.wait_until_ready()
        errors: list[Exception] = []
        visited: set[int] = set()
        for kind in VoteKind:
            try:
                sessions = await self.vote_service.list_open(kind)
            except Exception as error:
                errors.append(error)
                continue
            for session in sessions:
                if session.id in visited:
                    continue
                visited.add(session.id)
                try:
                    await self._reconcile_session_reactions(session)
                except Exception as error:
                    errors.append(error)
        if errors:
            message = "Vote reaction reconciliation failed"
            raise ExceptionGroup(message, errors)

    async def reconcile_message_reactions(self, message_id: int) -> None:
        """Make one open session agree with the reactions currently on its card."""
        snapshot = await self.vote_service.get_session(message_id)
        if snapshot is None or not snapshot.is_open:
            return
        if message_id not in snapshot.message_ids:
            return
        await self._reconcile_session_reactions(snapshot)

    async def _reconcile_session_reactions(self, snapshot: VoteSessionSnapshot) -> None:
        """Converge all of one session's cards without multi-card vote oscillation."""
        cards: list[tuple[VoteMessage, discord.Message, dict[str, discord.Reaction]]] = []
        observed: dict[int, list[tuple[int, discord.Message, discord.Member, str]]] = {}
        complete_observation = True
        for location in snapshot.messages:
            message = await self.bot.get_or_fetch_message(location.channel_id, location.id)
            if message is None:
                complete_observation = False
                continue
            reactions = {str(reaction.emoji): reaction for reaction in message.reactions}
            cards.append((location, message, reactions))
            for option in snapshot.options_for_guild(location.guild_id):
                reaction = reactions.get(option.emoji)
                if reaction is None:
                    continue
                async for user in reaction.users(limit=None):
                    if user.bot or (self.bot.user is not None and user.id == self.bot.user.id):
                        continue
                    member = await self._reaction_member(location.guild_id, user)
                    if member is None:
                        complete_observation = False
                        continue
                    account_id = await self.bot.account_ids.resolve(self.bot.services.accounts, member.id)
                    if account_id is not None:
                        observed.setdefault(account_id, []).append((location.id, message, member, option.emoji))

        changed = False
        for account_id, reactions_for_account in observed.items():
            reacted_message_id, _message, member, desired_emoji = reactions_for_account[0]
            selection = snapshot.selection_for(account_id)
            accepted = selection is not None and selection.emoji == desired_emoji
            if not accepted:
                actor = await resolve_actor(self.bot, member, account_id=account_id)
                result = await self.vote_service.cast_vote(reacted_message_id, actor, desired_emoji)
                accepted = result.accepted
                changed = changed or accepted
            if accepted:
                retained = None if snapshot.is_anonymous else desired_emoji
                for _message_id, reacted_message, reacted_member, emoji in reactions_for_account:
                    if emoji != retained:
                        await self._remove_reaction(reacted_message, emoji, reacted_member)

        if not snapshot.is_anonymous and complete_observation:
            observed_accounts = observed.keys()
            for selection in snapshot.selections:
                if selection.account_id in observed_accounts:
                    continue
                location = next(
                    (message for message in snapshot.messages if message.guild_id == selection.guild_id),
                    snapshot.messages[0] if snapshot.messages else None,
                )
                if location is None:
                    continue
                actor = await self.resolve(selection.account_id, location.guild_id, snapshot.kind)
                if actor is None:
                    continue
                result = await self.vote_service.cast_vote(location.id, actor, selection.emoji)
                changed = changed or result.accepted

        for location, message, reactions in cards:
            for option in snapshot.options_for_guild(location.guild_id):
                reaction = reactions.get(option.emoji)
                if reaction is None or not reaction.me:
                    await message.add_reaction(option.emoji)
        if changed:
            await self.bot.refresh_posts("vote_session", str(snapshot.id))

    async def _reaction_member(
        self, guild_id: int, user: discord.Member | discord.User
    ) -> discord.Member | None:
        if isinstance(user, discord.Member):
            return user
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return None
        member = guild.get_member(user.id)
        if member is not None:
            return member
        try:
            return await guild.fetch_member(user.id)
        except (discord.NotFound, discord.Forbidden):
            return None

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
        with localization_scope(localization_for(locale)):
            payload = render_payload([text_node(describe_rejection(rejection))])
        with contextlib.suppress(discord.Forbidden, discord.NotFound):
            await send_to(message.channel)(payload)

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
            return sd.Response(text_node(tr("Thanks. Run `/poll` again to open the editor.")), audience="personal")
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
            message = await self.publisher.create_and_publish(
                author_account_id=account.id,
                channel=cast(GuildMessageable, channel),
                question=draft.question,
                visibility=draft.visibility,
                duration_seconds=draft.duration_seconds,
                options=options,
                scope=draft.scope,
            )
            return message.jump_url

        return PollScreen(resolve_options, publish, allow_network=allow_network)

    @sd.context_menu(name="Vote to Delete", defer="private")
    async def delete_vote_context_menu(self, request: sd.Request[Self], message: discord.Message) -> sd.CommandResult:
        """Open a vote on deleting the message that was right-clicked.

        This was `/vote delete <message>`, which in slash form meant pasting a link to a
        message you were already looking at (audit C4).
        """
        if request.guild is None or message.guild != request.guild:
            return error_node(tr("Cannot vote on this message"), tr("The message is not from this guild."))

        author_account_id = await ensure_consented_account(request, self.bot.services.accounts)
        if author_account_id is None:
            return None

        # The card is a public artifact of a public decision, so it goes in the channel rather
        # than into the ephemeral reply the right-click opened. The placeholder is adopted by the
        # reconciler, which replaces it with the vote card.
        placeholder = await self.ui.send(
            message.channel, info_node(tr("Working"), tr("Getting information...")), locale=request.locale
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
        return text_node(tr("Deletion vote opened."))

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
        with localization_scope(localization_for(locale)):
            payload = render_payload(
                [
                    text_node(
                        tr(
                            "{user}, voting stores your Discord user ID. Run `/account consent` first.",
                            user=user.mention,
                        )
                    )
                ]
            )
        with contextlib.suppress(discord.HTTPException):
            await send_to(message.channel, delete_after=30)(payload)

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
