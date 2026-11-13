"""Check naming conventions from `docs/squid-vocabulary.md`.

The checks reject selected retired names and track deliberate exceptions. They do not
restrict every class suffix to a fixed vocabulary: domain names such as Heading and
Palette should not need an exception just because they are new to the checker.
Exported names are inspected at runtime; source-level checks also cover private and
nested definitions.
"""

import ast
import importlib
import inspect
import pkgutil
import re
from collections import defaultdict
from pathlib import Path
from types import ModuleType

import squid_reactivity
import squid_replication
import squid_storage
import squid_ui
import squid_ui_discord
import squid_ui_slack
import squid_ui_widgets
from tests.support.source_tree import source_tree

PACKAGE_SOURCE_ROOTS = (
    Path("packages/squid-ui/src"),
    Path("packages/squid-reactivity/src"),
    Path("packages/squid-replication/src"),
    Path("packages/squid-storage/src"),
    Path("packages/squid-ui-discord/src"),
    Path("packages/squid-ui-slack/src"),
    Path("packages/squid-ui-widgets/src"),
)

PUBLIC_PACKAGES = (
    squid_ui_discord,
    squid_ui_slack,
    squid_ui,
    squid_ui_widgets,
    squid_reactivity,
    squid_replication,
    squid_storage,
)

UI_PUBLIC_PACKAGES = (
    squid_ui_discord,
    squid_ui_slack,
    squid_ui,
    squid_ui_widgets,
    squid_reactivity,
)

TERMINATING_VERBS = frozenset({"close", "detach", "finish", "cancel", "discard", "run"})
"""Preferred verbs for the distinct lifetime operations described in `docs/squid-ui-architecture.md`."""

OBJECT_ENDING_VERBS = frozenset({"close", "finish"})
"""A class may expose either `close` or `finish` to end its own lifetime.

Other verbs can name separate work: `cancel` ends unfinished async work and `discard`
ends staged changes. Those operations may coexist with `close`.
"""

DISCOURAGED_VERBS = frozenset({"shutdown", "stop", "dispose", "teardown", "destroy", "terminate", "kill"})
"""Alternative lifetime verbs that the package conventions exclude.

`release` and `abort` remain available for operations such as releasing a claim or
aborting a prepared transaction, which need not end the object itself.
"""

RETIRED_SUFFIXES = frozenset(
    {"Outcome", "Verdict", "Feedback", "Receipt", "Summary", "Locator", "Protection", "Strategy"}
)
"""Class suffixes replaced by more specific vocabulary.

Use `Result` or `Status` for `Outcome` and `Receipt`, and `Decision` for `Verdict`.
A `Summary` becomes a live `Snapshot` or a completed `Report`; `Locator` becomes
`Address`; `Protection` becomes `Policy`. Use `Mode` for an enumerated `Strategy`
or `Policy` for an injected one. The former `Feedback` concept is now a `Spec`.
"""

RETIRED_SUFFIX_EXEMPTIONS = frozenset({"Summary"})
"""`semantic.Summary` mirrors the HTML `<summary>` element inside a `Details` disclosure."""

RETIRED_IDENTIFIER_WORDS = frozenset({"feedback", "locator", "protection", "reactor", "receipt", "verdict"})
"""Retired words checked in definition names, attributes, parameters, and local references.

`outcome` names a Screen's completed-or-cancelled value; the generic Outcome class suffix
remains retired. Result events carry only accepted values, so those names are distinct.
"""

RETIRED_IDENTIFIERS = frozenset({"summary_bytes", "summary_payload"})
"""Exact retired identifiers whose individual words remain valid elsewhere."""

RETIRED_CLASS_NAMES = frozenset(
    {
        "Action",
        "ActionGroup",
        "Composition",
        "Destination",
        "DiscordLimits",
        "DiscordMode",
        "DiscordModeError",
        "DiscordPresentation",
        "DiscordReservation",
        "HostSource",
        "LayoutHost",
        "LayoutHostMissing",
        "Measure",
        "Mount",
        "MountAddress",
        "MountDefaults",
        "MountInspection",
        "MountOptions",
        "MountScheduler",
        "MountSnapshot",
        "MountState",
        "Navigator",
        "Opener",
        "OpeningRequest",
        "ScreenOptionsResolver",
        "ScreenSpec",
        "SessionPolicy",
        "SessionRegistry",
    }
)
"""Exact retired class names. Their component words may still be valid in other identifiers."""

RETIRED_PACKAGE_IMPORTS = frozenset(
    {
        "squid_components",
        "squid_crdt",
        "squid_discord",
        "squid_layouts",
        "squid_persistence",
        "squid_reactive",
    }
)

RETIRED_DISCORD_MODULES = frozenset({"composition", "host", "mount", "presentation", "screens"})

