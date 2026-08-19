"""Guard the one ephemerality rule against the trap that hides its violations.

`Context.send` forwards to `Messageable.send`, which has no `ephemeral` parameter, so a hybrid
command that writes `ephemeral=True` is *public* whenever it is invoked as a prefix command —
silently, and only on the path nobody tests by hand. That is how `!error <ref>` posted
tracebacks (phase 3) and how `!account merge-code` posted an account-takeover credential
(phase 5.7).

So a literal `True` is banned on a `Context` reply. `personal(ctx)` says the same thing while
admitting the condition, and a payload that must never reach a channel goes through
`deliver_privately`, which uses direct messages instead. Interactions are untouched: there
`ephemeral=True` means what it says.
"""

import ast
from pathlib import Path

BOT_ROOT = Path(__file__).parents[2] / "squid" / "bot"

CONTEXT_REPLY_METHODS = {"send", "reply", "defer"}

EXEMPT = {
    # The one place that knows the difference, because it is the one that checks.
    BOT_ROOT / "utils" / "visibility.py",
}


def _violations(path: Path) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
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
    offenders = [
        violation for path in sorted(BOT_ROOT.rglob("*.py")) if path not in EXEMPT for violation in _violations(path)
    ]

    assert offenders == [], "Use personal(ctx), or deliver_privately for a payload a channel must never hold"
