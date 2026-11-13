"""Compile template structure to Fluent without parsing captured values."""

import ast as py_ast
import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from textwrap import dedent, indent

from fluent.syntax import FluentParser, FluentSerializer, ast
from fluent.syntax.visitor import Transformer, Visitor

from squid_i18n.errors import CatalogError, DeclarationError
from squid_i18n.expressions import DEVELOPER_OPTIONS, OPTION_NAMES, Option


@dataclass(frozen=True, slots=True)
class Slot:
    name: str
    function: str | None = None
    options: tuple[tuple[str, Option], ...] = ()


@dataclass(frozen=True, slots=True)
class Declaration:
    identifier: str
    source: str
    slots: tuple[Slot, ...]
    attributes: frozenset[str]
    has_value: bool
    fingerprint: str


class Variables(Visitor):
    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_VariableReference(self, node: ast.VariableReference) -> None:
        self.names.add(node.id.name)


def parse_resource(source: str) -> ast.Resource:
    resource = FluentParser().parse(source)
    seen: set[str] = set()
    for entry in resource.body:
        if isinstance(entry, ast.Junk):
            detail = "; ".join(annotation.message or annotation.code for annotation in entry.annotations)
            raise CatalogError(detail)
        if isinstance(entry, ast.Message | ast.Term):
            key = ("-" if isinstance(entry, ast.Term) else "") + entry.id.name
            if key in seen:
                msg = f"Duplicate Fluent identifier: {key}"
                raise CatalogError(msg)
            seen.add(key)
            attributes = [attribute.id.name for attribute in entry.attributes]
            if len(attributes) != len(set(attributes)):
                msg = f"Duplicate Fluent attribute in {key}"
                raise CatalogError(msg)
    return resource


def _leaf(node: py_ast.expr) -> str:
    if isinstance(node, py_ast.NamedExpr):
        node = node.target
    if not isinstance(node, py_ast.Name):
        msg = "Name interpolation values with a local variable or a parenthesized walrus expression"
        raise DeclarationError(msg)
    return node.id if re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]*", node.id) else "v" + node.id.encode().hex()


def expression_slot(expression: str) -> Slot:
    node = py_ast.parse(expression, mode="eval").body
    alias = None
    if isinstance(node, py_ast.NamedExpr):
        alias = _leaf(node)
        node = node.value
    function = node.func.id if isinstance(node, py_ast.Call) and isinstance(node.func, py_ast.Name) else None
    if function not in {"NUMBER", "DATETIME"}:
        return Slot(alias or _leaf(node))
    assert isinstance(node, py_ast.Call)
    if len(node.args) != 1:
        msg = "Formatting constructors require exactly one named value"
        raise DeclarationError(msg)
    options: list[tuple[str, Option]] = []
    for keyword in node.keywords:
        allowed = {"date_style", "time_style", "time_zone"}
        if function == "NUMBER":
            allowed = OPTION_NAMES.keys() - allowed
        if keyword.arg not in allowed:
            msg = f"Unknown {function} option: {keyword.arg}"
            raise DeclarationError(msg)
        try:
            value = py_ast.literal_eval(keyword.value)
        except (ValueError, TypeError) as error:
            msg = "Formatting options must be literal values"
            raise DeclarationError(msg) from error
        if not isinstance(value, str | int | bool):
            msg = "Formatting options must be string, integer, or boolean literals"
            raise DeclarationError(msg)
        options.append((OPTION_NAMES[keyword.arg], value))
    return Slot(alias or _leaf(node.args[0]), function, tuple(options))


