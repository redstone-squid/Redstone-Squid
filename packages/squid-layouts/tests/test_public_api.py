"""Public namespace and packaging contracts for the portable engine."""

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
        "grid",
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
        "operation",
        "operations",
        "paged",
        "paragraph",
        "plain",
        "planning",
        "primitives",
        "profiling",
        "progress",
        "quote",
        "rating",
        "raw_md",
        "resource",
        "resources",
        "roster",
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
        "tally",
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
    "operations",
    "planning",
    "primitives",
    "profiling",
    "routing",
    "resources",
    "runtime",
    "scene",
    "semantic",
    "sources",
    "temporal",
    "text",
)

RENAMED_SUBMODULES = (
    "squid_layouts.planning.layout_measurement.solver",
    "squid_layouts.runtime.histories",
    "squid_layouts.runtime.topics",
)

SPECIALIST_SAMPLES = {
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


def test_promoted_value_modules_do_not_shadow_their_factories() -> None:
    """`grids`, `rosters` and `tallies` are plural precisely so they cannot do this.

    `sl.grid`, `sl.roster` and `sl.tally` are factories. A submodule of the same name would be
    bound onto the package by the import system on first import and shadow them.
    """
    import squid_layouts.grids
    import squid_layouts.rosters
    import squid_layouts.tallies  # noqa: F401

    assert callable(sl.grid) and callable(sl.roster) and callable(sl.tally)


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
    assert sl.runtime.TopicBus
    assert {"Shared", "SharedPool", "SharedFactory", "ReactiveConflictError", "state", "addresses"} <= set(
        sl.runtime.__all__
    )


def test_the_engine_no_longer_carries_its_leaf_namespaces() -> None:
    """`sl.discord` was a lazy hook and `sl.patterns` an eager one; both are packages now."""
    for name in ("discord", "patterns"):
        assert not hasattr(sl, name)
        assert name not in sl.__all__


def test_engine_imports_without_transport_or_store_dependencies() -> None:
    """The point of the split, asserted: the engine installs none of these.

    `squid_stores` joins `discord` and `anyio` on the blocked list because it was a mandatory
    dependency of this package while only the Discord durability modules imported it.
    """
    code = """
import importlib.abc
import sys

class BlockDownstream(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".", 1)[0] in {"discord", "anyio", "squid_stores", "squid_discord", "squid_patterns"}:
            raise ModuleNotFoundError(fullname)
        return None

sys.meta_path.insert(0, BlockDownstream())
import squid_layouts
import squid_layouts.html
import squid_layouts.planning
import squid_layouts.profiling
import squid_layouts.runtime
import squid_layouts.runtime.shared
import squid_layouts.runtime.topics
assert squid_layouts.runtime.shared.Shared
assert squid_layouts.runtime.shared.SharedPool
assert not {"discord", "anyio", "squid_stores", "squid_discord", "squid_patterns"} & set(sys.modules)
"""
    subprocess.run([sys.executable, "-c", code], check=True)


def test_package_metadata_names_only_the_reactive_kernel() -> None:
    metadata = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    project = metadata["project"]
    assert project["version"] == "0.1.0"
    assert project["dependencies"] == ["squid-reactive"]
    # Both extras left with the adapter: `discord` carried discord.py/anyio/packaging, and
    # `postgres` only ever forwarded to squid-stores for Discord durability.
    assert "optional-dependencies" not in project
