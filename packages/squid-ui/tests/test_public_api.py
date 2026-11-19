"""Public namespace and packaging contracts for the portable engine."""

from importlib import import_module
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
    "interactions",
    "operations",
    "palette",
    "planning",
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


@pytest.mark.parametrize(
    ("package", "names"),
    (
        ("authoring", ("document", "semantic", "presentations", "grids", "rosters", "tallies")),
        ("content", ("assets", "emoji", "entity", "temporal", "text")),
        ("interactions", ("forms", "guards", "ownership")),
    ),
)
def test_grouped_modules_preserve_root_shortcuts(package: str, names: tuple[str, ...]) -> None:
    for name in names:
        assert getattr(sl, name) is import_module(f"squid_ui.{package}.{name}")
    assert callable(sl.handlers)


def test_namespaces_are_modules_not_shadowed_callables() -> None:
    """`import squid_ui.content.entity as e` must not hand back a factory."""
    for name in ROOT_NAMESPACES:
        assert isinstance(getattr(sl, name), ModuleType), f"sl.{name} is shadowed"


def test_promoted_value_modules_do_not_shadow_their_factories() -> None:
    """`grids`, `rosters` and `tallies` are plural precisely so they cannot do this.

    `sl.grid`, `sl.roster` and `sl.tally` are factories. A submodule of the same name would be
    bound onto the package by the import system on first import and shadow them.
    """
    import squid_ui.authoring.grids
    import squid_ui.authoring.rosters
    import squid_ui.authoring.tallies  # noqa: F401

    assert callable(sl.grid) and callable(sl.roster) and callable(sl.tally)


def test_presentation_state_module_does_not_shadow_the_declaration_factory() -> None:
    import_module("squid_ui.presentation_state")
    assert callable(sl.presentation)
    assert callable(sl.presentation.toggle)


def test_engine_imports_without_transport_or_store_dependencies() -> None:
    """Core imports must work without optional execution, frontend, widget, or storage packages."""
    testing.assert_imports_without(
        [
            "squid_ui",
            "squid_ui.planning",
            "squid_ui.routing",
            "squid_ui.profiling",
            "squid_ui.runtime",
            "squid_reactivity",
            "squid_reactivity.topics",
        ],
        "discord",
        "anyio",
        "squid_storage",
        "squid_ui.ext.discord",
        "squid_ui.ext.slack",
        "squid_ui.ext.html",
        "squid_ui_widgets",
    )
