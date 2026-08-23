"""Public namespace and optional-adapter packaging contracts."""

import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

import squid_layouts as sl

# ~105 names: the authoring vocabulary. See docs/plans/squid-layouts-redesign/58-public-api-narrowing.md
# for the promotion rule and the grouped rationale (namespaces, component model, document,
# semantic factories, factory type aliases, adaptation verbs, text, node union, event types,
# central nouns) this list encodes.
ROOT_API = frozenset(
    {
        "ActionEvent",
        "ChildLike",
        "ChoiceEvent",
        "Component",
        "Conditional",
        "ContextKey",
        "Document",
        "DocumentLike",
        "EntityEvent",
        "EntitySelectionEvent",
        "LayoutNode",
        "NavigateEvent",
        "OpenEvent",
        "Palette",
        "PressEvent",
        "ScaleEvent",
        "SelectionEvent",
        "SubmitEvent",
        "TextLike",
        "TextValue",
        "ToggleEvent",
        "Tone",
        "action",
        "action_group",
        "actions",
        "article",
        "aside",
        "best_effort",
        "block",
        "budget",
        "bullet",
        "bullets",
        "choice",
        "choices",
        "cluster",
        "code",
        "column",
        "columns",
        "computed",
        "controlled",
        "destination",
        "details",
        "download",
        "entities",
        "entity_choice",
        "errors",
        "fallback",
        "field",
        "fields",
        "figure",
        "form",
        "forms",
        "group",
        "guards",
        "heading",
        "html",
        "interactions",
        "item",
        "item_label",
        "items",
        "keep_with_next",
        "link",
        "managed",
        "md",
        "measure",
        "media",
        "media_item",
        "navigation",
        "note",
        "optional",
        "paged",
        "paragraph",
        "patterns",
        "plain",
        "planning",
        "primitives",
        "profiling",
        "progress",
        "quote",
        "rating",
        "raw_md",
        "resource",
        "routed_action",
        "routed_choices",
        "routing",
        "runtime",
        "scene",
        "section",
        "semantic",
        "sources",
        "spill",
        "stack",
        "state",
        "status",
        "summary",
        "table",
        "table_row",
        "temporal",
        "text",
        "themed",
        "timestamp",
        "toggle",
        "truncate",
        "unbreakable",
        "zoned_timestamp",
    }
)

ROOT_NAMESPACES = (
    "errors",
    "forms",
    "guards",
    "html",
    "interactions",
    "patterns",
    "planning",
    "primitives",
    "profiling",
    "routing",
    "runtime",
    "scene",
    "semantic",
    "sources",
    "temporal",
    "text",
)

RENAMED_SUBMODULES = (
    "squid_layouts.planning.measurement",
    "squid_layouts.runtime.histories",
    "squid_layouts.runtime.topics",
    "squid_layouts.discord.composition",
    "squid_layouts.discord.conformance",
)

SPECIALIST_SAMPLES = {
    "Wizard": sl.patterns,
    "WizardState": sl.patterns,
    "FormSpec": sl.forms,
    "SemanticNode": sl.semantic,
    "ActionMiddleware": sl.interactions,
    "TopicBus": sl.runtime,
    "ReactiveCycleError": sl.runtime,
    "Window": sl.sources,
    "Route": sl.routing,
    "PlanReport": sl.scene,
    "Button": sl.primitives,
    "Renderer": sl.html,
    "Guard": sl.guards,
    "LayoutError": sl.errors,
    "ZonedDateTime": sl.temporal,
    "Message": sl.text,
}


def test_root_exports_exactly_the_authoring_vocabulary() -> None:
    assert set(sl.__all__) == ROOT_API
    assert sl.__all__ == sorted(sl.__all__)


def test_root_all_is_fully_resolvable() -> None:
    assert [n for n in sl.__all__ if not hasattr(sl, n)] == []


def test_namespaces_are_modules_not_shadowed_callables() -> None:
    """`import squid_layouts.entity as e` must not hand back a factory."""
    for name in ROOT_NAMESPACES:
        assert isinstance(getattr(sl, name), ModuleType), f"sl.{name} is shadowed"


@pytest.mark.parametrize("dotted", RENAMED_SUBMODULES)
def test_renamed_submodules_are_modules_not_shadowed_callables(dotted: str) -> None:
    import importlib

    mod = importlib.import_module(dotted)
    assert isinstance(mod, ModuleType)


def test_specialists_live_in_namespaces_and_not_at_root() -> None:
    for name, ns in SPECIALIST_SAMPLES.items():
        assert name not in sl.__all__ and not hasattr(sl, name)
        assert name in ns.__all__  # catches the ReactiveCycleError class of bug


def test_explicit_namespaces_expose_specialized_apis() -> None:
    assert sl.primitives.Button
    assert sl.primitives.File
    assert sl.primitives.GalleryItem
    assert sl.primitives.PremiumButton
    assert sl.planning.measure
    assert sl.profiling.MemoryProfiler
    assert sl.profiling.snapshot_json
    assert sl.runtime.ComponentRuntime
    assert sl.scene.Codec
    assert sl.scene.SceneFile
    assert sl.html.Renderer
    assert sl.discord.Mount
    assert sl.discord.CheckboxGroupField
    assert sl.discord.MountDefaults
    assert sl.discord.SessionRegistry
    assert sl.discord.routers
    assert sl.discord.V2Renderer
    assert sl.discord.ClassicRenderer
    assert sl.discord.classic.compose
    assert sl.discord.SessionKey
    assert sl.discord.SessionPolicy
    assert sl.discord.Screen
    assert sl.discord.Scope
    assert sl.discord.Opener
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
    assert sl.runtime.TopicBus
    assert sl.discord.Reactor.follow
    assert {"Shared", "SharedStateConflictError", "state", "addresses"} <= set(sl.runtime.__all__)


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
import squid_layouts.runtime.shared
import squid_layouts.runtime.topics
assert squid_layouts.runtime.shared.Shared
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
        "packaging>=24,<27",
    }
