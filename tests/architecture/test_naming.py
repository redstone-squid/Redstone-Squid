"""Naming rules that keep the vocabulary in `docs/squid-vocabulary.md` from eroding.

Lifetime is carried by verbs, not by nouns. What nouns owe instead is consistency, which
needs no dictionary: one meaning per word, and one word per meaning.

The rules here are denylists rather than allowlists, which is deliberate and measured. An
allowlist of legal suffixes was considered and rejected on the numbers: the six packages
export 555 classes with 273 distinct last words, 179 of them used exactly once, and
restricting to multi-word names only brings that to 173 and 102. A closed set would have to
reject `Mount`, `Chrome`, `Palette` and every authoring node -- `Heading`, `Paragraph`,
`Gallery` -- or grow an exemption list longer than the rule. A denylist scales to names
nobody has written yet, which is the same reasoning `DISCOURAGED_VERBS` was written under.

What is enforced is that the words the sweep retired stay retired, that an agent noun names
a verb the dictionary has, and that no prefix is a single unreadable letter.
"""

import ast
import importlib
import inspect
import pkgutil
import re
from collections import defaultdict
from pathlib import Path
from types import ModuleType

import squid_discord
import squid_ui
import squid_patterns
import squid_reactivity
import squid_replicated
import squid_stores

PACKAGE_SOURCE_ROOTS = (
    Path("packages/squid-ui/src"),
    Path("packages/squid-reactivity/src"),
    Path("packages/squid-replicated/src"),
    Path("packages/squid-stores/src"),
    Path("packages/squid-discord/src"),
    Path("packages/squid-patterns/src"),
)

TERMINATING_VERBS = frozenset({"close", "detach", "finish", "cancel", "discard", "run"})
"""What ends something, and nothing else. See `docs/squid-ui-architecture.md`."""

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

RETIRED_SUFFIXES = frozenset(
    {"Outcome", "Verdict", "Feedback", "Receipt", "Summary", "Locator", "Protection", "Strategy"}
)
"""Words that each meant what another word already meant, and what they retired into.

`Outcome` and `Receipt` are a `Result` or a `Status`; `Verdict` is a `Decision`; `Summary`
is a `Snapshot` of something live or a `Report` about something finished; `Locator` is an
`Address`; `Protection` is a `Policy`; `Strategy` is a `Mode` when it is an enum and a
`Policy` when it is injected; `Feedback` was never an answer at all and became a `Spec`.
"""

RETIRED_SUFFIX_EXEMPTIONS = frozenset({"Summary"})
"""`semantic.Summary` is the `<summary>` of a `Details` disclosure, not a suffix.

HTML's own `details`/`summary` pair is the term of art the node mirrors, and the head noun
carries the whole meaning. Listed rather than assumed, like every other exemption.
"""

RETIRED_IDENTIFIER_WORDS = frozenset({"feedback", "locator", "outcome", "protection", "reactor", "receipt", "verdict"})
"""Unambiguous retired words forbidden in functions, attributes, parameters, and locals.

Unlike `Summary`, `Strategy`, and `Policy`, these have no surviving second meaning in the
six packages. Checking identifiers closes the gap left by the first class-and-method sweep.
"""

RETIRED_IDENTIFIERS = frozenset({"summary_bytes", "summary_payload"})
"""Ambiguous retired words in durable identifiers whose complete spelling is unambiguous."""

ANNOTATION_VOCABULARY = {
    "ActionMode": frozenset({"policy"}),
    "ActionResult": frozenset({"outcome"}),
    "ChangeReport": frozenset({"summary"}),
    "ExceptionReport": frozenset({"summary"}),
    "Markup": frozenset({"dialect"}),
    "MountScheduler": frozenset({"reactor"}),
    "ReplacementPolicy": frozenset({"protect", "protection"}),
    "SessionSnapshot": frozenset({"summary"}),
    "UndoMode": frozenset({"strategy"}),
}
"""Words whose ambiguity disappears once an identifier's annotated type is known."""

RETIRED_VERBS = frozenset({"format_prefill", "list_records", "purge_expired", "drop", "allows", "refresh_now"})
"""Callable names that said in two words what one dictionary verb already said.

`flush` is deliberately absent: persistence `flush` names a different subject -- writing
pending bytes -- and `PersistedPool.flush` and `DurableSessionRuntime.flush` keep it. What
retired was `Mount.flush`, which delivered a render and is now `Mount.refresh`.
"""

