"""Decoupled vote session implementation using domain models and service layer.

This module provides Discord bot-specific vote session classes that use the voting service
instead of directly accessing the database, following proper separation of concerns.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Literal, final, override
from textwrap import dedent

import discord

from squid.db.builds import Build
from squid.db.voting import BuildVoteSession as DomainBuildVoteSession
from squid.db.voting import DeleteLogVoteSession as DomainDeleteLogVoteSession
from squid.bot.voting.voting_service import VotingService

if TYPE_CHECKING:
    from squid.bot import RedstoneSquid


APPROVE_EMOJIS = ["👍", "✅"]
DENY_EMOJIS = ["👎", "❌"]


# AIDEV-NOTE: Discord-specific vote session wrappers using service layer
class AbstractDiscordVoteSession(ABC):
    """Abstract base class for Discord-specific vote sessions."""
    
    def __init__(
        self,
        bot: RedstoneSquid,
        voting_service: VotingService,
        domain_session: DomainBuildVoteSession | DomainDeleteLogVoteSession,
    ):
        self.bot = bot
        self._voting_service = voting_service
        self._domain_session = domain_session
        self._messages: set[discord.Message] = set()
    
    @property
    def id(self) -> int | None:
        """Get the vote session ID."""
        return self._domain_session.id
    
    @property
    def is_closed(self) -> bool:
        """Check if the vote session is closed."""
        return self._domain_session.is_closed
    
    @property
    def upvotes(self) -> float:
        """Get total upvotes."""
        return self._domain_session.upvotes
    
    @property
    def downvotes(self) -> float:
        """Get total downvotes."""
        return self._domain_session.downvotes
    
    @property
    def net_votes(self) -> float:
        """Get net votes."""
        return self._domain_session.net_votes
    
    @property
    def pass_threshold(self) -> int:
        """Get pass threshold."""
        return self._domain_session.pass_threshold
    
    @property
    def fail_threshold(self) -> int:
        """Get fail threshold."""
        return self._domain_session.fail_threshold
    
    @property
    def message_ids(self) -> set[int]:
        """Get message IDs associated with this vote session."""
        return self._domain_session.message_ids
    
    async def cast_vote(self, user_id: int, weight: float | None) -> None:
        """Cast a vote in this session."""
        await self._voting_service.cast_vote(self._domain_session, user_id, weight)
    
    async def close(self) -> None:
        """Close the vote session and process results."""
        if self.is_closed:
            return
        
        await self._voting_service.close_vote_session(self._domain_session)
        await self._process_result()
    
    async def fetch_messages(self) -> set[discord.Message]:
        """Fetch all Discord messages associated with this vote session."""
        if not self._messages:
            messages = set()
            for message_id in self.message_ids:
                # Try to find the message in cached channels
                for guild in self.bot.guilds:
                    for channel in guild.text_channels:
                        try:
                            message = await channel.fetch_message(message_id)
                            messages.add(message)
                            break
                        except discord.NotFound:
                            continue
                        except discord.Forbidden:
                            continue
            self._messages = messages
        return self._messages
    
    @abstractmethod
    async def send_message(self, channel: discord.abc.Messageable) -> discord.Message:
        """Send the initial vote message to a channel."""
        ...
    
    @abstractmethod
    async def update_messages(self) -> None:
        """Update all messages with current vote counts."""
        ...
    
    @abstractmethod
    async def _process_result(self) -> None:
        """Process the result of the vote session."""
        ...


@final
class DiscordBuildVoteSession(AbstractDiscordVoteSession):
    """Discord-specific build vote session."""
    
    def __init__(
        self,
        bot: RedstoneSquid,
        voting_service: VotingService,
        domain_session: DomainBuildVoteSession,
        build: Build,
    ):
        super().__init__(bot, voting_service, domain_session)
        self.build = build
    
    @classmethod
    async def create(
        cls,
        bot: RedstoneSquid,
        voting_service: VotingService,
        messages: list[discord.Message],
        author_id: int,
        build: Build,
        vote_type: Literal["add", "update"],
        pass_threshold: int = 3,
        fail_threshold: int = -3,
    ) -> DiscordBuildVoteSession:
        """Create a new build vote session."""
        domain_session = await voting_service.create_build_vote_session(
            messages=messages,
            author_id=author_id,
            build=build,
            vote_type=vote_type,
            pass_threshold=pass_threshold,
            fail_threshold=fail_threshold,
        )
        
        session = cls(bot, voting_service, domain_session, build)
        session._messages = set(messages)
        await session.update_messages()
        return session
    
    @classmethod
    async def from_domain(
        cls,
        bot: RedstoneSquid,
        voting_service: VotingService,
        domain_session: DomainBuildVoteSession,
    ) -> DiscordBuildVoteSession | None:
        """Create a DiscordBuildVoteSession from a domain model."""
        build = await Build.from_id(domain_session.build_id)
        if build is None:
            return None
        
        return cls(bot, voting_service, domain_session, build)
    
    @override
    async def send_message(self, channel: discord.abc.Messageable) -> discord.Message:
        """Send the initial build vote message."""
        embed = await self.bot.for_build(self.build).generate_embed()
        message = await channel.send(
            content=self.build.original_link,
            embed=embed,
        )
        
        # Track the new message
        await self._voting_service._track_messages_for_vote_session(
            [message], self.id, self.build.id
        )
        
        # Add to our local cache
        self._messages.add(message)
        
        return message
    
    @override
    async def update_messages(self) -> None:
        """Update build vote messages with current vote counts."""
        embed = await self.bot.for_build(self.build).generate_embed()
        embed.add_field(name="", value="", inline=False)  # Separator
        embed.add_field(name="Accept", value=f"{self.upvotes}/{self.pass_threshold}", inline=True)
        embed.add_field(name="Deny", value=f"{self.downvotes}/{-self.fail_threshold}", inline=True)
        
        messages = await self.fetch_messages()
        await asyncio.gather(*[
            message.edit(content=self.build.original_link, embed=embed)
            for message in messages
        ])
    
    @override
    async def _process_result(self) -> None:
        """Process the build vote result."""
        await self._voting_service.process_build_vote_session_result(
            self._domain_session  # type: ignore
        )


@final
class DiscordDeleteLogVoteSession(AbstractDiscordVoteSession):
    """Discord-specific delete log vote session."""
    
    def __init__(
        self,
        bot: RedstoneSquid,
        voting_service: VotingService,
        domain_session: DomainDeleteLogVoteSession,
        target_message: discord.Message,
    ):
        super().__init__(bot, voting_service, domain_session)
        self.target_message = target_message
    
    @classmethod
    async def create(
        cls,
        bot: RedstoneSquid,
        voting_service: VotingService,
        messages: list[discord.Message],
        author_id: int,
        target_message: discord.Message,
        pass_threshold: int = 3,
        fail_threshold: int = -3,
    ) -> DiscordDeleteLogVoteSession:
        """Create a new delete log vote session."""
        domain_session = await voting_service.create_delete_log_vote_session(
            messages=messages,
            author_id=author_id,
            target_message=target_message,
            pass_threshold=pass_threshold,
            fail_threshold=fail_threshold,
        )
        
        session = cls(bot, voting_service, domain_session, target_message)
        session._messages = set(messages)
        await session.update_messages()
        return session
    
    @classmethod
    async def from_domain(
        cls,
        bot: RedstoneSquid,
        voting_service: VotingService,
        domain_session: DomainDeleteLogVoteSession,
    ) -> DiscordDeleteLogVoteSession | None:
        """Create a DiscordDeleteLogVoteSession from a domain model."""
        # Fetch the target message
        try:
            channel = bot.get_channel(domain_session.target_channel_id)
            if channel and isinstance(channel, discord.abc.Messageable):
                target_message = await channel.fetch_message(domain_session.target_message_id)
                return cls(bot, voting_service, domain_session, target_message)
        except (discord.NotFound, discord.Forbidden):
            pass
        
        return None
    
    @override
    async def send_message(self, channel: discord.abc.Messageable) -> discord.Message:
        """Send the initial delete log vote message."""
        embed = discord.Embed(
            title="Vote to Delete Log",
            description=dedent(f"""
                React with {APPROVE_EMOJIS[0]} to upvote or {DENY_EMOJIS[0]} to downvote.

                **Log Content:**
                {self.target_message.content}

                **Upvotes:** {self.upvotes}
                **Downvotes:** {self.downvotes}
                **Net Votes:** {self.net_votes}
            """),
        )
        
        message = await channel.send(embed=embed)
        
        # Track the new message
        await self._voting_service._track_messages_for_vote_session(
            [message], self.id
        )
        
        # Add to our local cache
        self._messages.add(message)
        
        return message
    
    @override
    async def update_messages(self) -> None:
        """Update delete log vote messages with current vote counts."""
        embed = discord.Embed(
            title="Vote to Delete Log",
            description=dedent(f"""
                React with {APPROVE_EMOJIS[0]} to upvote or {DENY_EMOJIS[0]} to downvote.

                **Log Content:**
                {self.target_message.content}

                **Upvotes:** {self.upvotes}
                **Downvotes:** {self.downvotes}
                **Net Votes:** {self.net_votes}
            """),
        )
        
        messages = await self.fetch_messages()
        await asyncio.gather(*[
            message.edit(embed=embed)
            for message in messages
        ])
    
    @override
    async def _process_result(self) -> None:
        """Process the delete log vote result."""
        await self._voting_service.process_delete_log_vote_session_result(
            self._domain_session  # type: ignore
        ) 