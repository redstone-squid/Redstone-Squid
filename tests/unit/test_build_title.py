"""Tests for build title persistence functionality."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from squid.db.builds import Build
from squid.db.schema import Status, BuildCategory, RecordCategory


class TestBuildTitle:
    """Test cases for build title functionality."""

    def test_title_property_returns_stored_title(self):
        """Test that the title property returns the stored title when available."""
        build = Build()
        build._title = "Stored Title"
        
        assert build.title == "Stored Title"

    def test_title_property_generates_title_when_not_stored(self):
        """Test that the title property generates title when not stored."""
        build = Build(
            category=BuildCategory.DOOR,
            submission_status=Status.CONFIRMED,
            record_category=RecordCategory.SMALLEST,
            door_orientation_type="Door",
            door_width=2,
            door_height=3,
            component_restrictions=["Piston"],
            wiring_placement_restrictions=["Hidden"],
            door_type=["Regular"]
        )
        
        # Should generate title since _title is None
        title = build.title
        assert title is not None
        assert "Smallest" in title
        assert "Piston" in title
        assert "Hidden" in title
        assert "2x3" in title
        assert "Door" in title

    def test_refresh_title_updates_stored_title(self):
        """Test that refresh_title updates the stored title."""
        build = Build(
            category=BuildCategory.DOOR,
            submission_status=Status.CONFIRMED,
            record_category=RecordCategory.SMALLEST,
            door_orientation_type="Door",
            door_width=2,
            door_height=3,
            component_restrictions=["Piston"],
            wiring_placement_restrictions=["Hidden"],
            door_type=["Regular"]
        )
        
        # Initially no stored title
        assert build._title is None
        
        # Refresh title
        build.refresh_title()
        
        # Should now have stored title
        assert build._title is not None
        assert "Smallest" in build._title
        assert "Piston" in build._title
        assert "Hidden" in build._title
        assert "2x3" in build._title
        assert "Door" in build._title

    def test_title_setter_updates_stored_title(self):
        """Test that the title setter updates the stored title."""
        build = Build()
        
        # Set title
        build.title = "Custom Title"
        
        # Should update stored title
        assert build._title == "Custom Title"
        assert build.title == "Custom Title"

    def test_get_title_generates_correct_format(self):
        """Test that get_title generates the correct format."""
        build = Build(
            category=BuildCategory.DOOR,
            submission_status=Status.PENDING,
            record_category=RecordCategory.FASTEST,
            door_orientation_type="Trapdoor",
            door_width=4,
            door_height=2,
            component_restrictions=["Observer"],
            wiring_placement_restrictions=["Exposed"],
            door_type=["Regular"],
            ai_generated=True
        )
        
        title = build.get_title()
        
        # Check for expected components
        assert "Pending:" in title
        assert "🤖" in title  # Robot face emoji
        assert "Fastest" in title
        assert "Observer" in title
        assert "Exposed" in title
        assert "4x2" in title
        assert "Trapdoor" in title

    @pytest.mark.asyncio
    async def test_save_refreshes_title(self):
        """Test that save method refreshes the title."""
        build = Build(
            id=1,
            category=BuildCategory.DOOR,
            submission_status=Status.CONFIRMED,
            record_category=RecordCategory.SMALLEST,
            door_orientation_type="Door",
            door_width=2,
            door_height=3,
            component_restrictions=["Piston"],
            wiring_placement_restrictions=["Hidden"],
            door_type=["Regular"]
        )
        
        # Mock the lock
        build.lock = MagicMock()
        build.lock.acquire = AsyncMock(return_value=True)
        build.lock.release = AsyncMock()
        
        # Mock database operations
        with pytest.MonkeyPatch().context() as m:
            m.setattr("squid.db.builds.DatabaseManager", MagicMock())
            m.setattr("squid.db.builds.vecs", MagicMock())
            m.setattr("squid.db.builds.os.environ", {"DB_CONNECTION": "test"})
            m.setattr("squid.db.builds.os.getenv", lambda x, default=None: default)
            
            # Mock session and result
            mock_session = MagicMock()
            mock_result = MagicMock()
            mock_result.rowcount = 1
            mock_session.execute.return_value = mock_result
            mock_session.__aenter__.return_value = mock_session
            
            # Mock DatabaseManager
            mock_db = MagicMock()
            mock_db.async_session.return_value = mock_session
            m.setattr("squid.db.builds.DatabaseManager", lambda: mock_db)
            
            # Initially no stored title
            assert build._title is None
            
            # Save should refresh title
            await build.save()
            
            # Should now have stored title
            assert build._title is not None
            assert "Smallest" in build._title
            assert "Piston" in build._title
            assert "Hidden" in build._title
            assert "2x3" in build._title
            assert "Door" in build._title 