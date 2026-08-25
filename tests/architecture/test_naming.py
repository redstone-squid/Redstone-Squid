"""Naming rules that keep object lifetimes legible.

Lifetime is carried by verbs, not by nouns. The public surface has 93 distinct class-name
suffixes and 60 of them are used exactly once, so a closed noun vocabulary would have to
reject `Component`, `Mount`, `Screen` and `Destination` or grow until it was not a
vocabulary. What nouns owe instead is consistency, which needs no dictionary: one meaning
per word. The verbs are where a small closed set genuinely fits.
"""

import ast
import importlib
import inspect
import pkgutil
from collections import defaultdict
from pathlib import Path
from types import ModuleType

import squid_layouts
import squid_reactive
import squid_replicated
import squid_stores

PACKAGE_SOURCE_ROOTS = (
    Path("packages/squid-layouts/src"),
    Path("packages/squid-reactive/src"),
    Path("packages/squid-replicated/src"),
    Path("packages/squid-stores/src"),
)

TERMINATING_VERBS = frozenset({"close", "detach", "finish", "cancel", "discard", "run"})
"""What ends something, and nothing else. See `docs/squid-layouts-architecture.md`."""

OBJECT_ENDING_VERBS = frozenset({"close", "finish"})
"""The two that end *the object itself*, and so may not appear on one class together.

`run` ends an owner's tasks, `discard` ends staged work, `cancel` ends unfinished async
work. Those name other subjects, so pairing them with `close` is correct --
`PersistedPool` has `run` and `close`, `SubscriptionReconciler` has `discard` and `close`.
"""

DISCOURAGED_VERBS = frozenset({"shutdown", "stop", "dispose", "teardown", "destroy", "terminate", "kill"})
"""Synonyms for the six that would reintroduce the ambiguity they were chosen to remove.

A denylist rather than an allowlist, so it scales to names nobody has written yet.
`release` and `abort` are deliberately absent: `Fragment.release` documents why it is not
`detach`, the stores release a *claim* rather than themselves, and `abort` is two-phase
commit vocabulary paired with `prepare`/`apply`.
"""


SAME_CONCEPT_TWO_LAYERS = {
    # A semantic node and the exact primitive it lowers to. The parallel spelling is the
    # point: `sl.Heading` and what planning emits for it are the same idea, twice.
    "ActionGroup",
    "Code",
    "Heading",
    "Section",
    # Parallel settled-state vocabularies for the two async primitives.
    "Failed",
    "Pending",
    # `sl.forms.X` is the field form of the node named `sl.X`; the namespace disambiguates
    # and reading `forms.Text` as "a text field" is what plan 58's namespacing is for.
    "Choice",
    "Text",
    "Time",
}
"""Shared names that are deliberate: the same concept at two layers, or a namespaced form."""

UNRELATED_CONCEPTS_SHARING_A_WORD = {
    # `interactions.ActionKind` is the shape of a frontend interaction -- press, selection,
    # submit. `squid_reactive.actions.ActionKind` is why a transaction exists -- action, undo,
    # redo, compensation. Two senses of "action": the thing a person did, and the unit of work
    # that records it. Unrelated.
    "ActionKind",
    # `semantic.Destination(key, label, available)` is one option in a navigation control;
    # `delivery.Destination` is how a mount's message gets created. Unrelated.
    "Destination",
    # `semantic.Progress(value, label, maximum)` is a progress bar; `operations.Progress`
    # is the capability an operation reports through. Unrelated.
    "Progress",
}
"""Real collisions, recorded rather than renamed.

Both are in the authoring vocabulary, so renaming either is a public API decision with
taste in it rather than a mechanical fix. Listed here so the rule stays enforceable and
neither is forgotten before the package is published.
"""


def _exported_classes() -> dict[str, set[str]]:
    """Every class reachable through a package `__all__`, by short name to defining module."""
    found: dict[str, set[str]] = defaultdict(set)
    for package in (squid_layouts, squid_reactive, squid_replicated, squid_stores):
        modules: list[ModuleType] = [package]
        modules.extend(
            importlib.import_module(info.name)
            for info in pkgutil.walk_packages(package.__path__, f"{package.__name__}.")
        )
        for module in modules:
            for name in getattr(module, "__all__", ()):
                value = getattr(module, name, None)
                if inspect.isclass(value):
                    found[name].add(f"{value.__module__}.{value.__qualname__}")
    return found


def _classes_in_source() -> list[tuple[Path, ast.ClassDef]]:
    return [
        (path, node)
        for root in PACKAGE_SOURCE_ROOTS
        for path in root.rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ClassDef)
    ]


def _method_names(node: ast.ClassDef) -> set[str]:
    return {child.name for child in node.body if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)}


def test_one_public_name_means_one_class() -> None:
    """Two classes sharing a short name is the defect, whatever the word happens to be.

    `MountSnapshot` named both a view of a live mount and the serialized state that
    outlives it, and both were exported from `squid_layouts.discord`.
    """
    collisions = {name: sorted(where) for name, where in _exported_classes().items() if len(where) > 1}
    known = SAME_CONCEPT_TWO_LAYERS | UNRELATED_CONCEPTS_SHARING_A_WORD
    assert collisions.keys() == known, (
        "the set of names meaning two classes changed; justify a new one by adding it to "
        f"SAME_CONCEPT_TWO_LAYERS or UNRELATED_CONCEPTS_SHARING_A_WORD, or rename it. "
        f"new: {sorted(collisions.keys() - known)}, gone: {sorted(known - collisions.keys())}"
    )


def test_no_class_claims_to_end_itself_twice() -> None:
    """`close` and `finish` both mean "this object is done", so a class picks one."""
    offenders = [
        f"{path}::{node.name}"
        for path, node in _classes_in_source()
        if len(_method_names(node) & OBJECT_ENDING_VERBS) > 1
    ]
    assert not offenders, f"both close() and finish() on one class: {offenders}"


def test_termination_uses_the_agreed_verbs() -> None:
    """A seventh synonym for "it is over" puts the reader back where they started."""
    offenders = [
        f"{path}::{node.name}.{verb}"
        for path, node in _classes_in_source()
        for verb in sorted(_method_names(node) & DISCOURAGED_VERBS)
        if not verb.startswith("_")
    ]
    assert not offenders, f"use one of {sorted(TERMINATING_VERBS)} instead: {offenders}"
