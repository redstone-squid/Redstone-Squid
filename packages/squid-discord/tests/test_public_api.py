"""Public namespace and optional-extra packaging contracts for the Discord adapter."""

import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

import squid_discord

RENAMED_SUBMODULES = (
    "squid_discord.composition",
    "squid_discord.conformance",
)


@pytest.mark.parametrize("dotted", RENAMED_SUBMODULES)
def test_renamed_submodules_are_modules_not_shadowed_callables(dotted: str) -> None:
    import importlib

    mod = importlib.import_module(dotted)
    assert isinstance(mod, ModuleType)


def test_the_adapter_namespace_exposes_its_surface() -> None:
    """What `squid_discord` promotes to its root, and what it deliberately does not."""
    assert squid_discord.Mount
    assert squid_discord.button_grid
    assert squid_discord.modals.CheckboxGroupField
    assert squid_discord.MountDefaults
    assert squid_discord.SessionRegistry
    assert squid_discord.routing.routers
    assert squid_discord.renderer.V2Renderer
    assert squid_discord.classic_renderer.ClassicRenderer
    assert squid_discord.classic.compose
    assert squid_discord.SessionKey
    assert squid_discord.sessions.SessionPolicy
    assert squid_discord.ScreenSpec
    assert squid_discord.screens.Scope
    assert squid_discord.screens.Opener
    assert squid_discord.presentation.DiscordPresentation
    assert squid_discord.DiscordMode.COMPONENTS_V2
    assert squid_discord.DiscordModeError
    assert squid_discord.mode_of
    assert squid_discord.presentation.DiscordPresentation
    assert not hasattr(squid_discord, "MountRegistry")
    assert not hasattr(squid_discord, "WhenOpen")
    assert squid_discord.guards.requires_role
    assert squid_discord.durability.DurableSessionRuntime
    assert squid_discord.durability.DurableBot
    assert squid_discord.durability.DiscordFrontend
    assert not hasattr(squid_discord.durability, "MountManager")
    assert squid_discord.MountScheduler.follow
    for removed in ("SessionPolicy", "Opener", "Scope", "Router", "V2Renderer", "ClassicRenderer", "AuditReport"):
        assert removed not in squid_discord.__all__ and not hasattr(squid_discord, removed)


def test_testing_helpers_are_a_declared_namespace_not_an_accident() -> None:
    """A consumer's tests import these, so they are versioned surface, not a private module."""
    from types import ModuleType

    assert "testing" in squid_discord.__all__
    assert isinstance(squid_discord.testing, ModuleType)
    assert {"fake_interaction", "delivered_to", "commit_render", "assert_within_limits"} <= set(
        squid_discord.testing.__all__
    )
    assert [name for name in squid_discord.testing.__all__ if not hasattr(squid_discord.testing, name)] == []
    # The doubles stay one tier down; nothing here belongs beside Mount and Screen.
    for name in squid_discord.testing.__all__:
        assert name not in squid_discord.__all__


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
from squid_discord.durability import PostgresSessionStore, SQLiteSessionStore
assert PostgresSessionStore
assert SQLiteSessionStore
assert "asyncpg" not in sys.modules
"""
    subprocess.run([sys.executable, "-c", code], check=True)


def test_base_install_needs_no_store_backend() -> None:
    """The `durable` extra is real, not decorative.

    `durability` is behind a lazy `__getattr__`, and `operations`, `devtools` and
    `devtools_view` name its types under `TYPE_CHECKING`. Miss any one of those and importing
    this package reaches `squid_stores` again, which nothing else here would notice.
    """
    code = """
import importlib.abc
import sys

class BlockStores(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".", 1)[0] in {"squid_stores", "asyncpg"}:
            raise ModuleNotFoundError(fullname)
        return None

sys.meta_path.insert(0, BlockStores())
import squid_discord
assert squid_discord.Mount
assert squid_discord.ScreenSpec
assert "durability" in squid_discord.__all__
assert "squid_stores" not in sys.modules
"""
    subprocess.run([sys.executable, "-c", code], check=True)


def test_package_metadata_keeps_its_base_and_optional_dependencies() -> None:
    metadata = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    project = metadata["project"]
    assert project["version"] == "0.1.0"
    assert project["dependencies"] == [
        "squid-layouts",
        "discord-py>=2.7,<3",
        "anyio>=4.14,<5",
        "packaging>=24,<27",
    ]
    assert project["optional-dependencies"]["durable"] == ["squid-stores"]
    assert project["optional-dependencies"]["postgres"] == ["squid-stores[postgres]"]
