"""A type that defines a terminating verb says what ends it, in one clause, up front.

The rule is CLAUDE.md's and the verb table is `docs/squid-ui-architecture.md`. Everything
else states nothing, so only classes defining one of the six verbs are checked. Protocols,
private classes and overriding subclasses all count: the trigger is the method defined.
"""

import ast
import re
from pathlib import Path

import pytest

from tests.support.source_tree import TERMINATING_VERBS, classes_in_source

LIFETIME_EXEMPTIONS: frozenset[str] = frozenset(
    {
        # Seeded with the offenders at the start of the docstring pass; each unit of that
        # pass removes its own. Nothing should be added here without a reason beside it.
        "packages/squid-ui-discord/src/squid_ui_discord/actions.py::ActionResponder",
        "packages/squid-ui-discord/src/squid_ui_discord/challenges.py::ChallengeRunner",
        "packages/squid-ui-discord/src/squid_ui_discord/runtime.py::DiscordUIRuntime",
        "packages/squid-ui/src/squid_ui/profiling/profiler.py::DetachedSpanRecorder",
        "packages/squid-ui/src/squid_ui/profiling/profiler.py::_DetachedSpan",
        "packages/squid-ui/src/squid_ui/profiling/profiler.py::_NoOpDetachedSpan",
    }
)
"""`path::Class` whose first paragraph does not yet name its verb. Listed so an exemption is a decision."""


def _classes_defining_terminating_verbs() -> list[pytest.ParameterSet]:
    found: list[pytest.ParameterSet] = []
    for path, node in classes_in_source():
        verbs = sorted(
            child.name
            for child in node.body
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) and child.name in TERMINATING_VERBS
        )
        if verbs:
            found.append(pytest.param(path, node, verbs, id=f"{path.stem}::{node.name}"))
    return found


def _first_paragraph(node: ast.ClassDef) -> str:
    return (ast.get_docstring(node) or "").split("\n\n", 1)[0]


@pytest.mark.parametrize(("path", "node", "verbs"), _classes_defining_terminating_verbs())
def test_a_type_that_defines_a_terminating_verb_says_what_ends_it(
    path: Path, node: ast.ClassDef, verbs: list[str]
) -> None:
    if f"{path.as_posix()}::{node.name}" in LIFETIME_EXEMPTIONS:
        pytest.skip("listed in LIFETIME_EXEMPTIONS")
    paragraph = _first_paragraph(node)
    missing = [verb for verb in verbs if re.search(rf"\b{verb}\b", paragraph) is None]
    assert not missing, (
        f"{path}::{node.name} defines {missing} but its first docstring paragraph does not name it; "
        f"say what `{missing[0]}()` ends in one clause. Current: {paragraph!r}"
    )


def test_every_exemption_still_names_a_class_that_needs_one() -> None:
    """An exemption for a class that was fixed, renamed or removed is stale and must go."""
    current = {
        f"{path.as_posix()}::{node.name}"
        for path, node in classes_in_source()
        if any(
            isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) and child.name in TERMINATING_VERBS
            for child in node.body
        )
        and any(
            re.search(rf"\b{child.name}\b", _first_paragraph(node)) is None
            for child in node.body
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) and child.name in TERMINATING_VERBS
        )
    }
    stale = sorted(LIFETIME_EXEMPTIONS - current)
    assert not stale, f"remove exemptions that no longer apply: {stale}"