class _BindSlots(Transformer):
    def __init__(self, slots: dict[str, Slot]) -> None:
        self.slots = slots
        self.used: set[str] = set()
        self.tokens = re.compile("(" + "|".join(re.escape("$" + key) for key in slots) + ")" if slots else r"(?!)")

    def _expression(self, key: str) -> ast.InlineExpression:
        self.used.add(key)
        slot = self.slots[key]
        variable = ast.VariableReference(ast.Identifier(slot.name))
        if slot.function is None:
            return variable
        options = [
            ast.NamedArgument(
                ast.Identifier(name),
                ast.StringLiteral(value) if isinstance(value, str) else ast.NumberLiteral(str(int(value))),
            )
            for name, value in slot.options
            if name not in DEVELOPER_OPTIONS
        ]
        return ast.FunctionReference(ast.Identifier(slot.function), ast.CallArguments([variable], options))

    def visit_Pattern(self, node: ast.Pattern) -> ast.Pattern:
        elements: list[ast.TextElement | ast.Placeable] = []
        for element in node.elements:
            if isinstance(element, ast.TextElement):
                for part in self.tokens.split(element.value):
                    if part.startswith("$") and part[1:] in self.slots:
                        elements.append(ast.Placeable(self._expression(part[1:])))
                    elif part:
                        elements.append(ast.TextElement(part))
            else:
                elements.append(self.visit(element))
        node.elements = elements
        return node

    def visit_VariableReference(self, node: ast.VariableReference) -> ast.InlineExpression:
        return self._expression(node.id.name) if node.id.name in self.slots else node


@lru_cache(maxsize=2048)
def compile_template(strings: tuple[str, ...], expressions: tuple[str, ...], key: str | None = None) -> Declaration:
    slots = tuple(expression_slot(expression) for expression in expressions)
    prefix = "squidSlot"
    while any(prefix in text for text in strings):
        prefix += "X"
    markers = {f"{prefix}{index}End": slot for index, slot in enumerate(slots)}
    source = "".join(text + "$" + key for text, key in zip(strings, markers, strict=False)) + strings[-1]
    if key is not None:
        if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]*", key):
            msg = "Invalid translation key"
            raise DeclarationError(msg)
        if re.match(r"\s*[a-zA-Z][a-zA-Z0-9_-]*\s*=", source):
            msg = "Supply the translation key either in the declaration or with key=, not both"
            raise DeclarationError(msg)
        body = dedent(source).strip()
        # Fluent treats a leading '*' on a continuation line as a variant marker.
        # Keep the first value line beside '=' just as an embedded declaration does.
        if re.match(r"\.[a-zA-Z][a-zA-Z0-9_-]*\s*=", body):
            source = key + " =\n" + indent(body, "    ")
        else:
            source = key + " = " + indent(body, "    ").removeprefix("    ")
    resource = parse_resource(dedent(source).strip() + "\n")
    entries = [node for node in resource.body if isinstance(node, ast.Message | ast.Term)]
    if len(entries) != 1 or not isinstance(entries[0], ast.Message):
        msg = "tr() requires one named Fluent message, optionally with attributes"
        raise DeclarationError(msg)
    entry = entries[0]
    binder = _BindSlots(markers)
    binder.visit(entry)
    if binder.used != markers.keys():
        msg = "Interpolations must be values or expressions, not IDs, attributes, or quoted literals"
        raise DeclarationError(msg)
    variables = Variables()
    variables.visit(entry)
    if variables.names - {slot.name for slot in slots}:
        msg = "Every Fluent variable must have a captured Python binding"
        raise DeclarationError(msg)
    developer: dict[str, tuple[tuple[str, Option], ...]] = {}
    for slot in slots:
        options = tuple(
            sorted(
                (name, value)
                for name, value in slot.options
                if name in DEVELOPER_OPTIONS and (name, value) != ("style", "decimal")
            )
        )
        if slot.name in developer and developer[slot.name] != options:
            msg = f"Conflicting developer options for {slot.name}; give the value a separate name"
            raise DeclarationError(msg)
        developer[slot.name] = options
    canonical = FluentSerializer().serialize(ast.Resource([entry]))
    digest = hashlib.sha256((canonical + repr(sorted(developer.items()))).encode()).hexdigest()
    return Declaration(
        entry.id.name,
        canonical,
        slots,
        frozenset(item.id.name for item in entry.attributes),
        entry.value is not None,
        digest,
    )