AGENT_NOUNS = {
    "Adapter": "adapt",
    "Browser": "browse",
    "Converter": "convert",
    "Editor": "edit",
    "Inspector": "inspect",
    "Loader": "load",
    "Navigator": "navigate",
    "Planner": "plan",
    "Presenter": "present",
    "Profiler": "profile",
    "Reconciler": "reconcile",
    "Recorder": "record",
    "Renderer": "render",
    "Resolver": "resolve",
    "Responder": "respond",
    "Router": "route",
    "Runner": "run",
    "Scheduler": "schedule",
    "Supervisor": "supervise",
}
"""`Xer` performs the verb `x`, so the family is self-checking.

This is what caught `Reactor`: there is no verb `react`, and the class actually schedules
re-renders in response to topic traffic, so it is a `MountScheduler`.
"""

NOT_AGENT_NOUNS = frozenset(
    {
        # Plain nouns that happen to end in -er/-or. None of them performs a verb.
        "Actor",
        "Answer",
        "Author",
        "Cluster",
        "Counter",
        "Error",
        "Footer",
        "Ledger",
        "Never",
        "Opener",
        "Owner",
        "Pager",
        "Roster",
        "Separator",
        "Trigger",
    }
)
"""Words the `-er` rule must not read as derivations, listed so a new one is a decision."""


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
    # `sl.scene.X` is what `sl.X` lowers to. The `Scene` prefix that used to keep these
    # apart repeated the import path and said nothing else, so it was dropped; the pairing
    # is the whole point, and `sl.scene` is the namespace that disambiguates.
    "Asset",
    "Button",
    "Document",
    "EntitySelect",
    "Extension",
    "File",
    "Gallery",
    "GalleryItem",
    "Link",
    "Option",
    "Panel",
    "PremiumButton",
    "RoutedButton",
    "RoutedSelect",
    "Row",
    "Text",
    "Thumbnail",
    "Time",
    "ZonedTime",
}
"""Shared names that are deliberate: the same concept at two layers, or a namespaced form."""

UNRELATED_CONCEPTS_SHARING_A_WORD = {
    # `interactions.ActionKind` is the shape of a frontend interaction -- press, selection,
    # submit. `squid_reactivity.actions.ActionKind` is why a transaction exists -- action, undo,
    # redo, compensation. Two senses of "action": the thing a person did, and the unit of work
    # that records it. Unrelated.
    "ActionKind",
}
"""Real collisions, recorded rather than renamed.

`Destination` and `Progress` used to be here. Both were settled by renaming the semantic
node and leaving the load-bearing word alone: `semantic.Destination` is `NavOption` beside
`delivery.Destination`, and `semantic.Progress` is `ProgressBar` beside the capability an
operation reports through.
"""


def _exported_classes() -> dict[str, set[str]]:
    """Every class reachable through a package `__all__`, by short name to defining module."""
    found: dict[str, set[str]] = defaultdict(set)
    for package in (squid_discord, squid_ui, squid_patterns, squid_reactivity, squid_replicated, squid_stores):
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


def _functions_in_source() -> list[tuple[Path, ast.FunctionDef | ast.AsyncFunctionDef]]:
    return [
        (path, node)
        for root in PACKAGE_SOURCE_ROOTS
        for path in root.rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    ]


def _method_names(node: ast.ClassDef) -> set[str]:
    return {child.name for child in node.body if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)}


