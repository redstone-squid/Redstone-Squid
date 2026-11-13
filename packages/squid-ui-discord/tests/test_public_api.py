"""Public namespace and optional-extra packaging contracts for the Discord adapter."""

import subprocess
import sys

import squid_ui_discord


def test_testing_helpers_are_a_declared_namespace_not_an_accident() -> None:
    """A consumer can import and drive the versioned testing namespace."""
    harness = squid_ui_discord.testing.interaction_harness(user_id=7)

    assert harness.source.user.id == 7
    assert squid_ui_discord.testing.message_harness().source.id == 99


def test_durability_imports_without_postgres_dependency() -> None:
    code = """
import importlib.abc
import sys

class BlockAsyncpg(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".", 1)[0] == "asyncpg":
            raise ModuleNotFoundError(fullname)
        return None

sys.meta_path.insert(0, BlockAsyncpg())
from squid_ui_discord.durability import PostgresSessionStore, SQLiteSessionStore
assert PostgresSessionStore
assert SQLiteSessionStore
assert "asyncpg" not in sys.modules
"""
    subprocess.run([sys.executable, "-c", code], check=True)


def test_base_install_needs_no_store_backend() -> None:
    """Import the base adapter with both storage backends unavailable.

    Durability is exposed lazily, and modules that need only its types import them under
    TYPE_CHECKING. Blocking the backend imports verifies that these boundaries hold.
    """
    code = """
import importlib.abc
import sys

class BlockStores(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".", 1)[0] in {"squid_storage", "asyncpg"}:
            raise ModuleNotFoundError(fullname)
        return None

sys.meta_path.insert(0, BlockStores())
import squid_ui_discord
assert squid_ui_discord.MessageRoot
assert squid_ui_discord.SessionSpec
assert "squid_storage" not in sys.modules
"""
    subprocess.run([sys.executable, "-c", code], check=True)
