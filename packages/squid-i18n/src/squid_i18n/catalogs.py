"""Extract declarations without importing application code, and validate translations.

A distribution owns its English Fluent resource and a manifest of captured argument
names and source revisions. Translations may omit entries or attributes; resolution
then uses English. Supplied entries must compile and may only read captured values.
"""

import ast as python
import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

from fluent.syntax import ast
from fluent.syntax.visitor import Visitor

from squid_i18n.compiler import Declaration, compile_template, parse_resource
from squid_i18n.errors import CatalogError, DeclarationError
from squid_i18n.expressions import DEVELOPER_OPTIONS
from squid_i18n.runtime import compile_catalog


@dataclass(frozen=True)
class LocatedDeclaration:
    declaration: Declaration
    location: str


def extract(source: str, filename: str = "<source>") -> list[LocatedDeclaration]:
    """Read literal tr(...) calls, including imported aliases, without executing them."""
    tree = python.parse(source, filename)
    names = {"tr", "md_tr"}
    for node in python.walk(tree):
        if isinstance(node, python.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names if alias.name in {"tr", "md_tr"})
    found: list[LocatedDeclaration] = []
    for node in python.walk(tree):
        if not isinstance(node, python.Call):
            continue
        name = node.func.id if isinstance(node.func, python.Name) else None
        if name not in names:
            continue
        location = f"{filename}:{node.lineno}"
        if len(node.args) != 1 or any(keyword.arg not in {"key", "markdown"} for keyword in node.keywords):
            msg = f"{location}: use one literal declaration with optional literal key= and markdown="
            raise DeclarationError(msg)
        key = None
        for keyword in node.keywords:
            if keyword.arg == "key":
                if not isinstance(keyword.value, python.Constant) or not isinstance(keyword.value.value, str):
                    msg = f"{location}: key= must be a literal string"
                    raise DeclarationError(msg)
                key = keyword.value.value
            elif not isinstance(keyword.value, python.Constant) or not isinstance(keyword.value.value, bool):
                msg = f"{location}: markdown= must be a literal bool"
                raise DeclarationError(msg)
        argument = node.args[0]
        if isinstance(argument, python.Constant) and isinstance(argument.value, str):
            parts = [argument]
        elif isinstance(argument, python.TemplateStr):
            parts = argument.values
        else:
            msg = f"{location}: use a literal string or t-string declaration"
            raise DeclarationError(msg)
        strings = [""]
        expressions: list[str] = []
        for part in parts:
            if isinstance(part, python.Constant):
                strings[-1] += str(part.value)
            elif isinstance(part, python.Interpolation):
                if part.conversion != -1 or part.format_spec is not None:
                    msg = f"{location}: use NUMBER or DATETIME for formatting"
                    raise DeclarationError(msg)
                expressions.append(part.str or python.unparse(part.value))
                strings.append("")
        try:
            declaration = compile_template(tuple(strings), tuple(expressions), key)
        except (CatalogError, DeclarationError) as error:
            msg = f"{location}: {error}"
            raise DeclarationError(msg) from error
        found.append(LocatedDeclaration(declaration, location))
    return found


def collect(paths: Iterable[Path]) -> list[LocatedDeclaration]:
    found: list[LocatedDeclaration] = []
    for path in sorted(paths):
        found.extend(extract(path.read_text(), str(path)))
    return found


def artifacts(found: Iterable[LocatedDeclaration], supplemental: tuple[str, ...] = ()) -> tuple[str, str]:
    """Return deterministic English FTL and JSON binding metadata."""
    entries: dict[str, Declaration] = {}
    locations: dict[str, set[str]] = {}
    for item in found:
        declaration = item.declaration
        key = declaration.identifier
        if key in entries and entries[key].fingerprint != declaration.fingerprint:
            msg = f"Conflicting declaration for {key}: {item.location}"
            raise CatalogError(msg)
        entries[key] = declaration
        locations.setdefault(key, set()).add(item.location)
    source = "".join(
        "# " + ", ".join(sorted(locations[key])) + "\n" + value.source + "\n" for key, value in sorted(entries.items())
    )
    source = "\n".join(line if line.strip() else "" for line in source.splitlines()).rstrip() + "\n" if source else ""
    metadata = {
        key: {
            "fingerprint": value.fingerprint,
            "attributes": sorted(value.attributes),
            "has_value": value.has_value,
            "arguments": [asdict(slot) for slot in dict.fromkeys(value.slots)],
            "locations": sorted(locations[key]),
        }
        for key, value in sorted(entries.items())
    }
    validate("en", (source, *supplemental), entries.values())
    return source, json.dumps(metadata, indent=2, ensure_ascii=False) + "\n"


class _Dependencies(Visitor):
    def __init__(self) -> None:
        self.variables: set[str] = set()
        self.references: set[str] = set()
        self.functions: list[ast.FunctionReference] = []

    def visit_VariableReference(self, node: ast.VariableReference) -> None:
        self.variables.add(node.id.name)

    def visit_MessageReference(self, node: ast.MessageReference) -> None:
        self.references.add(node.id.name + ("." + node.attribute.name if node.attribute else ""))

    def visit_TermReference(self, node: ast.TermReference) -> None:
        # Terms have their own argument scope. The compiler checks their references.
        if node.arguments is not None:
            self.visit(node.arguments)

    def visit_FunctionReference(self, node: ast.FunctionReference) -> None:
        self.functions.append(node)
        self.visit(node.arguments)


def validate(locale: str, resources: tuple[str, ...], declarations: Iterable[Declaration]) -> None:
    """Reject syntax, reference, cycle, function and external-variable errors."""
    compiled = compile_catalog(locale, resources)
    del compiled
    dependencies: dict[str, _Dependencies] = {}
    for resource in resources:
        for entry in parse_resource(resource).body:
            if not isinstance(entry, ast.Message | ast.Term):
                continue
            key = ("-" if isinstance(entry, ast.Term) else "") + entry.id.name
            patterns = [(key, entry.value)] + [(key + "." + attr.id.name, attr.value) for attr in entry.attributes]
            for name, pattern in patterns:
                if pattern is None:
                    continue
                dependency = _Dependencies()
                dependency.visit(pattern)
                dependencies[name] = dependency
                for function in dependency.functions:
                    if function.id.name not in {"NUMBER", "DATETIME"}:
                        msg = f"{name}: unsupported function {function.id.name}"
                        raise CatalogError(msg)
                    forbidden = {option.name.name for option in function.arguments.named} & DEVELOPER_OPTIONS
                    if forbidden:
                        msg = f"{name}: developer options cannot be changed in Fluent: {sorted(forbidden)}"
                        raise CatalogError(msg)

    def variables(key: str, seen: frozenset[str] = frozenset()) -> set[str]:
        if key in seen or key not in dependencies:
            return set()
        dependency = dependencies[key]
        return dependency.variables | set().union(*(variables(ref, seen | {key}) for ref in dependency.references))

    for declaration in declarations:
        keys = [declaration.identifier] + [declaration.identifier + "." + attr for attr in declaration.attributes]
        allowed = {slot.name for slot in declaration.slots}
        for key in keys:
            missing = variables(key) - allowed
            if missing:
                msg = f"{key}: uncaptured arguments: {', '.join(sorted(missing))}"
                raise CatalogError(msg)
