"""Voting service layer to bridge domain models with Discord bot logic.

This service layer handles the coordination between Discord entities and the voting domain models,
reducing the coupling between bot logic and database operations.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Literal

import discord

from squid.db.builds import Build
from squid.db.voting import BuildVoteSession, DeleteLogVoteSession, VoteSession, VotingRepository

if TYPE_CHECKING:
    from squid.bot import RedstoneSquid


# AIDEV-NOTE: Service layer for voting operations - bridges Discord and domain models
class VotingService:
    """Service for voting operations that bridges Discord bot logic with domain models."""
    
    def __init__(self, bot: RedstoneSquid, voting_repo: VotingRepository):
        self.bot = bot
        self._voting_repo = voting_repo
    
    async def create_build_vote_session(
        self,
        messages: list[discord.Message],
        author_id: int,
        build: Build,
        vote_type: Literal["add", "update"],
        pass_threshold: int = 3,
        fail_threshold: int = -3,
    ) -> BuildVoteSession:
        """Create a new build vote session."""
        # Determine changes for the vote session
        if vote_type == "add":
            changes = [("submission_status", "PENDING", "CONFIRMED")]
        else:
            assert build.id is not None
            original = await Build.from_id(build.id)
            assert original is not None
            changes = original.diff(build)
        
        # Create the vote session
        session_id = await self._voting_repo.vote_sessions.create_vote_session(
            author_id=author_id,
            kind="build",
            pass_threshold=pass_threshold,
            fail_threshold=fail_threshold,
            message_ids=[msg.id for msg in messages],
            build_id=build.id,
        )
        
        # Create the build-specific vote session data
        assert build.id is not None
        await self._voting_repo.build_vote_sessions.create_build_vote_session(
            vote_session_id=session_id,
            build_id=build.id,
            changes=changes,
        )
        
        # Track messages for the vote session
        await self._track_messages_for_vote_session(messages, session_id, build.id)
        
        # Add reactions to messages
        await self._add_voting_reactions(messages)
        
        # Return the domain model
        return BuildVoteSession(
            id=session_id,
            author_id=author_id,
            pass_threshold=pass_threshold,
            fail_threshold=fail_threshold,
            build_id=build.id,
            changes=changes,
            message_ids={msg.id for msg in messages},
        )
    
    async def create_delete_log_vote_session(
        self,
        messages: list[discord.Message],
        author_id: int,
        target_message: discord.Message,
        pass_threshold: int = 3,
        fail_threshold: int = -3,
    ) -> DeleteLogVoteSession:
        """Create a new delete log vote session."""
        # Create the vote session
        session_id = await self._voting_repo.vote_sessions.create_vote_session(
            author_id=author_id,
            kind="delete_log",
            pass_threshold=pass_threshold,
            fail_threshold=fail_threshold,
            message_ids=[msg.id for msg in messages],
        )
        
        # Create the delete log specific vote session data
        await self._voting_repo.delete_log_vote_sessions.create_delete_log_vote_session(
            vote_session_id=session_id,
            target_message_id=target_message.id,
            target_channel_id=target_message.channel.id,
            target_server_id=target_message.guild.id if target_message.guild else 0,
        )
        
        # Track messages for the vote session
        await self._track_messages_for_vote_session(messages, session_id)
        
        # Add reactions to messages
        await self._add_voting_reactions(messages)
        
        # Return the domain model
        return DeleteLogVoteSession(
            id=session_id,
            author_id=author_id,
            pass_threshold=pass_threshold,
            fail_threshold=fail_threshold,
            target_message_id=target_message.id,
            target_channel_id=target_message.channel.id,
            target_server_id=target_message.guild.id if target_message.guild else 0,
            message_ids={msg.id for msg in messages},
        )
    
    async def get_vote_session_by_message_id(self, message_id: int) -> VoteSession | None:
        """Get a vote session by message ID."""
        return await self._voting_repo.vote_sessions.get_vote_session_by_message_id(message_id)
    
    async def get_build_vote_session_by_id(self, session_id: int) -> BuildVoteSession | None:
        """Get a build vote session by its ID."""
        return await self._voting_repo.build_vote_sessions.get_build_vote_session(session_id)
    
    async def get_delete_log_vote_session_by_id(self, session_id: int) -> DeleteLogVoteSession | None:
        """Get a delete log vote session by its ID."""
        return await self._voting_repo.delete_log_vote_sessions.get_delete_log_vote_session(session_id)
    
    async def get_open_build_vote_sessions(self) -> list[BuildVoteSession]:
        """Get all open build vote sessions."""
        # Get base vote sessions
        base_sessions = await self._voting_repo.vote_sessions.get_open_vote_sessions_by_kind("build")
        
        # Convert to build vote sessions
        build_sessions = []
        for session in base_sessions:
            if session.id is not None:
                build_session = await self.get_build_vote_session_by_id(session.id)
                if build_session:
                    build_sessions.append(build_session)
        
        return build_sessions
    
    async def get_open_delete_log_vote_sessions(self) -> list[DeleteLogVoteSession]:
        """Get all open delete log vote sessions."""
        # Get base vote sessions
        base_sessions = await self._voting_repo.vote_sessions.get_open_vote_sessions_by_kind("delete_log")
        
        # Convert to delete log vote sessions
        delete_log_sessions = []
        for session in base_sessions:
            if session.id is not None:
                delete_session = await self.get_delete_log_vote_session_by_id(session.id)
                if delete_session:
                    delete_log_sessions.append(delete_session)
        
        return delete_log_sessions
    
    async def cast_vote(self, vote_session: VoteSession, user_id: int, weight: float | None) -> None:
        """Cast a vote in a vote session."""
        if vote_session.id is None:
            raise ValueError("Cannot cast vote in untracked vote session")
        
        # Update the domain model
        if weight is None:
            vote_session.votes.pop(user_id, None)
        else:
            vote_session.votes[user_id] = weight
        
        # Persist to database
        await self._voting_repo.votes.upsert_vote(vote_session.id, user_id, weight)
    
    async def close_vote_session(self, vote_session: VoteSession) -> None:
        """Close a vote session."""
        if vote_session.id is None:
            raise ValueError("Cannot close untracked vote session")
        
        vote_session.status = "closed"
        await self._voting_repo.vote_sessions.close_vote_session(vote_session.id)
    
    async def process_build_vote_session_result(self, vote_session: BuildVoteSession) -> None:
        """Process the result of a build vote session."""
        build = await Build.from_id(vote_session.build_id)
        if build is None:
            return
        
        if vote_session.net_votes >= vote_session.pass_threshold:
            await build.confirm()
        else:
            await build.deny()
    
    async def process_delete_log_vote_session_result(self, vote_session: DeleteLogVoteSession) -> None:
        """Process the result of a delete log vote session."""
        if vote_session.net_votes >= vote_session.pass_threshold:
            # Fetch and delete the target message
            try:
                channel = self.bot.get_channel(vote_session.target_channel_id)
                if channel and isinstance(channel, discord.abc.Messageable):
                    message = await channel.fetch_message(vote_session.target_message_id)
                    await message.delete()
            except (discord.NotFound, discord.Forbidden):
                pass  # Message already deleted or no permission
    
    async def _track_messages_for_vote_session(
        self,
        messages: list[discord.Message],
        session_id: int,
        build_id: int | None = None,
    ) -> None:
        """Track messages for a vote session."""
        tasks = [
            self._voting_repo.vote_sessions.track_message_for_vote_session(
                message, session_id, build_id
            )
            for message in messages
        ]
        await asyncio.gather(*tasks)
    
    async def _add_voting_reactions(self, messages: list[discord.Message]) -> None:
        """Add voting reactions to messages."""
        APPROVE_EMOJIS = ["👍", "✅"]
        DENY_EMOJIS = ["👎", "❌"]
        
        reaction_tasks = []
        for message in messages:
            reaction_tasks.append(message.add_reaction(APPROVE_EMOJIS[0]))
            reaction_tasks.append(message.add_reaction(DENY_EMOJIS[0]))
        
        try:
            await asyncio.gather(*reaction_tasks)
        except discord.Forbidden:
            pass  # Bot doesn't have permission to add reactions 