"""Translate Discord reactions into stored ballots for voting sessions.

A voting session may have cards in several channels, but an account has one
selection across them. Live reaction events update that selection. Reactions
added while the bot was offline are picked up by a recount (`BallotRecounter`),
which runs on request and before a poll closes, never on a timer. Anonymous votes
are stored before the user's reaction is removed, so a leftover reaction can be
retried. Missing reactions alone never prove a stored ballot was withdrawn.
"""

import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, ClassVar

import discord

from squid.bot.i18n import localization_for, resolve_locale
from squid.bot.reactions import (
    ReactionClearEvent,
    ReactionEvent,
    ReactionFilter,
    ReactionSubscription,
    reaction_handler,
)
from squid.bot.ui import render_payload, text_node
from squid.bot.voting.actors import describe_rejection, resolve_actor
from squid.core.actors import Actor
from squid.core.locks import KeyedLock
from squid.runtime import JobHandle
from squid.voting.application.accounts import AccountConsentedVoterLookup
from squid.voting.application.ports import VoteActorResolver
from squid.voting.application.recount import BallotRecounter
from squid.voting.domain import CastVoteResult, VoteRejection, VoteSessionSnapshot
from squid_i18n import md_tr
from squid_ui.text import Message, discord_md, localization_scope
from squid_ui_discord import send_to

if TYPE_CHECKING:
    import squid.bot.app

_QUIET_REJECTIONS = frozenset({VoteRejection.NOT_FOUND, VoteRejection.CLOSED, VoteRejection.INVALID_OPTION})
"""Refusals a reaction should not be answered with.

Racing a close, or reacting with an emoji that is not an option, is ordinary and
would otherwise put a bot message in the channel for every stray reaction.
"""


