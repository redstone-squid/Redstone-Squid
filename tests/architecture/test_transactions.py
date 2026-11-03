"""Application scopes own business transactions across all repositories."""

import ast
from pathlib import Path

from tests.support.source_tree import source_tree

SESSION_FACTORY_OWNERS = {
    Path("squid/persistence/engine.py"),
    Path("squid/persistence/unit_of_work.py"),
    # Queue metrics are an infrastructure read, independent of business workflows.
    Path("squid/worker/queue_health.py"),
}


def test_session_factories_stay_with_database_lifetime_owners() -> None:
    violations: list[str] = []
    for path in Path("squid").rglob("*.py"):
        if path in SESSION_FACTORY_OWNERS:
            continue
        violations.extend(
            f"{path}:{node.lineno}"
            for node in ast.walk(source_tree(path))
            if (
                isinstance(node, ast.ImportFrom)
                and node.module is not None
                and node.module.startswith("sqlalchemy")
                and any(alias.name in {"sessionmaker", "async_sessionmaker"} for alias in node.names)
            )
        )
    assert not violations, f"Use a bound repository or an application UoW: {violations}"


def test_sql_adapters_leave_commit_and_rollback_to_the_unit_of_work() -> None:
    violations: list[str] = []
    for path in Path("squid").rglob("*.py"):
        if path == Path("squid/persistence/unit_of_work.py"):
            continue
        tree = source_tree(path)
        imports_sqlalchemy = any(
            isinstance(node, ast.ImportFrom) and node.module is not None and node.module.startswith("sqlalchemy")
            for node in ast.walk(tree)
        )
        if not imports_sqlalchemy:
            continue
        violations.extend(
            f"{path}:{node.lineno}"
            for node in ast.walk(tree)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"commit", "rollback"}
            )
        )
    assert not violations, f"Transaction completion belongs to the application UoW: {violations}"
