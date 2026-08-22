"""Public namespace and optional-adapter packaging contracts."""

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import squid_layouts as sl


def test_root_is_semantic_first() -> None:
    assert {"Section", "Paragraph", "Note", "Actions", "Component", "plan", "PlanReport"} <= set(sl.__all__)
    assert {"section", "paragraph", "note", "actions", "action", "ChildLike"} <= set(sl.__all__)
    assert {"budget", "paged", "unbreakable", "keep_with_next"} <= set(sl.__all__)
    assert {
        "CountPrecision",
        "Direction",
        "LoadedWindow",
        "Position",
        "PositionPolicy",
        "SourceCapabilities",
        "Window",
        "WindowLoader",
        "WindowSource",
    } <= set(sl.__all__)
    for removed in (
        "Button",
        "Mount",
        "HtmlRenderer",
        "PresentationSession",
        "SceneCodec",
        "solve",
        "conform",
        "render_static",
    ):
        with pytest.raises(AttributeError):
            getattr(sl, removed)


def test_explicit_namespaces_expose_specialized_apis() -> None:
    assert sl.primitives.Button
    assert sl.planning.solve
    assert sl.runtime.ComponentRuntime
    assert sl.scene.Codec
    assert sl.html.Renderer
    assert sl.discord.Mount
    assert sl.discord.MountRegistry
    assert sl.discord.routers
    assert sl.discord.Renderer
    assert sl.discord.SessionKey
    assert sl.discord.WhenOpen
    assert sl.discord.durability.MountManager


def test_core_and_html_import_without_discord_dependencies() -> None:
    code = """
import importlib.abc
import sys

class BlockAdapterDependencies(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".", 1)[0] in {"discord", "anyio"}:
            raise ModuleNotFoundError(fullname)
        return None

sys.meta_path.insert(0, BlockAdapterDependencies())
import squid_layouts
import squid_layouts.html
import squid_layouts.planning
import squid_layouts.runtime
assert "discord" not in sys.modules
assert "anyio" not in sys.modules
"""
    subprocess.run([sys.executable, "-c", code], check=True)


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
from squid_layouts.discord.durability import PostgresSnapshotStore, SQLiteSnapshotStore
assert PostgresSnapshotStore
assert SQLiteSnapshotStore
assert "asyncpg" not in sys.modules
"""
    subprocess.run([sys.executable, "-c", code], check=True)


def test_package_metadata_keeps_version_and_adapter_extra() -> None:
    metadata = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    project = metadata["project"]
    assert project["version"] == "0.1.0"
    assert project["dependencies"] == []
    assert set(project["optional-dependencies"]["discord"]) == {
        "discord-py>=2.7,<3",
        "anyio>=4.14,<5",
    }
