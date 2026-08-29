"""Public namespace and optional-extra packaging contracts for the Discord adapter."""

import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

import squid_ui_discord
from squid_ui.text import Message
from squid_ui_discord.sessions import Reject

RENAMED_SUBMODULES = (
    "squid_ui_discord.rendering",
    "squid_ui_discord.conformance",
)


@pytest.mark.parametrize("dotted", RENAMED_SUBMODULES)
def test_renamed_submodules_are_modules_not_shadowed_callables(dotted: str) -> None:
    import importlib

    mod = importlib.import_module(dotted)
    assert isinstance(mod, ModuleType)


def test_rejection_notices_accept_public_deferred_text() -> None:
    notice = Message("This screen is already open.")

    assert Reject(notice=notice).notice is notice


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
    """The `durable` extra is real, not decorative.

    `durability` is behind a lazy `__getattr__`, and `operations`, `devtools` and
    `devtools_view` name its types under `TYPE_CHECKING`. Miss any one of those and importing
    this package reaches `squid_storage` again, which nothing else here would notice.
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
assert "durability" in squid_ui_discord.__all__
assert "squid_storage" not in sys.modules
"""
    subprocess.run([sys.executable, "-c", code], check=True)


def test_package_metadata_keeps_its_base_and_optional_dependencies() -> None:
    metadata = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    project = metadata["project"]
    assert project["version"] == "0.1.0a1"
    assert project["dependencies"] == [
        "squid-ui==0.1.0a1",
        "squid-reactivity==0.1.0a1",
        "discord-py>=2.7,<3",
        "anyio>=4.14,<5",
        "packaging>=24,<27",
    ]
    assert project["optional-dependencies"]["durable"] == ["squid-storage==0.1.0a1"]
    assert project["optional-dependencies"]["postgres"] == ["squid-storage[postgres]==0.1.0a1"]