ANNOTATION_VOCABULARY = {
    "AdmissionSpec": frozenset({"policy"}),
    "ActionMode": frozenset({"policy"}),
    "ActionStatus": frozenset({"status"}),
    "ChangeReport": frozenset({"summary"}),
    "ExceptionReport": frozenset({"summary"}),
    "Markup": frozenset({"dialect"}),
    "MessagePayload": frozenset({"presentation"}),
    "MessageRoot": frozenset({"mount"}),
    "MessageRootScheduler": frozenset({"reactor"}),
    "OpenContext": frozenset({"opener"}),
    "ReplacementPolicy": frozenset({"protect", "protection"}),
    "RenderedMessage": frozenset({"composition"}),
    "SessionManager": frozenset({"registry"}),
    "SessionSpec": frozenset({"screen"}),
    "SessionSnapshot": frozenset({"summary"}),
}
"""Forbidden identifier words for selected annotation or constructor names."""

ANNOTATION_IDENTIFIER_EXEMPTIONS = frozenset({("MessageRoot", "mount")})
"""Deliberate verb uses that happen to return the noun their action creates."""

RETIRED_VERBS = frozenset({"format_prefill", "list_records", "purge_expired", "drop", "allows", "refresh_now"})
"""Retired callable names, checked at every nesting level.

`flush` remains valid for writing pending state. Message delivery uses `refresh`,
but this check does not ban persistence methods named `flush`.
"""

AGENT_NOUNS = {
    "Adapter": "adapt",
    "Browser": "browse",
    "Converter": "convert",
    "Decorator": "decorate",
    "Driver": "drive",
    "Editor": "edit",
    "Holder": "hold",
    "Inspector": "inspect",
    "Loader": "load",
    "Manager": "manage",
    "Navigator": "navigate",
    "Planner": "plan",
    "Picker": "pick",
    "Presenter": "present",
    "Profiler": "profile",
    "Reconciler": "reconcile",
    "Recorder": "record",
    "Reporter": "report",
    "Renderer": "render",
    "Resolver": "resolve",
    "Responder": "respond",
    "Router": "route",
    "Runner": "run",
    "Scheduler": "schedule",
    "Supervisor": "supervise",
}
"""Approved class suffixes and the operations their names describe."""

NOT_AGENT_NOUNS = frozenset(
    {
        # These nouns are accepted without an entry in AGENT_NOUNS.
        "Actor",
        "Answer",
        "Author",
        "Cluster",
        "Counter",
        "Divider",
        # Python's own protocol term: a descriptor is what the object *is*, not a doer.
        "Descriptor",
        "Error",
        "Footer",
        "Header",
        "Ledger",
        "Never",
        "OpenContext",
        "Owner",
        "Pager",
        # "One expanded render" is this codebase's own phrase for the noun (see
        # `runtime.component.ComponentTree`); a Render does not render anything.
        "Render",
        "Roster",
        "Separator",
        "Trigger",
    }
)
"""Accepted suffixes exempt from the operation-name mapping."""


SAME_CONCEPT_TWO_LAYERS = {
    "Choice",  # A labelled option in semantic UI and Discord command metadata.
    # Reactive operations and Discord screens each expose a cancellation arm. Their
    # namespaces distinguish operation progress from a screen's cancellation reason.
    "Cancelled",
    # Semantic nodes and their planned primitives share names across namespaces.
    "ControlGroup",
    "Code",
    "Heading",
    "Section",
    # Widget components wrap the same-named state machines in the machines namespace.
    "Tabs",
    "Wizard",
    # Parallel settled-state vocabularies for the two async primitives.
    "Failed",
    "Pending",
    # Each transport's complete message kwargs. The package namespace selects the wire
    # protocol; both values carry rendered output to message delivery.
    "MessagePayload",
    # Authoring nodes and their scene equivalents use their namespaces for distinction.
    "Asset",
    "Button",
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
    # Discord command groups and semantic layout groups retain their domain names.
    "Group",
}
"""Deliberate name collisions for concepts distinguished by their package namespaces."""


def _exported_classes() -> dict[str, set[str]]:
    """Map each class name listed in a package module's `__all__` to its defining locations."""
    found: dict[str, set[str]] = defaultdict(set)
    for package in PUBLIC_PACKAGES:
        modules: list[ModuleType] = [package]
        modules.extend(
            importlib.import_module(info.name)
            for info in pkgutil.walk_packages(package.__path__, f"{package.__name__}.")
        )
        for module in modules:
            # The explicit internals bridge exports implementation types for sibling packages.
            if module.__name__.endswith(".internals"):
                continue
            for name in getattr(module, "__all__", ()):
                value = getattr(module, name, None)
                if inspect.isclass(value):
                    found[name].add(f"{value.__module__}.{value.__qualname__}")
    return found


