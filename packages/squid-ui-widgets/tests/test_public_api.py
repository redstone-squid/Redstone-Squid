"""Public namespace and packaging contracts for the machine library."""

import subprocess
import sys
import tomllib
from pathlib import Path

import squid_ui_widgets as sp

SPECIALIST_SAMPLES = (
    "Agreement",
    "Browser",
    "CollectionEditor",
    "Decision",
    "Editor",
    "SearchPicker",
    "Menu",
    "MultiChoice",
    "RankedList",
    "SourceRankedList",
    "Tabs",
    "Wizard",
    "StateMachine",
    "TransitionEvent",
    "ComponentDriver",
    "RouteDriver",
    "GridCell",
    "RosterPlacement",
    "TallyOption",
)


def test_root_all_is_fully_resolvable() -> None:
    """Ordering is ruff's business (RUF022 sorts `REVIEW_STEP` ahead of the classes, which
    `sorted()` does not); what a test can usefully add is that every name resolves."""
    assert [name for name in sp.__all__ if not hasattr(sp, name)] == []
    assert len(sp.__all__) == len(set(sp.__all__)), "a duplicated export"


def test_the_documented_patterns_are_exported() -> None:
    missing = [name for name in SPECIALIST_SAMPLES if name not in sp.__all__]
    assert missing == []


def test_the_confirm_guard_lives_here_rather_than_in_the_vocabulary() -> None:
    """`squid_ui.guards` decides admission; the guard that *asks* renders a shell.

    Moving it up is what removed the engine's only forward reference into this package -- a
    function-local import whose own comment explained that the vocabulary could not depend on
    its rendering at import time.
    """
    import squid_ui as sl

    assert sp.guards.confirm
    assert not hasattr(sl.guards, "confirm")
    assert "confirm" not in sl.guards.__all__
    # Still a Guard, so it composes with the portable combinators unchanged.
    assert sl.guards.all_of(sl.guards.cooldown(5), sp.guards.confirm("Sure?"))


def test_patterns_import_without_a_transport_installed() -> None:
    """Frontend-neutral in fact, not just in intent."""
    code = """
import importlib.abc
import sys

class BlockTransport(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".", 1)[0] in {"discord", "anyio", "squid_storage", "squid_ui_discord"}:
            raise ModuleNotFoundError(fullname)
        return None

sys.meta_path.insert(0, BlockTransport())
import squid_ui_widgets
assert squid_ui_widgets.Wizard
assert squid_ui_widgets.guards.confirm
assert not {"discord", "anyio", "squid_storage", "squid_ui_discord"} & set(sys.modules)
"""
    subprocess.run([sys.executable, "-c", code], check=True)


def test_package_metadata_names_only_the_engine() -> None:
    metadata = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    project = metadata["project"]
    assert project["version"] == "0.1.0"
    assert project["dependencies"] == ["squid-ui"]
    assert "optional-dependencies" not in project
