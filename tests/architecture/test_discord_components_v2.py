"""Guard the Discord transport against reintroducing legacy message UI."""

import ast
from pathlib import Path
from typing import override

BOT_ROOT = Path(__file__).parents[2] / "squid" / "bot"
MESSAGE_METHODS = {"edit", "edit_message", "send", "send_message"}
LEGACY_KEYWORDS = {"content", "embed", "embeds"}


class DiscordUiVisitor(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.function_names: list[str] = []
        self.violations: list[str] = []

    @override
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.function_names.append(node.name)
        self.generic_visit(node)
        self.function_names.pop()

    @override
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.function_names.append(node.name)
        self.generic_visit(node)
        self.function_names.pop()

    @override
    def visit_Call(self, node: ast.Call) -> None:
        method = node.func.attr if isinstance(node.func, ast.Attribute) else None
        if method in MESSAGE_METHODS:
            legacy = {
                keyword.arg for keyword in node.keywords if keyword.arg is not None and keyword.arg in LEGACY_KEYWORDS
            }
            is_archive_relay = self.path.name == "admin.py" and self.function_names[-1:] == ["archive_message"]
            is_conversion_boundary = self.path.name == "components.py" and self.function_names[-1:] in [
                ["edit_layout"],
                ["edit_interaction_layout"],
            ]
            if legacy and not is_archive_relay and not is_conversion_boundary:
                self.violations.append(f"{self.path}:{node.lineno}: legacy message fields {sorted(legacy)}")
        self.generic_visit(node)

    @override
    def visit_Attribute(self, node: ast.Attribute) -> None:
        if (
            node.attr in {"Embed", "View"}
            and isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "discord"
            and node.value.attr == "ui"
        ):
            self.violations.append(f"{self.path}:{node.lineno}: discord.ui.{node.attr}")
        if node.attr == "Embed" and isinstance(node.value, ast.Name) and node.value.id == "discord":
            self.violations.append(f"{self.path}:{node.lineno}: discord.Embed")
        self.generic_visit(node)


def test_bot_uses_components_v2_outside_archive_relay() -> None:
    violations: list[str] = []
    for path in BOT_ROOT.rglob("*.py"):
        visitor = DiscordUiVisitor(path)
        visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
        violations.extend(visitor.violations)

    assert not violations, "\n".join(violations)
