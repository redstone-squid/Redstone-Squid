"""Voting service layer to bridge domain models with Discord bot logic.

This service layer handles the coordination between Discord entities and the voting domain models,
reducing the coupling between bot logic and database operations.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterable, AsyncIterator, Iterable
from textwrap import dedent
from typing import TYPE_CHECKING, Any, Literal, Self, final, override

import discord
from discord import Emoji, PartialEmoji, Reaction
from discord.utils import classproperty
from sqlalchemy import select

from squid.bot.utils import is_staff, is_trusted_or_staff
from squid.db.builds import Build
from squid.db.schema import BuildVoteSession as SQLBuildVoteSession
from squid.db.schema import DeleteLogVoteSession as SQLDeleteLogVoteSession
from squid.db.schema import Message, Status, VoteSessionEmoji
from squid.db.vote_session import (
    AbstractVoteSession,
    BuildVoteSession,
    DeleteLogVoteSession,
    get_vote_session_by_id,
    get_vote_session_from_message_id,
)
from squid.utils import async_iterator, fire_and_forget

if TYPE_CHECKING:
    import squid.bot


logger = logging.getLogger(__name__)


async def add_reactions_to_messages(
    messages: AsyncIterable[discord.Message] | Iterable[discord.Message],
    reactions: Iterable[Emoji | PartialEmoji | str | Reaction],
) -> None:
    """Add reactions to a list of messages."""
    tasks = []
    async for message in async_iterator(messages):
        for reaction in reactions:
            tasks.append(asyncio.create_task(message.add_reaction(reaction)))

    try:
        await asyncio.gather(*tasks)
    except discord.Forbidden:
        pass  # Bot doesn't have permission to add reactions


async def get_vote_session(
    bot: "squid.bot.RedstoneSquid",
    *,
    id: int | None = None,
    message_id: int | None = None,
    status: Literal["open", "closed"] | None = None,
) -> "AbstractDiscordVoteSession[Any] | None":
    """Gets a vote session from the database.

    Args:
        bot: The bot instance to fetch messages from.
        id: The ID of the vote session to fetch.
        message_id: The ID of a message associated with the vote session.
        status: The status of the vote session. If None, it will get any status.

    Returns:
        An instance of `AbstractDiscordVoteSession` if a vote session is found, otherwise None.

    Raises:
        ValueError: If neither `id` nor `message_id` is provided.
        NotImplementedError: If the vote session type is unknown.
    """
    if id is None and message_id is None:
        raise ValueError("Either 'id' or 'message_id' must be provided to get a vote session.")
    if id is not None and message_id is not None:
        raise ValueError("Only one of 'id' or 'message_id' can be provided to get a vote session.")

    if id:
        vs = await get_vote_session_by_id(id, status=status)
    elif message_id:
        vs = await get_vote_session_from_message_id(message_id, status=status)
    else:
        assert False

    if vs is None:
        return None

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
    def vote_session_cls(cls) -> type[V]:
        """The class of the vote session this Discord session manages."""
        raise NotImplementedError("Subclasses must implement the vote_session_cls property.")

    # A list of proxy properties that forward to the underlying vote session.
    @final
    @property
    def status(self):
        return self.vote_session.status

    @final
    @property
    def result(self):
        return self.vote_session.result

    @final
    @property
    def upvotes(self):
        return self.vote_session.upvotes

    @final
    @property
    def downvotes(self):
        return self.vote_session.downvotes

    @final
    @property
    def net_votes(self):
        return self.vote_session.net_votes

    @final
    @property
    def is_closed(self) -> bool:
        """Whether the vote session is closed."""
        return self.vote_session.is_closed

    @final
    def __getitem__(self, user_id: int) -> float | None:
        return self.vote_session[user_id]

    @final
    def __contains__(self, user_id: int) -> bool:
        return user_id in self.vote_session

    @final
    async def set_vote(self, user_id: int, weight: float | None, emoji: str | None = None) -> None:
        await self.vote_session.set_vote(user_id, weight, emoji)

    async def get_voting_weight(self, server_id: int | None, user_id: int, emoji: str) -> float | None:
        """Get the voting weight of a user using this emoji."""
        base_multiplier = await self.vote_session.get_emoji_multiplier(emoji)
        if base_multiplier is None:
            return None
        if await is_staff(self.bot, server_id, user_id):
            user_multiplier = 3
        else:
            user_multiplier = 1
        return user_multiplier * base_multiplier

    async def override_vote(self, user_id: int, guild_id: int | None, emoji: str) -> None:
        """Override the user's vote based on the emoji reaction.

        Raises:
            ValueError: If the emoji is not recognized as an approve or deny emoji.
        """
        original_vote = self[user_id]
        weight = await self.get_voting_weight(guild_id, user_id, emoji)
        if emoji in self.vote_session.approve_emojis:
            assert weight is not None
            await self.set_vote(user_id, weight if original_vote != weight else 0, emoji)
        elif emoji in self.vote_session.deny_emojis:
            assert weight is not None
            await self.set_vote(user_id, -weight if original_vote != -weight else 0, emoji)
        else:
            raise ValueError(
                f"Unknown emoji: {emoji}. Must be one of {self.vote_session.approve_emojis + self.vote_session.deny_emojis}."
            )

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
                if message is not None:
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
        """Handles discord-specific logic when the vote session is closed. Do NOT interact with the database here.

        This is automatically called when the vote session is closed, via the underlying vote session's close
        method -> db trigger -> db event_outbox -> bot event_dispatcher -> cogs handling the event -> this method.
        """
        raise NotImplementedError("Subclasses must implement the on_close method.")


@final
class DiscordBuildVoteSession(AbstractDiscordVoteSession[BuildVoteSession]):
    """A Discord vote session for a build."""

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
        messages = [msg async for msg in async_iterator(messages)]
        message_ids = [msg.id for msg in messages]
        if type == "add":
            diff = [("submission_status", Status.PENDING, Status.CONFIRMED)]
        elif type == "update":
            assert build.id is not None
            original = await Build.from_id(build.id)
            assert original is not None
            diff = original.diff(build)

        vote_session = BuildVoteSession(
            message_ids, author_id, build, diff, pass_threshold, fail_threshold, approve_emojis, deny_emojis
        )
        instance = cls(bot, vote_session)
        async with bot.db.async_session() as session:
            assert vote_session.build.id is not None
            sql_vote_session = SQLBuildVoteSession(
                status="open",
                author_id=author_id,
                kind=vote_session.kind,
                result=vote_session.result,
                pass_threshold=pass_threshold,
                fail_threshold=fail_threshold,
                build_id=vote_session.build.id,
                changes=vote_session.diff,
            )
            session.add(sql_vote_session)
            await session.flush()
            for emoji in approve_emojis:
                sql_vote_session.vote_session_emojis.append(
                    VoteSessionEmoji(vote_session_id=sql_vote_session.id, emoji=emoji)
                )
            for emoji in deny_emojis:
                sql_vote_session.vote_session_emojis.append(
                    VoteSessionEmoji(vote_session_id=sql_vote_session.id, emoji=emoji, default_multiplier=-1)
                )
            await session.commit()

        await asyncio.gather(
            *(
                bot.db.message.track_message(message, "vote", build_id=build.id, vote_session_id=sql_vote_session.id)
                for message in messages
            ),
            instance.update_messages(),
            add_reactions_to_messages(messages, [approve_emojis[0], deny_emojis[0]]),
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
        fire_and_forget(message.remove_reaction(payload.emoji, user))

        if user.bot:
            return  # Ignore bot reactions

        # Update votes based on the reaction
        emoji_name = payload.emoji.name
        assert emoji_name is not None, "Found a deleted discord emoji on reaction add???"
        # The vote session will handle the closing of the vote session
        if emoji_name in (self.vote_session.approve_emojis + self.vote_session.deny_emojis):
            await self.override_vote(payload.user_id, payload.guild_id, emoji_name)
            await self.update_messages()

    @override
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        pass

    @override
    async def on_close(self) -> None:
        """Handle the event when the vote session passes."""
        if self.vote_session.result == "approved":
            self.bot.dispatch("build_confirmed", self.vote_session.build)
        await self.update_messages()


@final
class DiscordDeleteLogVoteSession(AbstractDiscordVoteSession[DeleteLogVoteSession]):
    """A Discord vote session for deleting a log message."""

    @classproperty
    @override
    def vote_session_cls(cls) -> type[DeleteLogVoteSession]:
        return DeleteLogVoteSession

    @classmethod
    async def create(
        cls: type[Self],
        bot: "squid.bot.RedstoneSquid",
        messages: Iterable[discord.Message],
        author_id: int,
        target_message: discord.Message,
        pass_threshold: int,
        fail_threshold: int,
        approve_emojis: list[str],
        deny_emojis: list[str],
    ) -> Self:
        """Create a new instance of the vote session.

        Raises:
            NotImplementedError: If target_message is not in a guild.
        """
        if target_message.guild is None:
            raise NotImplementedError("Target message must be in a guild.")
        messages = [msg async for msg in async_iterator(messages)]
        message_ids = [msg.id for msg in messages]
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
            for emoji in approve_emojis:
                sql_vote_session.vote_session_emojis.append(
                    VoteSessionEmoji(vote_session_id=sql_vote_session.id, emoji=emoji)
                )
            for emoji in deny_emojis:
                sql_vote_session.vote_session_emojis.append(
                    VoteSessionEmoji(vote_session_id=sql_vote_session.id, emoji=emoji, default_multiplier=-1)
                )
            await session.commit()

        await asyncio.gather(
            *(
                bot.db.message.track_message(message, "vote", vote_session_id=sql_vote_session.id)
                for message in messages
            ),
            instance.update_messages(),
            add_reactions_to_messages(messages, [approve_emojis[0], deny_emojis[0]]),
        )
        return instance

    async def get_target_message(self) -> discord.Message | None:
        """Fetch the target message for the vote session."""
        return await self.bot.get_or_fetch_message(
            self.vote_session.target_message_id, channel_id=self.vote_session.target_channel_id
        )

    @override
    async def send_message(self, channel: discord.abc.Messageable) -> discord.Message:
        """Send the initial message to the channel."""
        vs = self.vote_session
        target = await self.get_target_message()
        log_content = target.content if target is not None else "Message not found or deleted."
        embed = discord.Embed(
            title="Vote to Delete Log",
            description=(
                dedent(f"""
                React with {vs.approve_emojis[0]} to upvote or {vs.deny_emojis[0]} to downvote.\n\n
                **Log Content:**\n{log_content}\n\n
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
        if vs.result == "pending":
            embed = discord.Embed(
                title="Vote to Delete Log",
                description=(
                    dedent(f"""
                    React with {vs.approve_emojis[0]} to upvote or {vs.deny_emojis[0]} to downvote.\n\n
                    **Log Content:**\n{log_content}\n\n
                    **Upvotes:** {vs.upvotes}
                    **Downvotes:** {vs.downvotes}
                    **Net Votes:** {vs.net_votes}""")
                ),
            )
        elif vs.result == "approved":
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
        elif vs.result == "denied":
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
        else:  # cancelled
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
        fire_and_forget(message.remove_reaction(payload.emoji, user))

        if user.bot:
            return  # Ignore bot reactions

        # Update votes based on the reaction
        emoji_name = payload.emoji.name
        assert emoji_name is not None, "Found a deleted discord emoji on reaction add???"

        # Check if the user has a trusted role
        if payload.guild_id is None:
            raise NotImplementedError("Cannot vote in DMs.")

        if await is_trusted_or_staff(self.bot, payload.guild_id, payload.user_id):
            pass
        else:
            await message.channel.send("You do not have a trusted role.")
            return

        # The vote session will handle the closing of the itself
        if emoji_name in (self.vote_session.approve_emojis + self.vote_session.deny_emojis):
            await self.override_vote(payload.user_id, payload.guild_id, emoji_name)
            await self.update_messages()

    @override
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        pass

    @override
    async def on_close(self) -> None:
        """Handle the event when the vote session passes."""
        vs = self.vote_session
        target = await self.get_target_message()
        if target is None:
            logger.warning("Target message not found or deleted.")
        if vs.result == "approved" and target is not None:
            await target.delete()
        await self.update_messages()
