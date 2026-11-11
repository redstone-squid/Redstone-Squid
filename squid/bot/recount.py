"""The /recount command, which repairs counts that missed reactions while the bot was offline.

Reactions on vote cards and starboard source messages normally reach the database as live
events. One added while the bot was offline is never delivered, so its vote or star is missing
until someone recounts. A recount reads every reacting user from Discord, which can take many
requests, so the bot never does it on its own (apart from a poll's final recount before it
closes) and this command is limited in two ways:

- One recount of a given vote session or source message runs at a time.
- Each guild may start one recount every `GUILD_COOLDOWN_SECONDS`.
"""

import re
import time
from collections.abc import Awaitable, Callable
from math import ceil
from typing import TYPE_CHECKING, Self, cast

from discord import app_commands

import squid_ui_discord as sd
from squid.bot.ui import text_node
from squid.bot.utils.permissions import enforce
from squid.core.i18n import tr
from squid.permissions.domain.catalogue import STARBOARD_BOARD_RECOUNT
from squid_ui.text import Message
from squid_ui_discord.i18n import to_locale_str

if TYPE_CHECKING:
    import squid.bot.app
    from squid.bot.starboard.cog import StarboardCog
    from squid.bot.voting.vote import VoteCog

GUILD_COOLDOWN_SECONDS = 30.0
"""How long a guild waits after starting a recount before it may start another."""

_MESSAGE_LINK = re.compile(
    r"https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/channels/(?P<guild>\d+)/(?P<channel>\d+)/(?P<message>\d+)"
)


class RecountCog[BotT: squid.bot.app.RedstoneSquid](sd.Cog[BotT]):
    """Recount the reactions on one vote card or starboard source message."""

    def __init__(self, bot: BotT) -> None:
        super().__init__(bot)
        self._last_started: dict[int, float] = {}
        """When each guild last started a recount, in `time.monotonic` seconds."""
        self._origins_in_progress: set[int] = set()

    @sd.command(
        name=to_locale_str(tr(t"command-recount-name = recount")),
        defer="private",
        description=to_locale_str(
            tr(t"command-recount-help = Count the reactions on a vote or starboard message again.")
        ),
    )
    @app_commands.rename(message=to_locale_str(tr(t"command-recount-message-name = message")))
    @app_commands.guild_only()
    @app_commands.describe(
        message=to_locale_str(
            tr(t"command-recount-message-help = Link to a vote card, a starboard post, or the message it shows")
        )
    )
    async def recount(self, request: sd.Request[Self], message: str) -> sd.CommandResult:
        """Count the reactions on a vote or starboard message again."""
        guild = request.guild
        assert guild is not None

        async def authorize_starboard() -> None:
            await enforce(request, STARBOARD_BOARD_RECOUNT)

        return text_node(await self.recount_link(guild.id, message, authorize_starboard=authorize_starboard))

    async def recount_link(
        self, guild_id: int, link: str, *, authorize_starboard: Callable[[], Awaitable[None]]
    ) -> Message:
        """Recount the message a link points at and describe the outcome to the requester.

        Anyone may recount a vote card. A starboard recount calls `authorize_starboard`
        first, which raises if the requester may not recount starboards.
        """
        match = _MESSAGE_LINK.fullmatch(link.strip())
        if match is None or int(match["guild"]) != guild_id:
            return tr(t"bot-recount-give-a-link-to-a-message-in = Give a link to a message in this server.")
        channel_id, message_id = int(match["channel"]), int(match["message"])

        # A vote card, recognized by its session.
        snapshot = await self.bot.services.votes.get_session_for_message(message_id)
        if snapshot is not None:
            vote_cog = cast("VoteCog[BotT] | None", self.bot.get_cog("VoteCog"))
            if not snapshot.is_open or vote_cog is None:
                return tr(t"bot-recount-that-vote-is-already-closed = That vote is already closed.")
            if vote_cog.reactions.is_recounting(snapshot.id):
                return tr(t"bot-recount-that-vote-is-already-being-recounted = That vote is already being recounted.")
            if (wait := self._start(guild_id)) is not None:
                return _cooldown(wait)
            changed = await vote_cog.reactions.recount(snapshot.id)
            return (
                tr(t"bot-recount-votes-recounted = Votes recounted.")
                if changed
                else tr(t"recount-votes-already-current = Votes recounted; nothing had been missed.")
            )

        # A starboard post or the source message it mirrors.
        starboard_cog = cast("StarboardCog[BotT] | None", self.bot.get_cog("StarboardCog"))
        if starboard_cog is None:
            return tr(t"bot-recount-that-message-is-not-a-vote-or = That message is not a vote or a starboard message.")
        await authorize_starboard()
        origin_channel_id, origin_id = await self._starboard_origin(channel_id, message_id)
        if origin_id in self._origins_in_progress:
            return tr(t"recount-already-running = That message is already being recounted.")
        origin = await self.bot.get_or_fetch_message(origin_channel_id, origin_id)
        if origin is None:
            return tr(t"bot-recount-i-cannot-read-that-message = I cannot read that message.")
        if (wait := self._start(guild_id)) is not None:
            return _cooldown(wait)
        self._origins_in_progress.add(origin_id)
        try:
            keys = await starboard_cog.recount(origin)
        finally:
            self._origins_in_progress.discard(origin_id)
        if not keys:
            return tr(t"recount-message-has-no-starboard = No starboard counts reactions on that message.")
        return tr(t"bot-recount-starboard-reactions-recounted = Starboard reactions recounted.")

    async def _starboard_origin(self, channel_id: int, message_id: int) -> tuple[int, int]:
        """Return the source message a starboard post mirrors, or the given message itself."""
        post = await self.bot.services.posts.resolve(message_id)
        if post is None or post.resource_kind != "starboard_entry":
            return channel_id, message_id
        starboard_id, _, origin_id = post.resource_key.partition(":")
        state = await self.bot.services.starboards.entry_state(int(starboard_id), int(origin_id))
        if state is None:
            return channel_id, message_id
        return state.origin.channel_id, state.origin.id

    def _start(self, guild_id: int) -> float | None:
        """Record that a guild starts a recount, or return the seconds it must still wait."""
        now = time.monotonic()
        last = self._last_started.get(guild_id)
        if last is not None and now - last < GUILD_COOLDOWN_SECONDS:
            return GUILD_COOLDOWN_SECONDS - (now - last)
        self._last_started[guild_id] = now
        return None


def _cooldown(seconds: float) -> Message:
    whole = max(1, ceil(seconds))
    return tr(t"recount-server-rate-limited = This server ran a recount recently. Try again in {whole} seconds.")


async def setup(bot: squid.bot.app.RedstoneSquid) -> None:
    await bot.add_cog(RecountCog(bot))
