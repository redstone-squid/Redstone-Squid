"""Discord message content must not reach the public API.

`messages.content` is retained for internal work — offline build inference, the edit
context menu, rendering a delete-log card without refetching — and `docs/plans/rest-api.md`
records that it is deliberately never exposed. Nothing structural stops a serialiser
from picking it up, so this asserts it.
"""

import ast
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[2] / "squid" / "api"
MESSAGE_MODULES = {"squid.messages", "squid.messages.application", "squid.messages.domain"}


def test_the_api_never_imports_the_message_context() -> None:
    """The one reliable guard: message facts have no business in a public serialiser."""
    offenders: list[str] = []
    for path in API_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                module = node.module
            elif isinstance(node, ast.Import):
                module = next((alias.name for alias in node.names), "")
            else:
                continue
            if module in MESSAGE_MODULES or module.startswith("squid.messages."):
                offenders.append(f"{path.relative_to(API_ROOT.parent.parent)}: {module}")

    assert offenders == [], "\n".join(offenders)
