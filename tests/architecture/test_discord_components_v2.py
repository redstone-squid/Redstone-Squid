"""Guard the Discord transport against reintroducing legacy message UI."""

import ast
from collections import defaultdict
from pathlib import Path
from typing import override

BOT_ROOT = Path(__file__).parents[2] / "squid" / "bot"
LAYOUTS_ROOT = Path(__file__).parents[2] / "packages" / "squid-ui" / "src"
MESSAGE_METHODS = {"edit", "edit_message", "send", "send_message"}
LEGACY_KEYWORDS = {"content", "embed", "embeds"}
# The framework has to *name* the classic message vocabulary to model it: a
# `MessagePayload` says which mode a payload is in, and the delivery protocol says what a
# host `send` must accept. Naming the types is allowed there; building one is not.
# `rendering.py` names `View` only in a generic bound: a `RenderedMessage` is typed by which
# kind of view its mode produces, and it never builds one.
LEGACY_TYPE_HOMES = {"message_payload.py", "delivery.py", "rendering.py", "adoption.py"}

# The classic target *is* the classic message vocabulary, so these modules build it on
# purpose: they draw embeds and plain views, measure a host's, and mount one. This is the
# whole point of the target and not a regression. Everything else in the package — and every
# line of the bot — stays on Components V2, which is what the rest of this test guards.
CLASSIC_TARGET_HOMES = {"classic.py", "classic_renderer.py", "inspection.py", "message_root.py", "targets.py"}


class DiscordUiVisitor(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.function_names: list[str] = []
        self.violations: list[str] = []
        in_discord_frontend = path.parts[-3:-1] == ("squid_ui", "discord")
        self.names_types_only = in_discord_frontend and path.name in LEGACY_TYPE_HOMES
        self.owns_classic = in_discord_frontend and path.name in CLASSIC_TARGET_HOMES
        self.constructions: set[int] = set()

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
        self.constructions.add(id(node.func))
        method = node.func.attr if isinstance(node.func, ast.Attribute) else None
        if method in MESSAGE_METHODS:
            legacy = {
                keyword.arg for keyword in node.keywords if keyword.arg is not None and keyword.arg in LEGACY_KEYWORDS
            }
            is_archive_relay = self.path.name == "admin.py" and self.function_names[-1:] == ["archive_message"]
            # The framework delivery module is the only place that translates a payload
            # into discord.py's message kwargs.
            is_conversion_boundary = self.path.name == "delivery.py" and self.function_names[-1:] in [
                ["apply"],
                ["apply_interaction"],
            ]
            if legacy and not is_archive_relay and not is_conversion_boundary:
                self.violations.append(f"{self.path}:{node.lineno}: legacy message fields {sorted(legacy)}")
        self.generic_visit(node)

    @override
    def visit_Attribute(self, node: ast.Attribute) -> None:
        if self.owns_classic:
            self.generic_visit(node)
            return
        if self.names_types_only and id(node) not in self.constructions:
            self.generic_visit(node)
            return
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
    for root in (BOT_ROOT, LAYOUTS_ROOT):
        for path in root.rglob("*.py"):
            visitor = DiscordUiVisitor(path)
            visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
            violations.extend(visitor.violations)

    assert not violations, "\n".join(violations)


def test_reaction_router_owns_all_raw_reaction_listeners() -> None:
    """Every gateway reaction event enters through the router, and only through it.

    Compared as a name-per-file mapping rather than a count: a cog that grows its own
    listener and a router that loses one are different bugs, and the diff says which.
    """
    listeners: dict[str, set[str]] = defaultdict(set)
    for path in BOT_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("on_raw_reaction_"):
                listeners[str(path.relative_to(BOT_ROOT))].add(node.name)

    assert dict(listeners) == {
        "reactions.py": {
            "on_raw_reaction_add",
            "on_raw_reaction_clear",
            "on_raw_reaction_clear_emoji",
            "on_raw_reaction_remove",
        }
    }
