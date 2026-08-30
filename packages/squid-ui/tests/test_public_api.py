"""Public namespace and packaging contracts for the portable engine."""

import tomllib
from pathlib import Path
from types import ModuleType

import pytest

import squid_ui as sl
from squid_ui import testing

# ~115 names: the authoring vocabulary. See docs/plans/squid-ui-redesign/58-public-api-narrowing.md
# for the promotion rule and the grouped rationale (namespaces, component model, document,
# semantic factories, factory type aliases, adaptation verbs, text, node union, event types,
# central nouns) this list encodes.

ROOT_NAMESPACES = (
    "chrome",
    "document",
    "emoji",
    "entity",
    "errors",
    "forms",
    "guards",
    "html",
    "interactions",
    "operations",
    "palette",
    "planning",
    "primitives",
    "profiling",
    "routing",
    "resources",
    "runtime",
    "scene",
    "semantic",
    "slack",
    "sources",
    "temporal",
    "text",
)

RENAMED_SUBMODULES = (
    "squid_ui.planning.layout_measurement.solver",
    "squid_ui.runtime.histories",
    "squid_ui.runtime.topics",
)


def test_namespaces_are_modules_not_shadowed_callables() -> None:
    """`import squid_ui.entity as e` must not hand back a factory."""
    for name in ROOT_NAMESPACES:
        assert isinstance(getattr(sl, name), ModuleType), f"sl.{name} is shadowed"


def test_promoted_value_modules_do_not_shadow_their_factories() -> None:
    """`grids`, `rosters` and `tallies` are plural precisely so they cannot do this.

    `sl.grid`, `sl.roster` and `sl.tally` are factories. A submodule of the same name would be
    bound onto the package by the import system on first import and shadow them.
    """
    import squid_ui.grids
    import squid_ui.rosters
    import squid_ui.tallies  # noqa: F401

    assert callable(sl.grid) and callable(sl.roster) and callable(sl.tally)


@pytest.mark.parametrize("dotted", RENAMED_SUBMODULES)
def test_renamed_submodules_are_modules_not_shadowed_callables(dotted: str) -> None:
    import importlib

    mod = importlib.import_module(dotted)
    assert isinstance(mod, ModuleType)


def test_engine_imports_without_transport_or_store_dependencies() -> None:
    """The point of the split, asserted: the engine installs none of these.

    `squid_storage` joins `discord` and `anyio` on the blocked list because it was a mandatory
    dependency of this package while only the Discord durability modules imported it.
    """
    testing.assert_imports_without(
        [
            "squid_ui",
            "squid_ui.html",
            "squid_ui.planning",
            "squid_ui.profiling",
            "squid_ui.runtime",
            "squid_ui.runtime.shared",
            "squid_ui.runtime.topics",
        ],
        "discord",
        "anyio",
        "squid_storage",
        "squid_ui_discord",
        "squid_ui_widgets",
    )


def test_package_metadata_names_only_the_reactive_kernel() -> None:
    metadata = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    project = metadata["project"]
    assert project["version"] == "0.1.0a1"
    assert project["dependencies"] == ["markdown-it-py>=4.0,<5", "squid-reactivity==0.1.0a1"]
    # Both extras left with the adapter: `discord` carried discord.py/anyio/packaging, and
    # `postgres` only ever forwarded to squid-storage for Discord durability.
    assert "optional-dependencies" not in project
