"""Pin application UI ownership to Squid screens, widgets, and portable events."""

import ast
from pathlib import Path

from tests.support.source_tree import source_tree

BOT_ROOT = Path(__file__).parents[2] / "squid" / "bot"


def _classes(tree: ast.AST) -> list[ast.ClassDef]:
    return [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]


def _component_class(node: ast.ClassDef) -> bool:
    return any(
        ast.unparse(base).endswith((".Component", ".Screen"))
        or ".Component[" in ast.unparse(base)
        or ".Screen[" in ast.unparse(base)
        for base in node.bases
    )


def test_application_code_defines_no_raw_discord_ui_subclasses_or_items() -> None:
    violations: list[str] = []
    for path in BOT_ROOT.rglob("*.py"):
        tree = source_tree(path)
        for node in _classes(tree):
            for base in node.bases:
                name = ast.unparse(base)
                if name.startswith("discord.ui."):
                    violations.append(f"{path}:{node.lineno}: raw interactive base {name}")
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "RawItem":
                violations.append(f"{path}:{node.lineno}: RawItem")
            if isinstance(node, ast.Attribute) and node.attr == "RawItem":
                violations.append(f"{path}:{node.lineno}: {ast.unparse(node)}")

    assert not violations, "\n".join(violations)


def test_application_components_do_not_own_message_roots_or_send_methods() -> None:
    violations: list[str] = []
    for path in BOT_ROOT.rglob("*.py"):
        tree = source_tree(path)
        for component in filter(_component_class, _classes(tree)):
            for member in component.body:
                if isinstance(member, ast.AnnAssign) and "MessageRoot" in ast.unparse(member.annotation):
                    violations.append(f"{path}:{member.lineno}: {component.name} stores a MessageRoot")
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and member.name == "send":
                    violations.append(f"{path}:{member.lineno}: {component.name}.send")

    assert not violations, "\n".join(violations)
