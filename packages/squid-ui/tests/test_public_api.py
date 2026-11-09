"""Public namespace and packaging contracts for the portable engine."""

from types import ModuleType

import pytest

import squid_ui as sl
from squid_ui import testing

# Public namespace policy: docs/plans/completed/squid-layouts-redesign/58-public-api-narrowing.md.
# Importing these modules must not replace a public factory with a module object.

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
    "squid_reactivity.topics",
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
    """Core imports must work without optional execution, frontend, widget, or storage packages."""
    testing.assert_imports_without(
        [
            "squid_ui",
            "squid_ui.html",
            "squid_ui.planning",
            "squid_ui.profiling",
            "squid_ui.runtime",
            "squid_reactivity.shared_state",
            "squid_reactivity.topics",
        ],
        "discord",
        "anyio",
        "squid_storage",
        "squid_ui_discord",
        "squid_ui_widgets",
    )
