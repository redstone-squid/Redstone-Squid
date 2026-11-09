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
from squid_ui.runtime import component, definition, expansion, tree
from squid_ui.runtime.context import ContextKey as RuntimeContextKey
from squid_ui.runtime.presentation_state import PresentationState as RuntimePresentationState
from squid_ui.runtime.presentation_state import SessionUpdate as RuntimeSessionUpdate

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


def test_existing_contract_imports_preserve_identity() -> None:
    assert sl.ContextKey is RuntimeContextKey is ContextKey
    assert sl.runtime.ContextKey is ContextKey
    assert sl.runtime.PresentationState is RuntimePresentationState is PresentationState
    assert sl.runtime.SessionUpdate is RuntimeSessionUpdate is SessionUpdate
    assert "ComponentRuntime" in vars(sl.runtime)


@pytest.mark.parametrize(
    "entrypoint",
    (
        "squid_ui.chrome",
        "squid_ui.context",
        "squid_ui.presentation",
        "squid_ui.scene",
        "squid_ui.planning",
        "squid_ui.runtime",
        "squid_ui.runtime.context",
        "squid_ui.runtime.presentation_state",
    ),
)
def test_entrypoints_import_in_fresh_processes(entrypoint: str) -> None:
    testing.assert_imports_without([entrypoint, "squid_ui"], "discord", "anyio", "squid_storage", "squid_ui_widgets")


def test_scene_sources_do_not_depend_on_live_execution() -> None:
    forbidden = (
        "squid_ui.runtime",
        "squid_ui.planning",
        "squid_ui.presentation",
        "squid_ui.forms",
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
                "squid_ui.runtime.component",
                "squid_ui.runtime.tree",
                "squid_ui.runtime.expansion",
                "squid_ui.runtime.owner",
                "squid_ui.planning",
            ),
        ),
        (
            "tree",
            ("squid_ui.runtime.component", "squid_ui.runtime.expansion", "squid_ui.runtime.owner", "squid_ui.planning"),
        ),
        ("expansion", ("squid_ui.runtime.component", "squid_ui.runtime.owner", "squid_ui.planning")),
        ("render_cache", ("squid_ui.runtime.component", "squid_ui.runtime.owner", "squid_ui.planning")),
    ),
)
def test_component_execution_dependencies_flow_toward_the_contract(module: str, forbidden: tuple[str, ...]) -> None:
    imports = runtime_imports(SOURCE / "runtime" / f"{module}.py")
    assert not {name for name in imports if name.startswith(forbidden)}


def test_component_facade_preserves_existing_imports() -> None:
    assert sl.Component is component.Component is definition.Component
    assert component.ComponentTree is tree.ComponentTree
    assert component.IncrementalRender is tree.IncrementalRender
    assert component.render_component_tree is expansion.render_component_tree


def test_handler_composition_does_not_import_planning_or_runtime() -> None:
    imports = runtime_imports(SOURCE / "handlers.py")
    assert not {name for name in imports if name.startswith(("squid_ui.planning", "squid_ui.runtime"))}
