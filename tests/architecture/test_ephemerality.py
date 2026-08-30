"""Guard the one ephemerality rule against the trap that hides its violations.

`Context.send` forwards to `Messageable.send`, which has no `ephemeral` parameter, so a hybrid
command that writes `ephemeral=True` is *public* whenever it is invoked as a prefix command —
silently, and only on the path nobody tests by hand. That is how `!error <ref>` posted
tracebacks (phase 3) and how `!account merge-code` posted an account-takeover credential
(phase 5.7).

So a literal `True` is banned on a `Context` reply. `DiscordUI` owns the public/personal
transport distinction, and a payload that must never reach a channel uses `Private`, which
uses direct messages instead. Interactions are untouched: there `ephemeral=True` means what
it says.
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

    assert offenders == [], "Use DiscordUI audience policy instead of a literal ephemeral=True"