class VoteReactionHandler:
    """Handle vote reactions and recounts for one loaded voting cog.

    start subscribes this handler to reactions. close detaches it and cancels
    pending reaction cleanup; closing a voting session is a separate operation
    performed by the voting service.

    Live reactions and recounts for the same voting session take turns, because
    each reads the stored ballots before changing them. Different sessions do
    not wait for each other, so a slow recount holds up only its own session.
    """

    reaction_filter: ClassVar[ReactionFilter] = ReactionFilter(guild_only=True)

    def __init__(self, bot: squid.bot.app.RedstoneSquid, actors: VoteActorResolver) -> None:
        self.bot = bot
        self.vote_service = bot.services.votes
        self._voters = AccountConsentedVoterLookup(bot.services.accounts)
        self._recounter = BallotRecounter(self.vote_service, bot.services.vote_cards(bot), self._voters, actors)
        self._background_tasks: set[JobHandle] = set()
        self._session_locks: KeyedLock[int] = KeyedLock()
        self._recounting: dict[int, int] = {}
        """How many recount calls are waiting or running, by voting session ID."""
        self._subscription: ReactionSubscription | None = None

    def start(self) -> None:
        """Subscribe to reactions after the cog has loaded."""
        # Not a cog, so the bot does not subscribe it automatically.
        self._subscription = self.bot.reactions.subscribe(self)

    async def close(self) -> None:
        """Detach routing and settle anonymous-reaction cleanup."""
        if self._subscription is not None:
            self._subscription.detach()
        await self.bot.background_tasks.cancel(*self._background_tasks)

    def _track(self, handle: JobHandle) -> None:
        """Hold a handle for cancellation on unload, dropping the settled ones.

        A JobHandle has no completion callback, so the set is swept on insert
        rather than pruned as each job finishes.
        """
        self._background_tasks = {tracked for tracked in self._background_tasks if not tracked.finished.is_set()}
        self._background_tasks.add(handle)

    @asynccontextmanager
    async def _open_session_for(self, message_id: int) -> AsyncIterator[VoteSessionSnapshot | None]:
        """Hold the lock of the session a card belongs to, and yield the session if it is open.

        The session is read again once the lock is held, since a recount or another
        reaction may have changed it while this one waited.
        """
        found = await self.vote_service.get_session_for_message(message_id)
        if found is None or not found.is_open:
            yield None
            return
        async with self._session_locks.hold(found.id):
            snapshot = await self.vote_service.get_session(found.id)
            yield snapshot if snapshot is not None and snapshot.is_open else None

    @reaction_handler("add", recover=True)
    async def on_reaction_add(self, event: ReactionEvent) -> None:
        """Record a ballot.

        Live handling explains any actionable refusal. Recovery stays quiet and
        awaits its reaction cleanup instead of scheduling it.
        """
        async with self._open_session_for(event.payload.message_id) as snapshot:
            if snapshot is not None:
                await self._add(event, snapshot)

    async def _add(self, event: ReactionEvent, snapshot: VoteSessionSnapshot) -> None:
        payload = event.payload
        recovering = event.recovering
        assert payload.guild_id is not None  # The subscription is guild-only.
        emoji = str(payload.emoji)
        option = snapshot.option_by_emoji(emoji, payload.guild_id)
        if option is None:
            return
        message = await event.message()
        member = await event.member(humans_only=True)
        if message is None or member is None:
            return
        account_id = await self._voters.consented_account_id(member.id)
        if account_id is None:
            if not recovering:
                await self._decline_unconsented_vote(message, member, payload.emoji)
            return
        actor = await resolve_actor(self.bot, member, account_id=account_id)
        previous = snapshot.selection_for(actor.require_account_id())
        if not recovering and not snapshot.is_anonymous and previous is not None and previous.option_id != option.id:
            await self._remove_reaction(message, previous.emoji, member)
        result, changed = await self._set_reaction_selection(snapshot, actor, emoji, selected=True)
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

    @reaction_handler("remove", recover=True)
    async def on_reaction_remove(self, event: ReactionEvent) -> None:
        """Synchronize removal of a publicly retained reaction; recovery may repeat it safely."""
        async with self._open_session_for(event.payload.message_id) as snapshot:
            payload = event.payload
            recovering = event.recovering
            if snapshot is None or snapshot.is_anonymous:
                return
            # Removing a reaction needs no fresh consent and must never create an account.
            account_id = await self.bot.account_ids.resolve(self.bot.services.accounts, payload.user_id)
            if account_id is None:
                return
            member = await event.member(humans_only=True)
            if member is None:
                return
            actor = await resolve_actor(self.bot, member, account_id=account_id)
            result, changed = await self._set_reaction_selection(snapshot, actor, str(payload.emoji), selected=False)
            session = snapshot if recovering else result.session
            if result.accepted and changed and session is not None:
                await self.bot.refresh_posts("vote_session", str(session.id))

    @reaction_handler("clear", "clear_emoji", recover=True)
    async def on_reaction_clear(self, event: ReactionClearEvent) -> None:
        """Restore options after any clear without inferring that stored ballots vanished."""
        await self._restore_reactions(event.payload.message_id)

    def is_recounting(self, vote_session_id: int) -> bool:
        """Return whether a recount of this session is waiting or running."""
        return vote_session_id in self._recounting

    async def recount(self, vote_session_id: int) -> bool:
        """Rebuild one open session's ballots from its cards and refresh them if any changed.

        A second call for the same session waits for the first and then recounts again.
        Callers that should not repeat the work, such as a user command, check
        `is_recounting` first.

        Returns:
            Whether any ballot changed.
        """
        self._recounting[vote_session_id] = self._recounting.get(vote_session_id, 0) + 1
        try:
            async with self._session_locks.hold(vote_session_id):
                changed = await self._recounter.recount(vote_session_id)
        finally:
            self._recounting[vote_session_id] -= 1
            if not self._recounting[vote_session_id]:
                del self._recounting[vote_session_id]
        if changed:
            await self.bot.refresh_posts("vote_session", str(vote_session_id))
        return changed

    async def _set_reaction_selection(
        self, snapshot: VoteSessionSnapshot, actor: Actor, emoji: str, *, selected: bool
    ) -> tuple[CastVoteResult, bool]:
        """Make the stored ballot match whether this reaction is present.

        Casting toggles, so it is only called when the stored selection differs. The
        actor is a member of the guild the reaction was made in, which decides the alias.
        """
        if actor.guild_id is None:
            return CastVoteResult(snapshot, VoteRejection.WRONG_GUILD), False
        option = snapshot.option_by_emoji(emoji, actor.guild_id)
        if option is None:
            return CastVoteResult(snapshot, VoteRejection.INVALID_OPTION), False
        current = snapshot.selection_for(actor.require_account_id())
        already_selected = current is not None and current.option_id == option.id
        if selected is already_selected:
            return CastVoteResult(snapshot), False
        return await self.vote_service.cast_vote(snapshot.id, actor, option.id, emoji=emoji), True

    async def _restore_reactions(self, message_id: int) -> None:
        """Restore baseline reactions without changing stored ballots.

        A clear removes the voting controls from Discord, not the database's
        selections. Anonymous ballots cannot be reconstructed from reaction lists.
        """
        snapshot = await self.vote_service.get_session_for_message(message_id)
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

    async def _decline_unconsented_vote(
        self,
        message: discord.Message,
        user: discord.Member | discord.User,
        emoji: discord.PartialEmoji | str,
    ) -> None:
        """Remove the reaction and send a consent instruction that expires after 30 seconds.

        The notice appears in the source channel because raw reactions cannot
        receive an ephemeral interaction response. Repeated attempts can each
        produce a notice.
        """
        await self._remove_reaction(message, emoji, user)
        locale = await resolve_locale(message, self.bot.services.settings)
        with localization_scope(localization_for(locale)):
            payload = render_payload([text_node(_unconsented_vote_message(user.mention))])
        with contextlib.suppress(discord.HTTPException):
            await send_to(message.channel, delete_after=30)(payload)


def _unconsented_vote_message(user: object) -> Message:
    user = discord_md(str(user))
    return md_tr(t"vote-consent-required = {user}, voting stores your Discord user ID. Run `/account consent` first.")
