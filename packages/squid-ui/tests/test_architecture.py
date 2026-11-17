"""Keep shared values below the runtime and planner implementations."""

import ast
from pathlib import Path

import pytest

import squid_ui as sl
from squid_ui import testing
from squid_ui.action_mode import ActionMode
from squid_ui.context import ContextKey
from squid_ui.planning.result import PlanResult
from squid_ui.presentation import PresentationState, SessionUpdate

SOURCE = Path(sl.__file__).parent


def runtime_imports(path: Path) -> set[str]:
    """Find executable imports, including imports nested inside functions."""
    imports: set[str] = set()

    def visit(node: ast.AST) -> None:
        if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
            for child in node.orelse:
                visit(child)
            return
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
            imports.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(ast.parse(path.read_text()))
    return imports


def test_shared_contracts_and_planning_do_not_import_runtime() -> None:
    paths = [SOURCE / name for name in ("context.py", "presentation.py", "chrome.py", "sources.py")]
    paths.extend((SOURCE / "planning").rglob("*.py"))
    violations = {
        str(path.relative_to(SOURCE)): sorted(
            name for name in runtime_imports(path) if name.startswith("squid_ui.runtime")
        )
        for path in paths
        if any(name.startswith("squid_ui.runtime") for name in runtime_imports(path))
    }
    assert not violations


def test_search_explorers_do_not_import_each_other_or_the_coordinator() -> None:
    for explorer, other in (("offer_explorer", "partition_explorer"), ("partition_explorer", "offer_explorer")):
        imports = runtime_imports(SOURCE / "planning" / f"{explorer}.py")
        assert f"squid_ui.planning.{other}" not in imports
        assert "squid_ui.planning.joint_search" not in imports


def test_allocation_frontier_does_not_import_search_or_explorers() -> None:
    imports = runtime_imports(SOURCE / "planning" / "allocation_frontier.py")
    assert not imports & {
        "squid_ui.planning.joint_search",
        "squid_ui.planning.offer_explorer",
        "squid_ui.planning.partition_explorer",
    }


def test_existing_contract_imports_preserve_identity() -> None:
    assert sl.ContextKey is ContextKey
    assert sl.runtime.ContextKey is ContextKey
    assert sl.runtime.PresentationState is PresentationState
    assert sl.runtime.SessionUpdate is SessionUpdate
    assert "ComponentRuntime" in vars(sl.runtime)


@pytest.mark.parametrize(
    "entrypoint",
    (
        "squid_ui.chrome",
        "squid_ui.authoring.semantic",
        "squid_ui.content.text",
        "squid_ui.context",
        "squid_ui.interactions.forms",
        "squid_ui.interactions.handlers",
        "squid_ui.presentation",
        "squid_ui.scene",
        "squid_ui.planning",
        "squid_ui.runtime",
    ),
)
def test_entrypoints_import_in_fresh_processes(entrypoint: str) -> None:
    testing.assert_imports_without(
        [entrypoint, "squid_ui"],
        "discord",
        "anyio",
        "squid_storage",
        "squid_ui_widgets",
        "squid_ui.ext.discord",
        "squid_ui.ext.slack",
        "squid_ui.ext.html",
    )


def test_scene_sources_do_not_depend_on_live_execution() -> None:
    forbidden = (
        "squid_ui.runtime",
        "squid_ui.planning",
        "squid_ui.presentation",
        "squid_ui.interactions.forms",
        "squid_ui.interactions",
    )
    violations = {
        str(path.relative_to(SOURCE)): sorted(name for name in runtime_imports(path) if name.startswith(forbidden))
        for path in (SOURCE / "scene").glob("*.py")
        if any(name.startswith(forbidden) for name in runtime_imports(path))
    }
    assert not violations


def test_planning_owns_live_results_and_scene_preserves_action_modes() -> None:
    assert sl.planning.PlanResult is PlanResult
    assert not hasattr(sl.scene, "PlanResult")
    assert sl.interactions.ActionMode is ActionMode
    assert sl.scene.Button("Save", "save").mode is ActionMode.EXCLUSIVE


@pytest.mark.parametrize(
    ("module", "forbidden"),
    (
        (
            "definition",
            (
                "squid_ui.runtime.tree",
                "squid_ui.runtime.expansion",
                "squid_ui.runtime.owner",
                "squid_ui.planning",
            ),
        ),
        (
            "tree",
            ("squid_ui.runtime.expansion", "squid_ui.runtime.owner", "squid_ui.planning"),
        ),
        ("expansion", ("squid_ui.runtime.owner", "squid_ui.planning")),
        ("render_cache", ("squid_ui.runtime.owner", "squid_ui.planning")),
    ),
)
def test_component_execution_dependencies_flow_toward_the_contract(module: str, forbidden: tuple[str, ...]) -> None:
    imports = runtime_imports(SOURCE / "runtime" / f"{module}.py")
    assert not {name for name in imports if name.startswith(forbidden)}


def test_handler_composition_does_not_import_planning_or_runtime() -> None:
    imports = runtime_imports(SOURCE / "interactions" / "handlers.py")
    assert not {name for name in imports if name.startswith(("squid_ui.planning", "squid_ui.runtime"))}


def test_core_does_not_import_target_implementations() -> None:
    adapters = ("squid_ui.ext.discord", "squid_ui.ext.slack", "squid_ui.ext.html")
    violations = {
        str(path.relative_to(SOURCE)): sorted(name for name in runtime_imports(path) if name.startswith(adapters))
        for path in SOURCE.rglob("*.py")
        if any(name.startswith(adapters) for name in runtime_imports(path))
    }
    assert not violations