def test_ui_public_callables_have_docstrings() -> None:
    """Exported UI classes and functions must have a docstring, including inherited docstrings."""
    missing: set[str] = set()
    for package in UI_PUBLIC_PACKAGES:
        modules: list[ModuleType] = [package]
        modules.extend(
            importlib.import_module(info.name)
            for info in pkgutil.walk_packages(package.__path__, f"{package.__name__}.")
        )
        for module in modules:
            for name in getattr(module, "__all__", ()):
                value = getattr(module, name, None)
                if (inspect.isclass(value) or inspect.isfunction(value)) and inspect.getdoc(value) is None:
                    missing.add(f"{value.__module__}.{value.__qualname__}")
    assert not missing, f"exported UI callables without docstrings: {sorted(missing)}"


def _classes_in_source() -> list[tuple[Path, ast.ClassDef]]:
    return [
        (path, node)
        for root in PACKAGE_SOURCE_ROOTS
        for path in root.rglob("*.py")
        for node in ast.walk(source_tree(path))
        if isinstance(node, ast.ClassDef)
    ]


def _functions_in_source() -> list[tuple[Path, ast.FunctionDef | ast.AsyncFunctionDef]]:
    return [
        (path, node)
        for root in PACKAGE_SOURCE_ROOTS
        for path in root.rglob("*.py")
        for node in ast.walk(source_tree(path))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    ]


def _method_names(node: ast.ClassDef) -> set[str]:
    return {child.name for child in node.body if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)}


def test_one_public_name_means_one_class() -> None:
    """Require an explicit exception when exported classes share a short name."""
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
    """Reject the listed alternative lifetime verbs on classes."""
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
    """Reject retired class suffixes except the explicitly allowed names."""
    offenders = [
        f"{path}::{node.name}"
        for path, node in _classes_in_source()
        if _last_word(node.name) in RETIRED_SUFFIXES and node.name not in RETIRED_SUFFIX_EXEMPTIONS
    ]
    assert not offenders, f"these words retired into the suffix table; see docs/squid-vocabulary.md: {offenders}"


def test_replaced_concept_names_stay_retired() -> None:
    """Exact old concepts cannot return while their ordinary words remain available."""
    offenders = [f"{path}::{node.name}" for path, node in _classes_in_source() if node.name in RETIRED_CLASS_NAMES]
    assert not offenders, f"use the current concept vocabulary: {offenders}"


def test_replaced_package_and_module_names_stay_retired() -> None:
    """Imports and physical Discord modules use the same current package vocabulary."""
    imported: list[str] = []
    for root in PACKAGE_SOURCE_ROOTS:
        for path in root.rglob("*.py"):
            tree = source_tree(path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.extend(alias.name.split(".", maxsplit=1)[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module is not None:
                    imported.append(node.module.split(".", maxsplit=1)[0])
    old_imports = sorted(set(imported) & RETIRED_PACKAGE_IMPORTS)
    discord_root = Path("packages/squid-ui-discord/src/squid_ui_discord")
    old_modules = sorted(path.stem for path in discord_root.glob("*.py") if path.stem in RETIRED_DISCORD_MODULES)
    assert not old_imports, f"use the renamed package roots: {old_imports}"
    assert not old_modules, f"use the renamed Discord modules: {old_modules}"


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
    return {
        node.id if isinstance(node, ast.Name) else node.attr
        for node in ast.walk(annotation)
        if isinstance(node, ast.Name | ast.Attribute)
    }


def test_retired_identifier_words_stay_retired() -> None:
    """Reject retired words and exact names across definitions and identifier references."""
    offenders: list[str] = []
    seen: set[tuple[Path, str]] = set()
    for root in PACKAGE_SOURCE_ROOTS:
        for path in root.rglob("*.py"):
            tree = source_tree(path)
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
            tree = source_tree(path)
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
                        if (type_name, name) in ANNOTATION_IDENTIFIER_EXEMPTIONS:
                            continue
                        retired = ANNOTATION_VOCABULARY.get(type_name, frozenset())
                        if words & retired:
                            offenders.append(f"{path}::{name}: {type_name}")
    assert not offenders, f"identifiers disagree with their annotated types: {sorted(offenders)}"


def test_constructor_assigned_identifiers_follow_their_type_vocabulary() -> None:
    """An unannotated value named by its constructor still follows that type vocabulary."""
    offenders: list[str] = []
    for root in PACKAGE_SOURCE_ROOTS:
        for path in root.rglob("*.py"):
            tree = source_tree(path)
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
    """Require an explicit classification for exported class suffixes ending in `-er` or `-or`."""
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
    """Reject class names beginning with two capitals followed by a lowercase letter."""
    offenders = [
        f"{path}::{node.name}" for path, node in _classes_in_source() if re.match(r"^[A-Z][A-Z][a-z]", node.name)
    ]
    assert not offenders, f"spell the prefix as a word: {offenders}"
