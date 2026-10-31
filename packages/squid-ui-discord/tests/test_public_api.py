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


def test_the_adapter_namespace_exposes_its_surface() -> None:
    """What `squid_ui_discord` promotes to its root, and what it deliberately does not."""
    assert squid_ui_discord.MessageRoot
    assert squid_ui_discord.owner_message_root
    assert squid_ui_discord.button_grid
    assert squid_ui_discord.modals.CheckboxGroupField
    assert squid_ui_discord.MessageRootDefaults
    assert squid_ui_discord.SessionManager
    assert squid_ui_discord.routing.routers
    assert squid_ui_discord.renderer.V2Renderer
    assert squid_ui_discord.classic_renderer.ClassicRenderer
    assert squid_ui_discord.classic.render_message
    assert squid_ui_discord.SessionKey
    assert squid_ui_discord.sessions.AdmissionSpec
    assert squid_ui_discord.SessionSpec
    assert squid_ui_discord.StackNavigator
    assert squid_ui_discord.OpenContext
    assert squid_ui_discord.ScopeKind
    assert squid_ui_discord.SessionOptionsResolver
    assert squid_ui_discord.Invocation
    assert squid_ui_discord.Private
    assert squid_ui_discord.Visibility
    assert squid_ui_discord.LocalizationResolver
    assert squid_ui_discord.current_invocation
    assert squid_ui_discord.invocation_scope
    assert squid_ui_discord.Screen
    assert squid_ui_discord.message_payload.MessagePayload
    assert squid_ui_discord.MessageMode.COMPONENTS_V2
    assert squid_ui_discord.MessageModeError
    assert squid_ui_discord.message_mode
    assert squid_ui_discord.message_payload.MessagePayload
    assert not hasattr(squid_ui_discord, "MountRegistry")
    assert not hasattr(squid_ui_discord, "WhenOpen")
    assert squid_ui_discord.guards.requires_role
    assert squid_ui_discord.durability.DurableSessionRuntime
    assert squid_ui_discord.durability.DurableBot
    assert squid_ui_discord.durability.DiscordFrontend
    assert not hasattr(squid_ui_discord.durability, "MountManager")
    assert squid_ui_discord.MessageRootScheduler.follow
    for removed in ("AdmissionSpec", "Router", "V2Renderer", "ClassicRenderer", "AuditReport"):
        assert removed not in squid_ui_discord.__all__ and not hasattr(squid_ui_discord, removed)


def test_rejection_notices_accept_public_deferred_text() -> None:
    notice = Message("This screen is already open.")

    assert Reject(notice=notice).notice is notice


def test_testing_helpers_are_a_declared_namespace_not_an_accident() -> None:
    """A consumer's tests import these, so they are versioned surface, not a private module."""
    from types import ModuleType

    assert "testing" in squid_ui_discord.__all__
    assert isinstance(squid_ui_discord.testing, ModuleType)
    assert {"fake_interaction", "delivered_to", "commit_render", "assert_within_limits"} <= set(
        squid_ui_discord.testing.__all__
    )
    assert [name for name in squid_ui_discord.testing.__all__ if not hasattr(squid_ui_discord.testing, name)] == []
    # The doubles stay one tier down; nothing here belongs beside MessageRoot and Screen.
    for name in squid_ui_discord.testing.__all__:
        assert name not in squid_ui_discord.__all__


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
