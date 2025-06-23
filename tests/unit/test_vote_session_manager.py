"""Tests for the VoteSessionManager."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from squid.db.vote_session import VoteSessionManager, VoteSessionRecord, VoteRecord


class TestVoteSessionManager:
    """Test cases for VoteSessionManager."""

    @pytest.fixture
    def mock_session(self):
        """Create a mock async session."""
        return AsyncMock()

    @pytest.fixture
    def mock_session_maker(self, mock_session):
        """Create a mock session maker."""
        session_maker = MagicMock()
        session_maker.return_value.__aenter__.return_value = mock_session
        session_maker.return_value.__aexit__.return_value = None
        return session_maker

    @pytest.fixture
    def vote_session_manager(self, mock_session_maker):
        """Create a VoteSessionManager instance."""
        return VoteSessionManager(mock_session_maker)

    @pytest.mark.asyncio
    async def test_create_vote_session(self, vote_session_manager, mock_session):
        """Test creating a vote session."""
        # Mock the database response
        mock_vote_session = MagicMock()
        mock_vote_session.id = 123
        mock_session.execute.return_value.scalar_one.return_value = mock_vote_session

        # Call the method
        result = await vote_session_manager.create_vote_session(
            author_id=456,
            kind="build",
            pass_threshold=3,
            fail_threshold=-3,
            build_id=789,
        )

        # Verify the result
        assert result == 123

        # Verify the database was called correctly
        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_vote_session_by_id_found(self, vote_session_manager, mock_session):
        """Test getting a vote session by ID when it exists."""
        # Mock the database response
        mock_vote_session = MagicMock()
        mock_vote_session.id = 123
        mock_vote_session.status = "open"
        mock_vote_session.author_id = 456
        mock_vote_session.kind = "build"
        mock_vote_session.pass_threshold = 3
        mock_vote_session.fail_threshold = -3
        mock_vote_session.created_at = "2024-01-01T00:00:00Z"
        
        mock_session.execute.return_value.scalar_one_or_none.return_value = mock_vote_session

        # Call the method
        result = await vote_session_manager.get_vote_session_by_id(123)

        # Verify the result
        assert result is not None
        assert result["id"] == 123
        assert result["status"] == "open"
        assert result["author_id"] == 456
        assert result["kind"] == "build"
        assert result["pass_threshold"] == 3
        assert result["fail_threshold"] == -3
        assert result["created_at"] == "2024-01-01T00:00:00Z"

    @pytest.mark.asyncio
    async def test_get_vote_session_by_id_not_found(self, vote_session_manager, mock_session):
        """Test getting a vote session by ID when it doesn't exist."""
        # Mock the database response
        mock_session.execute.return_value.scalar_one_or_none.return_value = None

        # Call the method
        result = await vote_session_manager.get_vote_session_by_id(999)

        # Verify the result
        assert result is None

    @pytest.mark.asyncio
    async def test_upsert_vote_insert(self, vote_session_manager, mock_session):
        """Test upserting a vote (insert case)."""
        # Call the method
        await vote_session_manager.upsert_vote(123, 456, 1.0)

        # Verify the database was called correctly
        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_upsert_vote_remove(self, vote_session_manager, mock_session):
        """Test upserting a vote (remove case)."""
        # Call the method
        await vote_session_manager.upsert_vote(123, 456, None)

        # Verify the database was called correctly
        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_votes(self, vote_session_manager, mock_session):
        """Test getting votes for a vote session."""
        # Mock the database response
        mock_vote1 = MagicMock()
        mock_vote1.vote_session_id = 123
        mock_vote1.user_id = 456
        mock_vote1.weight = 1.0

        mock_vote2 = MagicMock()
        mock_vote2.vote_session_id = 123
        mock_vote2.user_id = 789
        mock_vote2.weight = -1.0

        mock_session.execute.return_value.scalars.return_value.all.return_value = [mock_vote1, mock_vote2]

        # Call the method
        result = await vote_session_manager.get_votes(123)

        # Verify the result
        assert len(result) == 2
        assert result[0]["vote_session_id"] == 123
        assert result[0]["user_id"] == 456
        assert result[0]["weight"] == 1.0
        assert result[1]["vote_session_id"] == 123
        assert result[1]["user_id"] == 789
        assert result[1]["weight"] == -1.0

    @pytest.mark.asyncio
    async def test_get_vote_summary(self, vote_session_manager):
        """Test getting vote summary."""
        # Mock the get_votes method
        vote_session_manager.get_votes = AsyncMock(return_value=[
            VoteRecord(vote_session_id=123, user_id=456, weight=1.0),
            VoteRecord(vote_session_id=123, user_id=789, weight=-1.0),
            VoteRecord(vote_session_id=123, user_id=101, weight=2.0),
        ])

        # Call the method
        upvotes, downvotes, net_votes = await vote_session_manager.get_vote_summary(123)

        # Verify the result
        assert upvotes == 3.0  # 1.0 + 2.0
        assert downvotes == 1.0  # abs(-1.0)
        assert net_votes == 2.0  # 1.0 + (-1.0) + 2.0

    @pytest.mark.asyncio
    async def test_close_vote_session(self, vote_session_manager, mock_session):
        """Test closing a vote session."""
        # Call the method
        await vote_session_manager.close_vote_session(123)

        # Verify the database was called correctly
        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once() 