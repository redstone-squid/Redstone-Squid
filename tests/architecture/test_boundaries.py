import ast
from pathlib import Path

from pytest_archon import archrule


def test_exception_model_is_transport_neutral() -> None:
    (
        archrule("application exceptions stay independent from transport adapters")
        .match("squid.core.errors")
        .should_not_import("discord*")
        .should_not_import("fastapi*")
        .should_not_import("squid.bot*")
        .check("squid", only_direct_imports=True)
    )


def test_domain_layers_are_framework_and_persistence_independent() -> None:
    (
        archrule("domain layers stay independent from frameworks and outer layers")
        .match("squid.*.domain*")
        .should_not_import("sqlalchemy*")
        .should_not_import("discord*")
        .should_not_import("fastapi*")
        .should_not_import("squid.*.application*")
        .should_not_import("squid.*.infrastructure*")
        .check("squid", only_direct_imports=True)
    )


def test_application_layers_are_framework_and_infrastructure_independent() -> None:
    (
        archrule("application layers depend on ports rather than adapters")
        .match("squid.*.application*")
        .should_not_import("sqlalchemy*")
        .should_not_import("discord*")
        .should_not_import("fastapi*")
        .should_not_import("squid.*.infrastructure*")
        .check("squid", only_direct_imports=True)
    )


def test_transports_do_not_import_persistence_adapters() -> None:
    for transport in ("squid.bot*", "squid.api*"):
        (
            archrule("transports invoke application services")
            .match(transport)
            .should_not_import("sqlalchemy*")
            .should_not_import("squid.persistence*")
            .should_not_import("squid.*.infrastructure*")
            .check("squid", only_direct_imports=True)
        )


def test_voting_adapter_does_not_construct_database_service_locator() -> None:
    source = Path("squid/bot/voting/vote_session.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    database_manager_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "DatabaseManager"
    ]

    assert database_manager_calls == []


def test_build_entity_does_not_expose_active_record_persistence_methods() -> None:
    source = Path("squid/builds/domain/models.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    build_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Build")
    method_names = {node.name for node in build_class.body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)}

    assert method_names.isdisjoint(
        {
            "from_id",
            "from_message_id",
            "save",
            "confirm",
            "deny",
            "ai_generate_from_message",
            "generate_embedding",
        }
    )
    assert all(not isinstance(node, ast.ClassDef) or node.name != "BuildLock" for node in tree.body)
    assert all(
        not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "DatabaseManager")
        for node in ast.walk(tree)
    )


def test_build_manager_does_not_construct_database_service_locator() -> None:
    source = Path("squid/builds/infrastructure/repository.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert all(
        not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "DatabaseManager")
        for node in ast.walk(tree)
    )


def test_build_repository_does_not_coordinate_leases() -> None:
    source = Path("squid/builds/infrastructure/repository.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    repository = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "BuildRepository")
    method_names = {node.name for node in repository.body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)}

    assert method_names.isdisjoint({"acquire_lock", "release_lock", "locked", "clean_stale_locks"})
    assert "BuildLockRepository" not in source
