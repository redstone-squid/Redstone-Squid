"""Converge live and recovered Discord reactions into persisted vote selections."""

import asyncio
import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord

from squid.accounts.domain import IdentityProvider
from squid.bot.i18n import localization_for, resolve_locale
from squid.bot.reactions import ReactionClearEvent, ReactionEvent
from squid.bot.ui import render_payload, text_node
from squid.bot.voting.actors import describe_rejection, resolve_actor
from squid.core.i18n import tr
from squid.runtime import JobHandle
from squid.voting.domain import CastVoteResult, VoteActor, VoteKind, VoteMessage, VoteRejection, VoteSessionSnapshot
from squid_ui.text import Message, localization_scope, raw_md
from squid_ui_discord import send_to

if TYPE_CHECKING:
    import squid.bot.app

_QUIET_REJECTIONS = frozenset({VoteRejection.NOT_FOUND, VoteRejection.CLOSED, VoteRejection.INVALID_OPTION})
"""Refusals a reaction should not be answered with.

Racing a close, or reacting with an emoji that is not an option, is ordinary and
would otherwise put a bot message in the channel for every stray reaction.
"""


@dataclass(frozen=True, slots=True)
class _ObservedReaction:
    message_id: int
    message: discord.Message
    member: discord.Member
    emoji: str
    option_id: str


