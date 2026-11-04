"""Reject literal ephemeral=True on calls spelled ctx.send, ctx.reply, or ctx.defer.

Hybrid commands can also run as prefix commands, where an ephemeral reply is unavailable.
Application replies use Scope audience policy; Private can deliver by direct message.
This AST check covers the literal call pattern, not aliases or dynamically supplied kwargs.
"""

import ast
from pathlib import Path

from tests.support.source_tree import source_tree

BOT_ROOT = Path(__file__).parents[2] / "squid" / "bot"

CONTEXT_REPLY_METHODS = {"send", "reply", "defer"}


def _violations(path: Path) -> list[str]:
    found: list[str] = []
    for node in ast.walk(source_tree(path)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        target = node.func.value
        if node.func.attr not in CONTEXT_REPLY_METHODS or not isinstance(target, ast.Name) or target.id != "ctx":
            continue
        found.extend(
            f"{path.relative_to(BOT_ROOT.parents[1])}:{node.lineno}"
            for keyword in node.keywords
            if keyword.arg == "ephemeral" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True
        )
    return found


def test_no_context_reply_claims_an_ephemerality_it_may_not_get() -> None:
    offenders = [violation for path in sorted(BOT_ROOT.rglob("*.py")) for violation in _violations(path)]

    assert offenders == [], "Use Scope audience policy instead of a literal ephemeral=True"
