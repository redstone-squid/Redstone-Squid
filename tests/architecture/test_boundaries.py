import ast
from pathlib import Path

from pytest_archon import archrule


def test_application_services_do_not_import_discord_or_bot_layer() -> None:
    (
        archrule("application services stay independent from Discord adapters")
        .match("squid.services*")
        .should_not_import("discord*")
        .should_not_import("squid.bot*")
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
    source = Path("squid/db/builds.py").read_text(encoding="utf-8")
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
    source = Path("squid/db/repos/build_repository.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert all(
        not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "DatabaseManager")
        for node in ast.walk(tree)
    )
