"""Integration test for pg_only_db_manager fixture."""

import pytest
from sqlalchemy import text

from squid.db import DatabaseManager


async def test_pg_only_db_manager_connection(pg_only_db_manager: DatabaseManager):
    """Test that pg_only_db_manager fixture provides a working database connection."""
    # Test basic database connection with a simple query
    async with pg_only_db_manager.async_session() as session:
        # Simple query to verify connection works
        result = await session.execute(text("SELECT 1 as test_value"))
        row = result.fetchone()
        assert row is not None
        assert row[0] == 1

    # Test that we can query actual tables created by migrations
    async with pg_only_db_manager.async_session() as session:
        # Check that the users table exists (created by migrations)
        result = await session.execute(text("SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'users'"))
        row = result.fetchone()
        assert row is not None
        assert row[0] == 1, "Users table should exist after applying migrations"

    # Test that we can query the builds table structure
    async with pg_only_db_manager.async_session() as session:
        # Check that the builds table exists with expected columns
        result = await session.execute(text("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'builds' 
            ORDER BY column_name
        """))
        columns = [row[0] for row in result.fetchall()]
        
        # Verify some expected columns exist
        expected_columns = ['id', 'name', 'description', 'submitter_id', 'created_at']
        for col in expected_columns:
            assert col in columns, f"Column '{col}' should exist in builds table"


async def test_pg_only_db_manager_crud_operations(pg_only_db_manager: DatabaseManager):
    """Test basic CRUD operations work with pg_only_db_manager."""
    # Test inserting and querying data
    async with pg_only_db_manager.async_session() as session:
        # Insert a test user
        await session.execute(text("""
            INSERT INTO users (discord_id, username, is_verified, created_at) 
            VALUES (123456789, 'test_user', true, NOW())
        """))
        await session.commit()

        # Query the inserted user
        result = await session.execute(text("SELECT username FROM users WHERE discord_id = 123456789"))
        row = result.fetchone()
        assert row is not None
        assert row[0] == 'test_user'

        # Clean up
        await session.execute(text("DELETE FROM users WHERE discord_id = 123456789"))
        await session.commit() 