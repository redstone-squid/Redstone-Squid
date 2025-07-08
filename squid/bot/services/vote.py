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
from discord import Emoji, PartialEmoji, Reaction
from discord.utils import classproperty
from sqlalchemy import insert, select

from squid.bot.utils import is_staff, is_trusted_or_staff
from squid.db import DatabaseManager
from squid.db.builds import Build
from squid.db.schema import BuildVoteSession as SQLBuildVoteSession
from squid.db.schema import DeleteLogVoteSession as SQLDeleteLogVoteSession
from squid.db.schema import Message, Status
from squid.db.vote_session import (
    AbstractVoteSession,
    BuildVoteSession,
    DeleteLogVoteSession,
    track_vote_session,
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
    vs = get_vote_session_from_message_id(message_id, status=status)

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
        await track_vote_session(
            message_ids, author_id, vote_session.kind, pass_threshold, fail_threshold, approve_emojis, deny_emojis
        )

        instance = cls(bot, vote_session)
        async with DatabaseManager().async_session() as session:
            stmt = insert(SQLBuildVoteSession).values(
                vote_session_id=vote_session.id,
                build_id=vote_session.build.id,
                changes=vote_session.diff,
            )
            await session.execute(stmt)
            await session.commit()

        await instance.update_messages()
        await add_reactions_to_messages(messages, [APPROVE_EMOJIS[0], DENY_EMOJIS[0]])

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
            *(
                asyncio.create_task(message.edit(content=vs.build.original_link, embed=embed))
                async for message in self.fetch_messages()
            )
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
        await track_vote_session(messages, author_id, vote_session.kind, pass_threshold, fail_threshold)
        async with bot.db.async_session() as session:
            stmt = insert(SQLDeleteLogVoteSession).values(
                vote_session_id=vote_session.id,
                target_message_id=target_message.id,
                target_channel_id=target_message.channel.id,
                target_server_id=target_message.guild.id,  # type: ignore
            )
            await session.execute(stmt)
            await session.commit()
        instance = cls(bot, vote_session)
        await instance.update_messages()
        await add_reactions_to_messages(messages, [APPROVE_EMOJIS[0], DENY_EMOJIS[0]])
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
        await asyncio.gather(
            *(asyncio.create_task(message.edit(embed=embed)) async for message in self.fetch_messages())
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


# class VotingService:
#     """Service for voting operations that bridges Discord bot logic with domain models."""
#
#     def __init__(self, bot: "squid.bot.RedstoneSquid", voting_repo: VotingRepository):
#         self.bot = bot
#         self._voting_repo = voting_repo
#
#     async def create_build_vote_session(
#         self,
#         messages: list[discord.Message],
#         author_id: int,
#         build: Build,
#         vote_type: Literal["add", "update"],
#         pass_threshold: int = 3,
#         fail_threshold: int = -3,
#     ) -> BuildVoteSession:
#         """Create a new build vote session."""
#         # Determine changes for the vote session
#         if vote_type == "add":
#             changes = [("submission_status", "PENDING", "CONFIRMED")]
#         else:
#             assert build.id is not None
#             original = await Build.from_id(build.id)
#             assert original is not None
#             changes = original.diff(build)
#
#         # Create the vote session
#         session_id = await self._voting_repo.vote_sessions.create_vote_session(
#             author_id=author_id,
#             kind="build",
#             pass_threshold=pass_threshold,
#             fail_threshold=fail_threshold,
#             message_ids=[msg.id for msg in messages],
#             build_id=build.id,
#         )
#
#         # Create the build-specific vote session data
#         assert build.id is not None
#         await self._voting_repo.build_vote_sessions.create_build_vote_session(
#             vote_session_id=session_id,
#             build_id=build.id,
#             changes=changes,
#         )
#
#         # Track messages for the vote session
#         await self._track_messages_for_vote_session(messages, session_id, build.id)
#
#         # Add reactions to messages
#         await self._add_voting_reactions(messages)
#
#         # Return the domain model
#         return BuildVoteSession(
#             id=session_id,
#             author_id=author_id,
#             pass_threshold=pass_threshold,
#             fail_threshold=fail_threshold,
#             build_id=build.id,
#             changes=changes,
#             message_ids={msg.id for msg in messages},
#         )
#
#     async def create_delete_log_vote_session(
#         self,
#         messages: list[discord.Message],
#         author_id: int,
#         target_message: discord.Message,
#         pass_threshold: int = 3,
#         fail_threshold: int = -3,
#     ) -> DeleteLogVoteSession:
#         """Create a new delete log vote session."""
#         # Create the vote session
#         session_id = await self._voting_repo.vote_sessions.create_vote_session(
#             author_id=author_id,
#             kind="delete_log",
#             pass_threshold=pass_threshold,
#             fail_threshold=fail_threshold,
#             message_ids=[msg.id for msg in messages],
#         )
#
#         # Create the delete log specific vote session data
#         await self._voting_repo.delete_log_vote_sessions.create_delete_log_vote_session(
#             vote_session_id=session_id,
#             target_message_id=target_message.id,
#             target_channel_id=target_message.channel.id,
#             target_server_id=target_message.guild.id if target_message.guild else 0,
#         )
#
#         # Track messages for the vote session
#         await self._track_messages_for_vote_session(messages, session_id)
#
#         # Add reactions to messages
#         await self._add_voting_reactions(messages)
#
#         # Return the domain model
#         return DeleteLogVoteSession(
#             id=session_id,
#             author_id=author_id,
#             pass_threshold=pass_threshold,
#             fail_threshold=fail_threshold,
#             target_message_id=target_message.id,
#             target_channel_id=target_message.channel.id,
#             target_server_id=target_message.guild.id if target_message.guild else 0,
#             message_ids={msg.id for msg in messages},
#         )
#
#     async def get_vote_session_by_message_id(self, message_id: int) -> VoteSession | None:
#         """Get a vote session by message ID."""
#         return await self._voting_repo.vote_sessions.get_vote_session_by_message_id(message_id)
#
#     async def get_build_vote_session_by_id(self, session_id: int) -> BuildVoteSession | None:
#         """Get a build vote session by its ID."""
#         return await self._voting_repo.build_vote_sessions.get_build_vote_session(session_id)
#
#     async def get_delete_log_vote_session_by_id(self, session_id: int) -> DeleteLogVoteSession | None:
#         """Get a delete log vote session by its ID."""
#         return await self._voting_repo.delete_log_vote_sessions.get_delete_log_vote_session(session_id)
#
#     async def get_open_build_vote_sessions(self) -> list[BuildVoteSession]:
#         """Get all open build vote sessions."""
#         # Get base vote sessions
#         base_sessions = await self._voting_repo.vote_sessions.get_open_vote_sessions_by_kind("build")
#
#         # Convert to build vote sessions
#         build_sessions = []
#         for session in base_sessions:
#             if session.id is not None:
#                 build_session = await self.get_build_vote_session_by_id(session.id)
#                 if build_session:
#                     build_sessions.append(build_session)
#
#         return build_sessions
#
#     async def get_open_delete_log_vote_sessions(self) -> list[DeleteLogVoteSession]:
#         """Get all open delete log vote sessions."""
#         # Get base vote sessions
#         base_sessions = await self._voting_repo.vote_sessions.get_open_vote_sessions_by_kind("delete_log")
#
#         # Convert to delete log vote sessions
#         delete_log_sessions = []
#         for session in base_sessions:
#             if session.id is not None:
#                 delete_session = await self.get_delete_log_vote_session_by_id(session.id)
#                 if delete_session:
#                     delete_log_sessions.append(delete_session)
#
#         return delete_log_sessions
#
#     async def cast_vote(self, vote_session: VoteSession, user_id: int, weight: float | None) -> None:
#         """Cast a vote in a vote session."""
#         if vote_session.id is None:
#             raise ValueError("Cannot cast vote in untracked vote session")
#
#         # Update the domain model
#         if weight is None:
#             vote_session.votes.pop(user_id, None)
#         else:
#             vote_session.votes[user_id] = weight
#
#         # Persist to database
#         await self._voting_repo.votes.upsert_vote(vote_session.id, user_id, weight)
#
#     async def close_vote_session(self, vote_session: VoteSession) -> None:
#         """Close a vote session."""
#         if vote_session.id is None:
#             raise ValueError("Cannot close untracked vote session")
#
#         vote_session.status = "closed"
#         await self._voting_repo.vote_sessions.close_vote_session(vote_session.id)
#
#     async def process_build_vote_session_result(self, vote_session: BuildVoteSession) -> None:
#         """Process the result of a build vote session."""
#         build = await Build.from_id(vote_session.build_id)
#         if build is None:
#             return
#
#         if vote_session.net_votes >= vote_session.pass_threshold:
#             await build.confirm()
#         else:
#             await build.deny()
#
#     async def process_delete_log_vote_session_result(self, vote_session: DeleteLogVoteSession) -> None:
#         """Process the result of a delete log vote session."""
#         if vote_session.net_votes >= vote_session.pass_threshold:
#             # Fetch and delete the target message
#             try:
#                 channel = self.bot.get_channel(vote_session.target_channel_id)
#                 if channel and isinstance(channel, discord.abc.Messageable):
#                     message = await channel.fetch_message(vote_session.target_message_id)
#                     await message.delete()
#             except (discord.NotFound, discord.Forbidden):
#                 pass  # Message already deleted or no permission
#
#     async def _track_messages_for_vote_session(
#         self,
#         messages: list[discord.Message],
#         session_id: int,
#         build_id: int | None = None,
#     ) -> None:
#         """Track messages for a vote session."""
#         tasks = [
#             self._voting_repo.vote_sessions.track_message_for_vote_session(message, session_id, build_id)
#             for message in messages
#         ]
#         await asyncio.gather(*tasks)
#
#     async def _add_voting_reactions(self, messages: list[discord.Message]) -> None:
#         """Add voting reactions to messages."""
#         APPROVE_EMOJIS = ["👍", "✅"]
#         DENY_EMOJIS = ["👎", "❌"]
#
#         reaction_tasks = []
#         for message in messages:
#             reaction_tasks.append(message.add_reaction(APPROVE_EMOJIS[0]))
#             reaction_tasks.append(message.add_reaction(DENY_EMOJIS[0]))
#
#         try:
#             await asyncio.gather(*reaction_tasks)
#         except discord.Forbidden:
#             pass  # Bot doesn't have permission to add reactions
