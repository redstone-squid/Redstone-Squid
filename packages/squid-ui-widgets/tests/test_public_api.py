"""Public namespace and packaging contracts for the machine library."""

import tomllib
from pathlib import Path

import squid_ui_widgets as sp
from squid_ui import testing as engine


def test_the_confirm_guard_composes_with_portable_guards() -> None:
    import squid_ui as sl

    assert sl.guards.all_of(sl.guards.cooldown(5), sp.guards.confirm("Sure?"))


def test_patterns_import_without_a_transport_installed() -> None:
    """Frontend-neutral in fact, not just in intent."""
    engine.assert_imports_without(
        ["squid_ui_widgets", "squid_ui_widgets.guards"], "discord", "anyio", "squid_storage", "squid_ui_discord"
    )


def test_package_metadata_names_only_the_engine() -> None:
    metadata = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    project = metadata["project"]
    assert project["version"] == "0.1.0a1"
    assert project["dependencies"] == ["squid-ui==0.1.0a1"]
    assert "optional-dependencies" not in project