def test_one_public_name_means_one_class() -> None:
    """Two classes sharing a short name is the defect, whatever the word happens to be.

    `MountSnapshot` named both a view of a live mount and the serialized state that
    outlives it, and both were exported from `squid_discord`.
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


def _last_word(name: str) -> str:
    words = re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+", name)
    return words[-1] if words else name


def test_retired_words_stay_retired() -> None:
    """A synonym that came back would undo the sweep one class at a time.

    `Outcome` alone named a `Status` enum in profiling and a `Result` union in the reactive
    kernel, and nothing said they were different kinds of thing.
    """
    offenders = [
        f"{path}::{node.name}"
        for path, node in _classes_in_source()
        if _last_word(node.name) in RETIRED_SUFFIXES and node.name not in RETIRED_SUFFIX_EXEMPTIONS
    ]
    assert not offenders, f"these words retired into the suffix table; see docs/squid-vocabulary.md: {offenders}"


def test_retired_verbs_stay_retired() -> None:
    """Public, private, module, and nested callables share the verb dictionary."""
    offenders = [f"{path}::{node.name}" for path, node in _functions_in_source() if node.name in RETIRED_VERBS]
    assert not offenders, f"use the dictionary verb instead; see docs/squid-vocabulary.md: {offenders}"


def _identifier_words(name: str) -> set[str]:
    return {
        word.lower()
        for part in name.strip("_").split("_")
        for word in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z0-9]+", part)
    }


def _annotation_names(annotation: ast.expr | None) -> set[str]:
    if annotation is None:
        return set()
    return {node.id for node in ast.walk(annotation) if isinstance(node, ast.Name)}


def test_retired_identifier_words_stay_retired() -> None:
    """The noun reset reaches functions, attributes, parameters, and private locals."""
    offenders: list[str] = []
    seen: set[tuple[Path, str]] = set()
    for root in PACKAGE_SOURCE_ROOTS:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                name = None
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                    name = node.name
                elif isinstance(node, ast.Name):
                    name = node.id
                elif isinstance(node, ast.Attribute):
                    name = node.attr
                elif isinstance(node, ast.arg):
                    name = node.arg
                if name is None or (
                    name not in RETIRED_IDENTIFIERS and not (_identifier_words(name) & RETIRED_IDENTIFIER_WORDS)
                ):
                    continue
                key = (path, name)
                if key not in seen:
                    seen.add(key)
                    offenders.append(f"{path}::{name}")
    assert not offenders, f"retired words remain in identifiers: {sorted(offenders)}"


def test_annotated_identifiers_follow_their_type_vocabulary() -> None:
    """A typed value uses the same subject word as the class that defines its meaning."""
    offenders: list[str] = []
    for root in PACKAGE_SOURCE_ROOTS:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                pairs: list[tuple[str, ast.expr | None]] = []
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    pairs.append((node.name, node.returns))
                    arguments = (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
                    pairs.extend((argument.arg, argument.annotation) for argument in arguments)
                    if node.args.vararg is not None:
                        pairs.append((node.args.vararg.arg, node.args.vararg.annotation))
                    if node.args.kwarg is not None:
                        pairs.append((node.args.kwarg.arg, node.args.kwarg.annotation))
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name | ast.Attribute):
                    target = node.target.id if isinstance(node.target, ast.Name) else node.target.attr
                    pairs.append((target, node.annotation))
                for name, annotation in pairs:
                    words = _identifier_words(name)
                    for type_name in _annotation_names(annotation):
                        retired = ANNOTATION_VOCABULARY.get(type_name, frozenset())
                        if words & retired:
                            offenders.append(f"{path}::{name}: {type_name}")
    assert not offenders, f"identifiers disagree with their annotated types: {sorted(offenders)}"


def test_constructor_assigned_identifiers_follow_their_type_vocabulary() -> None:
    """An unannotated value named by its constructor still follows that type vocabulary."""
    offenders: list[str] = []
    for root in PACKAGE_SOURCE_ROOTS:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                    continue
                constructor = node.value.func
                type_name = constructor.id if isinstance(constructor, ast.Name) else None
                if isinstance(constructor, ast.Attribute):
                    type_name = constructor.attr
                retired = ANNOTATION_VOCABULARY.get(type_name or "", frozenset())
                if not retired:
                    continue
                for target in node.targets:
                    if not isinstance(target, ast.Name | ast.Attribute):
                        continue
                    name = target.id if isinstance(target, ast.Name) else target.attr
                    if _identifier_words(name) & retired:
                        offenders.append(f"{path}::{name}: {type_name}")
    assert not offenders, f"constructor-assigned identifiers disagree with their types: {sorted(offenders)}"


def test_agent_nouns_name_a_verb_the_dictionary_has() -> None:
    """`Xer` is a derivation, not a free word: it claims to perform `x`.

    A new `-er` class is therefore a decision -- either it performs a listed verb, or it is
    an ordinary noun that happens to end in those letters and says so in `NOT_AGENT_NOUNS`.
    """
    unclassified = sorted(
        {
            _last_word(name)
            for name in _exported_classes()
            if (last := _last_word(name)).endswith(("er", "or"))
            and len(last) > 3
            and last not in AGENT_NOUNS
            and last not in NOT_AGENT_NOUNS
        }
    )
    assert not unclassified, (
        "an -er name must name a verb it performs (add it to AGENT_NOUNS) or say it is not "
        f"an agent noun (add it to NOT_AGENT_NOUNS): {unclassified}"
    )


def test_no_single_letter_prefixes() -> None:
    """`RText` and `RPanel` spent a word on a letter; the word is `Measured`."""
    offenders = [
        f"{path}::{node.name}" for path, node in _classes_in_source() if re.match(r"^[A-Z][A-Z][a-z]", node.name)
    ]
    assert not offenders, f"spell the prefix as a word: {offenders}"
