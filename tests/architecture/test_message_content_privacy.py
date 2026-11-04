"""Keep the Discord message context out of public API modules.

The import scan catches direct coupling to message repositories and values. It supplements
response tests; it cannot prove that content never arrives through another service or alias.
"""

import ast
from pathlib import Path

from tests.support.source_tree import source_tree

API_ROOT = Path(__file__).resolve().parents[2] / "squid" / "api"
MESSAGE_MODULES = {"squid.messages", "squid.messages.application", "squid.messages.domain"}


def test_the_api_never_imports_the_message_context() -> None:
    """Reject direct imports of the message context from API source files."""
    offenders: list[str] = []
    for path in API_ROOT.rglob("*.py"):
        tree = source_tree(path)
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
