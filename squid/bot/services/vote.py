"""Voting service layer to bridge domain models with Discord bot logic.

This service layer handles the coordination between Discord entities and the voting domain models,
reducing the coupling between bot logic and database operations.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterable, AsyncIterator, Iterable
from functools import wraps
from textwrap import dedent
from typing import TYPE_CHECKING, Any, Literal, Self, final, override

import discord
from discord import PartialEmoji, Reaction
from discord.utils import classproperty
from sqlalchemy import select

from squid.bot.utils import is_staff, is_trusted_or_staff
from squid.db.builds import Build
from squid.db.schema import BuildVoteSession as SQLBuildVoteSession, VoteSessionEmoji, Emoji
from squid.db.emoji import EmojiRepository
from squid.db.schema import DeleteLogVoteSession as SQLDeleteLogVoteSession
from squid.db.schema import Message, Status
from squid.db.vote_session import (
    AbstractVoteSession,
    BuildVoteSession,
    DeleteLogVoteSession,
    get_vote_session_from_message_id,
)

if TYPE_CHECKING:
    import squid.bot


logger = logging.getLogger(__name__)


APPROVE_EMOJIS = ["👍", "✅"]
DENY_EMOJIS = ["👎", "❌"]


async def add_reactions_to_messages(
    messages: AsyncIterable[discord.Message] | Iterable[discord.Message],
    reactions: Iterable[Emoji | PartialEmoji | str | Reaction],
) -> None:
    """Add reactions to a list of messages."""
    tasks = []
    if isinstance(messages, AsyncIterator):
        async for message in messages:
            for reaction in reactions:
                tasks.append(asyncio.create_task(message.add_reaction(reaction)))
    else:
        for message in messages:
            for reaction in reactions:
                tasks.append(asyncio.create_task(message.add_reaction(reaction)))
    try:
        await asyncio.gather(*tasks)
    except discord.Forbidden:
        pass  # Bot doesn't have permission to add reactions


async def get_vote_session(
    bot: "squid.bot.RedstoneSquid", message_id: int, *, status: Literal["open", "closed"] | None = None
) -> "AbstractDiscordVoteSession[Any] | None":
    """Gets a vote session from the database.

    Args:
        bot: The bot instance to fetch messages from.
        message_id: The message ID of the vote session.
        status: The status of the vote session. If None, it will get any status.

    Returns:
        An instance of `AbstractDiscordVoteSession` if a vote session is found, otherwise None.

    Raises:
        NotImplementedError: If the vote session type is unknown.
    """
    vs = await get_vote_session_from_message_id(message_id, status=status)

    if isinstance(vs, BuildVoteSession):
        return DiscordBuildVoteSession(bot, vs)
    elif isinstance(vs, DeleteLogVoteSession):
        return DiscordDeleteLogVoteSession(bot, vs)
    else:
        logger.error(f"Unknown vote session type: {type(vs)}")
        raise NotImplementedError(f"Unknown vote session type: {type(vs)}")


class AbstractDiscordVoteSession[V: AbstractVoteSession](ABC):
    """An abstract class for a vote session that interacts with Discord."""

    def __init__(self, bot: "squid.bot.RedstoneSquid", vote_session: V):
        """
        Initialize the Discord vote session.

        Args:
            bot: The discord bot.
            vote_session: The vote session to manage.
        """
        self.bot = bot
        self.vote_session = vote_session

    @classproperty
    @abstractmethod
    def vote_session_cls(cls: type[Self]) -> type[V]:
        """The class of the vote session this Discord session manages."""
        raise NotImplementedError("Subclasses must implement the vote_session_cls property.")

    # A list of proxy properties that forward to the underlying vote session.
    @final
    @property
    @wraps(AbstractVoteSession.status.fget)
    def status(self):
        return self.vote_session.status

    @final
    @property
    @wraps(AbstractVoteSession.upvotes.fget)
    def upvotes(self):
        return self.vote_session.upvotes

    @final
    @property
    @wraps(AbstractVoteSession.downvotes.fget)
    def downvotes(self):
        return self.vote_session.downvotes

    @final
    @property
    @wraps(AbstractVoteSession.net_votes.fget)
    def net_votes(self):
        return self.vote_session.net_votes

    @final
    @property
    def is_closed(self) -> bool:
        """Whether the vote session is closed."""
        return self.vote_session.is_closed

    @final
    def __getitem__(self, user_id: int) -> int | None:
        return self.vote_session[user_id]

    @final
    def __contains__(self, user_id: int) -> bool:
        return user_id in self.vote_session

    @final
    async def set_vote(self, user_id: int, weight: int | None) -> None:
        await self.vote_session.set_vote(user_id, weight)

    async def get_voting_weight(self, server_id: int | None, user_id: int) -> float:
        """Get the voting weight of a user."""
        if await is_staff(self.bot, server_id, user_id):
            return 3
        return 1

    @abstractmethod
    async def send_message(self, channel: discord.abc.Messageable) -> discord.Message:
        """Send the initial message to the channel."""
        raise NotImplementedError

    @abstractmethod
    async def update_messages(self) -> None:
        """Update the messages with the current vote count."""
        raise NotImplementedError

    async def fetch_messages(self) -> AsyncIterator[discord.Message]:
        """Fetch all messages for this vote session."""
        async with self.bot.db.async_session() as session:
            stmt = select(Message).where(Message.id.in_(self.vote_session.message_ids))
            result = await session.execute(stmt)
            message_record = list(result.scalars().all())
            message_ids = [record.id for record in message_record]
            channel_ids = [record.channel_id for record in message_record]

            async for message in self.bot.get_or_fetch_messages(message_ids, channel_ids=channel_ids):
                yield message

    @classmethod
    async def get_open_sessions(cls: type[Self], bot: "squid.bot.RedstoneSquid") -> list[Self]:
        """Get all open vote sessions."""
        vote_sessions = await cls.vote_session_cls.get_open_sessions()
        return [cls(bot, vs) for vs in vote_sessions]

    @final
    async def close(self) -> None:
        """Close the vote session.

        Override the `on_close` method to handle the event when the vote session is closed.

        Notes:
            This method intentionally does NOT call the `on_close` method, as the event is dispatched from the database layer.
        """
        await self.vote_session.close()

    async def on_create(self) -> None:
        """Handle the event when the vote session is created.

        This is automatically called when the vote session is created, via the underlying vote session's create method -> db -> bot -> this method. By default, it does nothing.
        """

    @abstractmethod
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        """Handle the event when a reaction is added to a message in the vote session."""
        raise NotImplementedError("Subclasses must implement the on_raw_reaction_add method.")

    @abstractmethod
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        """Handle the event when a reaction is removed from a message in the vote session."""
        raise NotImplementedError("Subclasses must implement the on_raw_reaction_remove method.")

    @abstractmethod
    async def on_close(self) -> None:
        """Handle the event when the vote session passes.

        This is automatically called when the vote session is closed, via the underlying vote session's close method -> db -> bot -> this method. By default, it does nothing.
        """
        raise NotImplementedError("Subclasses must implement the on_close method.")


@final
class DiscordBuildVoteSession(AbstractDiscordVoteSession[BuildVoteSession]):
    """A Discord vote session for a build."""

    _background_tasks: set[asyncio.Task[Any]] = set()

    @classproperty
    @override
    def vote_session_cls(cls) -> type[BuildVoteSession]:
        return BuildVoteSession

    @classmethod
    async def create(
        cls: type[Self],
        bot: "squid.bot.RedstoneSquid",
        *,
        messages: AsyncIterable[discord.Message] | Iterable[discord.Message],
        type: Literal["add", "update"],
        diff: list[tuple[str, Any, Any]] | None = None,
        build: Build,
        author_id: int,
        approve_emojis: list[str],
        deny_emojis: list[str],
        pass_threshold: int,
        fail_threshold: int,
    ) -> Self:
        """Create a new instance of the vote session."""
        message_ids = [msg.id if isinstance(msg, discord.Message) else msg for msg in messages]
        if type == "add":
            diff = [("submission_status", Status.PENDING, Status.CONFIRMED)]
        elif type == "update":
            assert build.id is not None
            original = await Build.from_id(build.id)
            assert original is not None
            diff = original.diff(build)

        if approve_emojis is None:
            approve_emojis = APPROVE_EMOJIS
        if deny_emojis is None:
            deny_emojis = DENY_EMOJIS

        vote_session = BuildVoteSession(
            message_ids, author_id, build, diff, pass_threshold, fail_threshold, approve_emojis, deny_emojis
        )
        instance = cls(bot, vote_session)
        async with bot.db.async_session() as session:
            emoji_repo = EmojiRepository(session)
            sql_vote_session = SQLBuildVoteSession(
                status="open",
                author_id=author_id,
                kind=vote_session.kind,
                pass_threshold=pass_threshold,
                fail_threshold=fail_threshold,
                build_id=vote_session.build.id,
                changes=vote_session.diff,
            )
            session.add(sql_vote_session)
            await session.flush()
            for emoji in await emoji_repo.get_emojis_by_symbols(approve_emojis):
                sql_vote_session.vote_session_emojis.append(
                    VoteSessionEmoji(vote_session_id=sql_vote_session.id, emoji_id=emoji.id)
                )
            for emoji in await emoji_repo.get_emojis_by_symbols(deny_emojis):
                sql_vote_session.vote_session_emojis.append(
                    VoteSessionEmoji(vote_session_id=sql_vote_session.id, emoji_id=emoji.id, default_multiplier=-1)
                )
            await session.commit()

        await asyncio.gather(
            *(
                bot.db.message.track_message(message, "vote", build_id=build.id, vote_session_id=sql_vote_session.id)
                for message in messages
            ),
            instance.update_messages(),
            add_reactions_to_messages(messages, [APPROVE_EMOJIS[0], DENY_EMOJIS[0]]),
        )

        return instance

    @override
    async def send_message(self, channel: discord.abc.Messageable) -> discord.Message:
        build = self.vote_session.build
        message = await channel.send(
            content=build.original_link, embed=await self.bot.for_build(build).generate_embed()
        )
        await self.bot.db.message.track_message(
            message, purpose="vote", build_id=build.id, vote_session_id=self.vote_session.id
        )
        return message

    @override
    async def update_messages(self):
        vs = self.vote_session
        embed = await self.bot.for_build(vs.build).generate_embed()
        embed.add_field(name="", value="", inline=False)  # Add a blank field to separate the vote count
        embed.add_field(name="Accept", value=f"{vs.upvotes}/{vs.pass_threshold}", inline=True)
        embed.add_field(name="Deny", value=f"{vs.downvotes}/{-vs.fail_threshold}", inline=True)
        await asyncio.gather(
            *[  # Has to unpack a list here because () is interpreted as a generator
                asyncio.create_task(message.edit(content=vs.build.original_link, embed=embed))
                async for message in self.fetch_messages()
            ]
        )

    @override
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        # This must be before the removal of the reaction to prevent the bot from removing its own reaction
        if payload.user_id == self.bot.user.id:  # type: ignore
            return

        # Remove the user's reaction to keep votes anonymous
        message = await self.bot.get_or_fetch_message(payload.message_id, channel_id=payload.channel_id)
        if message is None:
            logger.warning(
                f"Message with ID {payload.message_id} not found in channel {payload.channel_id}. "
                "This could be a case where the message is quickly deleted after the reaction was added."
            )
            return
        user = self.bot.get_user(payload.user_id)
        assert user is not None
        remove_reaction_task = asyncio.create_task(message.remove_reaction(payload.emoji, user))
        self._background_tasks.add(remove_reaction_task)
        remove_reaction_task.add_done_callback(self._background_tasks.discard)

        if user.bot:
            return  # Ignore bot reactions

        # Update votes based on the reaction
        emoji_name = str(payload.emoji)
        user_id = payload.user_id

        # The vote session will handle the closing of the vote session
        original_vote = self[user_id]
        weight = await self.get_voting_weight(payload.guild_id, user_id)
        if emoji_name in APPROVE_EMOJIS:
            await self.set_vote(user_id, weight if original_vote != weight else 0)
        elif emoji_name in DENY_EMOJIS:
            await self.set_vote(user_id, -weight if original_vote != -weight else 0)
        else:
            return
        await self.update_messages()

    @override
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        pass

    @override
    async def on_close(self) -> None:
        """Handle the event when the vote session passes."""
        if self.vote_session.status == "passed":
            self.bot.dispatch("build_confirmed", self.vote_session.build)
        await self.update_messages()


@final
class DiscordDeleteLogVoteSession(AbstractDiscordVoteSession[DeleteLogVoteSession]):
    """A Discord vote session for deleting a log message."""

    _background_tasks: set[asyncio.Task[Any]] = set()

    @classproperty
    @override
    def vote_session_cls(cls: type["DiscordDeleteLogVoteSession"]) -> type[DeleteLogVoteSession]:
        return DeleteLogVoteSession

    @classmethod
    async def create(
        cls: type[Self],
        bot: "squid.bot.RedstoneSquid",
        messages: AsyncIterable[discord.Message] | Iterable[discord.Message],
        author_id: int,
        target_message: discord.Message,
        pass_threshold: int,
        fail_threshold: int,
        approve_emojis: list[str],
        deny_emojis: list[str],
    ) -> Self:
        """Create a new instance of the vote session."""
        # Sample implementation of create method
        message_ids = [msg.id if isinstance(msg, discord.Message) else msg for msg in messages]
        vote_session = DeleteLogVoteSession(
            message_ids,
            author_id,
            target_message.id,
            target_message.channel.id,
            target_message.guild.id,
            pass_threshold,
            fail_threshold,
            approve_emojis,
            deny_emojis,
        )
        instance = cls(bot, vote_session)
        async with bot.db.async_session() as session:
            emoji_repo = EmojiRepository(session)
            sql_vote_session = SQLDeleteLogVoteSession(
                status="open",
                author_id=author_id,
                kind=vote_session.kind,
                pass_threshold=pass_threshold,
                fail_threshold=fail_threshold,
                target_message_id=target_message.id,
                target_channel_id=target_message.channel.id,
                target_server_id=target_message.guild.id,  # type: ignore
            )
            session.add(sql_vote_session)
            await session.flush()
            for emoji in await emoji_repo.get_emojis_by_symbols(approve_emojis):
                sql_vote_session.vote_session_emojis.append(
                    VoteSessionEmoji(vote_session_id=sql_vote_session.id, emoji_id=emoji.id)
                )
            for emoji in await emoji_repo.get_emojis_by_symbols(deny_emojis):
                sql_vote_session.vote_session_emojis.append(
                    VoteSessionEmoji(vote_session_id=sql_vote_session.id, emoji_id=emoji.id, default_multiplier=-1)
                )
            await session.commit()

        await asyncio.gather(
            *(
                bot.db.message.track_message(message, "vote", vote_session_id=sql_vote_session.id)
                for message in messages
            ),
            instance.update_messages(),
            add_reactions_to_messages(messages, [APPROVE_EMOJIS[0], DENY_EMOJIS[0]]),
        )
        return instance

    async def get_target_message(self) -> discord.Message | None:
        """Fetch the target message for the vote session."""
        return await self.bot.get_or_fetch_message(self.vote_session.target_message_id)

    @override
    async def send_message(self, channel: discord.abc.Messageable) -> discord.Message:
        """Send the initial message to the channel."""
        vs = self.vote_session
        target = await self.get_target_message()
        embed = discord.Embed(
            title="Vote to Delete Log",
            description=(
                dedent(f"""
                React with {APPROVE_EMOJIS[0]} to upvote or {DENY_EMOJIS[0]} to downvote.\n\n
                **Log Content:**\n{target.content}\n\n
                **Upvotes:** {vs.upvotes}
                **Downvotes:** {vs.downvotes}
                **Net Votes:** {vs.net_votes}""")
            ),
        )
        return await channel.send(embed=embed)

    @override
    async def update_messages(self) -> None:
        """Updates the message with the current vote count."""
        vs = self.vote_session
        target = await self.get_target_message()
        log_content = target.content if target is not None else "Message not found or deleted."
        if vs.status == "open":
            embed = discord.Embed(
                title="Vote to Delete Log",
                description=(
                    dedent(f"""
                    React with {APPROVE_EMOJIS[0]} to upvote or {DENY_EMOJIS[0]} to downvote.\n\n
                    **Log Content:**\n{log_content}\n\n
                    **Upvotes:** {vs.upvotes}
                    **Downvotes:** {vs.downvotes}
                    **Net Votes:** {vs.net_votes}""")
                ),
            )
        elif vs.status == "passed":
            embed = discord.Embed(
                title="Vote to Delete Log: Passed",
                description=(
                    dedent(f"""
                    **Log Content:**\n{log_content}\n\n
                    **Upvotes:** {vs.upvotes}
                    **Downvotes:** {vs.downvotes}
                    **Net Votes:** {vs.net_votes}""")
                ),
            )
        elif vs.status == "failed":
            embed = discord.Embed(
                title="Vote to Delete Log: Failed",
                description=(
                    dedent(f"""
                    **Log Content:**\n{log_content}\n\n
                    **Upvotes:** {vs.upvotes}
                    **Downvotes:** {vs.downvotes}
                    **Net Votes:** {vs.net_votes}""")
                ),
            )
        else:
            embed = discord.Embed(
                title="Vote to Delete Log: Closed",
                description=(
                    dedent(f"""
                    **Log Content:**\n{log_content}\n\n
                    **Upvotes:** {vs.upvotes}
                    **Downvotes:** {vs.downvotes}
                    **Net Votes:** {vs.net_votes}""")
                ),
            )
        await asyncio.gather(  # Has to unpack a list here because () is interpreted as a generator
            *[asyncio.create_task(message.edit(embed=embed)) async for message in self.fetch_messages()]
        )

    @override
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        # Remove the user's reaction to keep votes anonymous
        message = await self.bot.get_or_fetch_message(payload.message_id, channel_id=payload.channel_id)
        if message is None:
            logger.warning(
                f"Message with ID {payload.message_id} not found in channel {payload.channel_id}. "
                "This could be a case where the message is quickly deleted after the reaction was added."
            )
            return

        user = self.bot.get_user(payload.user_id)
        assert user is not None
        remove_reaction_task = asyncio.create_task(message.remove_reaction(payload.emoji, user))
        self._background_tasks.add(remove_reaction_task)
        remove_reaction_task.add_done_callback(self._background_tasks.discard)

        if user.bot:
            return  # Ignore bot reactions

        # Update votes based on the reaction
        emoji_name = str(payload.emoji)
        user_id = payload.user_id

        # Check if the user has a trusted role
        if payload.guild_id is None:
            raise NotImplementedError("Cannot vote in DMs.")

        if await is_trusted_or_staff(self.bot, payload.guild_id, user_id):
            pass
        else:
            await message.channel.send("You do not have a trusted role.")
            return

        # The vote session will handle the closing of the vote session
        original_vote = self[user_id]
        weight = await self.get_voting_weight(payload.guild_id, user_id)
        if emoji_name in APPROVE_EMOJIS:
            await self.set_vote(user_id, weight if original_vote != weight else 0)
        elif emoji_name in DENY_EMOJIS:
            await self.set_vote(user_id, -weight if original_vote != -weight else 0)
        else:
            return
        await self.update_messages()

    @override
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        pass

    @override
    async def on_close(self) -> None:
        """Handle the event when the vote session passes."""
        vs = self.vote_session
        target = await self.get_target_message()
        if vs.status == "passed" and target is not None:
            await target.delete()
        await self.update_messages()
