"""Shared database fixtures for build integration tests.

`migrated_session_factory` lives in the parent conftest: several contexts need a database
at Alembic head, so it is defined once there rather than per directory.
"""