class VoteReactionHandler:
    """Converge vote reactions until close detaches routing and cancels owned work."""

    def __init__(self, bot: squid.bot.app.RedstoneSquid, *, consumer: str) -> None:
        self.bot = bot
        self.vote_service = bot.services.votes
        self._background_tasks: set[JobHandle] = set()
        self._lock = asyncio.Lock()
        self._subscription = bot.reactions.subscribe(
            consumer,
            add=self.on_reaction_add,
            remove=self.on_reaction_remove,
            clear=self.on_reaction_clear,
            clear_emoji=self.on_reaction_clear,
            recover_add=self.recover_reaction_add,
            recover_remove=self.recover_reaction_remove,
            recover_clear=self.on_reaction_clear,
            recover_clear_emoji=self.on_reaction_clear,
        )

    def start(self) -> None:
        """Start periodic reconciliation after the cog has registered."""
        self._track(
            self.bot.background_tasks.start_periodic(
                self.reconcile_open_reactions,
                name="vote-reaction-reconciliation",
                interval=60,
            )
        )

    async def close(self) -> None:
        """Detach routing and settle reconciliation and anonymous-reaction cleanup."""
        self._subscription.detach()
        await self.bot.background_tasks.cancel(*self._background_tasks)

    def _track(self, handle: JobHandle) -> None:
        """Hold a handle for cancellation on unload, dropping the settled ones.

        A JobHandle has no completion callback, so the set is swept on insert
        rather than pruned as each job finishes.
        """
        self._background_tasks = {tracked for tracked in self._background_tasks if not tracked.finished.is_set()}
        self._background_tasks.add(handle)

    async def on_reaction_add(self, event: ReactionEvent) -> None:
        """Record a live ballot and explain any actionable refusal."""
        async with self._lock:
            await self._add(event, recovering=False)

    async def recover_reaction_add(self, event: ReactionEvent) -> None:
        """Recover an interrupted ballot quietly and await its reaction cleanup."""
        async with self._lock:
            await self._add(event, recovering=True)

    async def _add(self, event: ReactionEvent, *, recovering: bool) -> None:
        payload = event.payload
        if payload.guild_id is None or (self.bot.user is not None and payload.user_id == self.bot.user.id):
            return
        snapshot = await self.vote_service.get_session(payload.message_id)
        if snapshot is None or not snapshot.is_open:
            return
        emoji = str(payload.emoji)
        option = snapshot.option_by_emoji(emoji, payload.guild_id)
        if option is None:
            return
        message = await event.message()
        member = await event.resolve_member()
        if message is None or member is None or member.bot:
            return
        account_id = await self._consented_account_id(member.id)
        if account_id is None:
            if not recovering:
                await self._decline_unconsented_vote(message, member, payload.emoji)
            return
        actor = await resolve_actor(self.bot, member, account_id=account_id)
        previous = snapshot.selection_for(actor.account_id)
        if not recovering and not snapshot.is_anonymous and previous is not None and previous.option_id != option.id:
            await self._remove_reaction(message, previous.emoji, member)
        result, changed = await self._set_reaction_selection(snapshot, payload.message_id, actor, emoji, selected=True)
        if result.rejection is not None:
            if snapshot.should_remove_reaction_on_cast():
                await self._remove_reaction(message, payload.emoji, member)
            if not recovering:
                await self._report_rejection(message, result.rejection)
            return
        session = snapshot if recovering else result.session
        if session is None:
            return
        remove_reaction = session.should_remove_reaction_on_cast()
        # Live cleanup runs under the handler's owner only after commit. Recovery waits for
        # cleanup itself, leaving the reaction as a retry source if recovery is interrupted.
        if remove_reaction and not recovering:
            self._track(
                self.bot.background_tasks.start(
                    self._remove_reaction(message, payload.emoji, member),
                    name=f"remove-vote-reaction-{payload.message_id}-{payload.user_id}",
                )
            )
        if changed:
            await self.bot.refresh_posts("vote_session", str(snapshot.id))
        if remove_reaction and recovering:
            await self._remove_reaction(message, payload.emoji, member)

    async def on_reaction_remove(self, event: ReactionEvent) -> None:
        """Synchronize removal of a publicly retained reaction."""
        await self._remove(event, recovering=False)

    async def recover_reaction_remove(self, event: ReactionEvent) -> None:
        """Idempotently recover a public reaction removal from a guild."""
        await self._remove(event, recovering=True)

    async def _remove(self, event: ReactionEvent, *, recovering: bool) -> None:
        async with self._lock:
            payload = event.payload
            snapshot = await self.vote_service.get_session(payload.message_id)
            if snapshot is None or not snapshot.is_open or snapshot.is_anonymous:
                return
            if recovering and payload.guild_id is None:
                return
            # Removing a reaction needs no fresh consent and must never create an account.
            account_id = await self.bot.account_ids.resolve(self.bot.services.accounts, payload.user_id)
            if account_id is None:
                return
            member = await event.resolve_member()
            if member is None or member.bot:
                return
            actor = await resolve_actor(self.bot, member, account_id=account_id)
            result, changed = await self._set_reaction_selection(
                snapshot, payload.message_id, actor, str(payload.emoji), selected=False
            )
            session = snapshot if recovering else result.session
            if result.accepted and changed and session is not None:
                await self.bot.refresh_posts("vote_session", str(session.id))

    async def on_reaction_clear(self, event: ReactionClearEvent) -> None:
        """Restore options after any clear without inferring that stored ballots vanished."""
        await self._restore_reactions(event.payload.message_id)

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
                    async with self._lock:
                        location = session.messages[0] if session.messages else None
                        current = None if location is None else await self.vote_service.get_session(location.id)
                        if current is not None and current.is_open:
                            await self._reconcile_session_reactions(current)
                except Exception as error:
                    errors.append(error)
        if errors:
            message = "Vote reaction reconciliation failed"
            raise ExceptionGroup(message, errors)

    async def reconcile_message_reactions(self, message_id: int) -> None:
        """Make one open session agree with the reactions currently on its card."""
        async with self._lock:
            snapshot = await self.vote_service.get_session(message_id)
            if snapshot is None or not snapshot.is_open:
                return
            if message_id not in snapshot.message_ids:
                return
            await self._reconcile_session_reactions(snapshot)

    async def _set_reaction_selection(
        self,
        snapshot: VoteSessionSnapshot,
        message_id: int,
        actor: VoteActor,
        emoji: str,
        *,
        selected: bool,
    ) -> tuple[CastVoteResult, bool]:
        """Apply one reaction's desired state without replaying repository toggle semantics."""
        message = next((item for item in snapshot.messages if item.id == message_id), None)
        guild_id = actor.guild_id if message is None else message.guild_id
        option = snapshot.option_by_emoji(emoji, guild_id)
        if option is None:
            return CastVoteResult(snapshot, VoteRejection.INVALID_OPTION), False
        current = snapshot.selection_for(actor.account_id)
        already_selected = current is not None and current.option_id == option.id
        if selected is already_selected:
            return CastVoteResult(snapshot), False
        return await self.vote_service.cast_vote(message_id, actor, emoji), True

    async def _reconcile_session_reactions(self, snapshot: VoteSessionSnapshot) -> None:
        """Converge all of one session's cards without multi-card vote oscillation."""
        cards: list[tuple[VoteMessage, discord.Message, dict[str, discord.Reaction]]] = []
        observed: dict[int, list[_ObservedReaction]] = {}
        for location in snapshot.messages:
            message = await self.bot.get_or_fetch_message(location.channel_id, location.id)
            if message is None:
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
                        continue
                    account_id = await self._consented_account_id(member.id)
                    if account_id is None:
                        await self._remove_reaction(message, option.emoji, member)
                        continue
                    observed.setdefault(account_id, []).append(
                        _ObservedReaction(location.id, message, member, option.emoji, option.id)
                    )

        changed = False
        for account_id, reactions_for_account in observed.items():
            selection = snapshot.selection_for(account_id)
            desired = next(
                (
                    item
                    for item in reactions_for_account
                    if selection is not None and item.option_id == selection.option_id
                ),
                reactions_for_account[0],
            )
            if selection is not None and selection.option_id == desired.option_id:
                result, mutation = CastVoteResult(snapshot), False
            else:
                actor = await resolve_actor(self.bot, desired.member, account_id=account_id)
                result, mutation = await self._set_reaction_selection(
                    snapshot,
                    desired.message_id,
                    actor,
                    desired.emoji,
                    selected=True,
                )
            accepted = result.accepted
            changed = changed or (accepted and mutation)
            keep_option = desired.option_id if accepted else (None if selection is None else selection.option_id)
            for reaction in reactions_for_account:
                if snapshot.is_anonymous or reaction.option_id != keep_option:
                    await self._remove_reaction(reaction.message, reaction.emoji, reaction.member)

        for location, message, reactions in cards:
            for option in snapshot.options_for_guild(location.guild_id):
                reaction = reactions.get(option.emoji)
                if reaction is None or not reaction.me:
                    await message.add_reaction(option.emoji)
        if changed:
            await self.bot.refresh_posts("vote_session", str(snapshot.id))

    async def _reaction_member(self, guild_id: int, user: discord.Member | discord.User) -> discord.Member | None:
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
        except discord.NotFound, discord.Forbidden:
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
            payload = render_payload([text_node(_unconsented_vote_message(user.mention))])
        with contextlib.suppress(discord.HTTPException):
            await send_to(message.channel, delete_after=30)(payload)


def _unconsented_vote_message(user: object) -> Message:
    user = raw_md(user)
    return tr(t"{user}, voting stores your Discord user ID. Run `/account consent` first.")
