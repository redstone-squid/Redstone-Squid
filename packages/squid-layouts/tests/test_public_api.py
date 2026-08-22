"""Public namespace and optional-adapter packaging contracts."""

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import squid_layouts as sl


def test_root_is_semantic_first() -> None:
    assert {"Section", "Paragraph", "Note", "Actions", "Component", "plan", "PlanReport", "TopicBus"} <= set(sl.__all__)
    assert {"ActionKind", "ActionMiddleware", "ActionProceed", "ActionRequest"} <= set(sl.__all__)
    assert {"section", "paragraph", "note", "actions", "action", "ChildLike"} <= set(sl.__all__)
    assert {"budget", "paged", "unbreakable", "keep_with_next"} <= set(sl.__all__)
    assert {"Toggle", "ToggleEvent", "ToggleOwnership", "OFF", "toggle"} <= set(sl.__all__)
    assert {"MultiChoiceField", "UploadedFile"} <= set(sl.__all__)
    assert {
        "AmbiguousTimePolicy",
        "DateTimeField",
        "NonexistentTimePolicy",
        "TimeField",
        "Timestamp",
        "TimeStyle",
        "timestamp",
    } <= set(sl.__all__)
    assert {"Decision", "DecisionOption", "DecisionState", "DecisionHandler", "confirm"} <= set(sl.__all__)
    assert {"CollectionEditor", "CollectionEntry", "CollectionState", "CollectionChangeHandler"} <= set(sl.__all__)
    assert "CommitPolicy" in sl.__all__
    assert {
        "Editor",
        "EditorCommitHandler",
        "EditorSection",
        "EditorSectionState",
        "EditorState",
        "EditorValues",
    } <= set(sl.__all__)
    assert {"Browser", "BrowserDetail", "BrowserOpenHandler", "BrowserOverview", "list_source"} <= set(sl.__all__)
    assert {"Lookup", "LookupPickHandler", "LookupSearch"} <= set(sl.__all__)
    assert {"Download", "download"} <= set(sl.__all__)
    assert {"Guard", "GuardVerdict", "GuardLedger", "GuardScope", "ADMIT", "guards"} <= set(sl.__all__)
    assert "Feedback" in sl.__all__
    assert "WizardReview" in sl.__all__
    assert {"Scale", "ScaleField", "ScaleEvent", "ScaleOwnership", "UNRATED", "rating"} <= set(sl.__all__)
    assert {"Resource", "ResourceDelivery", "ResourceState", "Pending", "Ready", "Failed", "resource"} <= set(
        sl.__all__
    )
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
        "conform",
        "render_static",
    ):
        with pytest.raises(AttributeError):
            getattr(sl, removed)


def test_explicit_namespaces_expose_specialized_apis() -> None:
    assert sl.primitives.Button
    assert sl.primitives.File
    assert sl.planning.measure
    assert sl.profiling.MemoryProfiler
    assert sl.profiling.snapshot_json
    assert sl.runtime.ComponentRuntime
    assert sl.scene.Codec
    assert sl.scene.SceneFile
    assert sl.html.Renderer
    assert sl.discord.Mount
    assert sl.discord.SessionRegistry
    assert sl.discord.routers
    assert sl.discord.Renderer
    assert sl.discord.SessionKey
    assert sl.discord.SessionPolicy
    assert sl.discord.DiscordPresentation
    assert sl.discord.DiscordMode.COMPONENTS_V2
    assert sl.discord.DiscordModeError
    assert sl.discord.mode_of
    assert sl.discord.presentation.DiscordPresentation
    assert not hasattr(sl.discord, "MountRegistry")
    assert not hasattr(sl.discord, "WhenOpen")
    assert sl.discord.guards.requires_role
    assert sl.discord.durability.DurableSessionRuntime
    assert sl.discord.durability.DurableBot
    assert sl.discord.durability.DiscordFrontend
    assert not hasattr(sl.discord.durability, "MountManager")
    assert sl.TopicBus
    assert sl.discord.Reactor.follow


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
import squid_layouts.profiling
import squid_layouts.runtime
import squid_layouts.topics
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
