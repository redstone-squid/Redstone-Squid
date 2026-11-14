"""Bounded polynomial parsing and exact arithmetic for door expansion facts.

The parser accepts decimal and integer constants, declared parameter names,
+ - * /, powers from zero through three, and implicit multiplication such as
20n or (x+3)(y+3). Division requires a nonzero constant. No source code is run.
Domains additionally accept comparisons, and/or/not, semicolons as conjunctions,
and integer congruences. Unsupported source syntax stays in the source document.
"""

import ast
import re
from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction
from itertools import pairwise
from math import prod
from typing import Literal

MAX_DEGREE = 3
MAX_TERMS = 64
MAX_BITS = 256
_TOKEN = re.compile(r"\d+(?:\.\d+)?|[A-Za-z_][A-Za-z_0-9]*|\*\*|<=|>=|==|!=|[()+*/^%<>=;-]")
_NAME = re.compile(r"[A-Za-z_][A-Za-z_0-9]*\Z")
_KEYWORDS = frozenset({"and", "or", "not"})


class ExpressionError(ValueError):
    """Source syntax or arithmetic exceeds the supported computational grammar."""


@dataclass(frozen=True, slots=True)
class Polynomial:
    """Canonical sparse terms, ordered by exponent tuple, with rational coefficients."""

    variables: tuple[str, ...]
    terms: tuple[tuple[tuple[int, ...], Fraction], ...]

    @classmethod
    def create(cls, variables: tuple[str, ...], terms: Mapping[tuple[int, ...], Fraction]) -> Polynomial:
        nonzero = tuple(sorted((powers, value) for powers, value in terms.items() if value))
        if len(nonzero) > MAX_TERMS or any(sum(powers) > MAX_DEGREE for powers, _ in nonzero):
            msg = "Polynomial exceeds degree or term limits."
            raise ExpressionError(msg)
        if any(
            max(abs(value.numerator).bit_length(), value.denominator.bit_length()) > MAX_BITS for _, value in nonzero
        ):
            msg = "Polynomial coefficient exceeds the arithmetic limit."
            raise ExpressionError(msg)
        return cls(variables, nonzero)

    @classmethod
    def constant(cls, variables: tuple[str, ...], value: Fraction) -> Polynomial:
        return cls.create(variables, {(0,) * len(variables): value})

    @property
    def degree(self) -> int:
        return max((sum(powers) for powers, _ in self.terms), default=0)

    def evaluate(self, values: tuple[int, ...]) -> Fraction:
        if len(values) != len(self.variables):
            msg = "A value is required for every polynomial variable."
            raise ExpressionError(msg)
        return sum(
            (
                coefficient * prod(value**power for value, power in zip(values, powers, strict=True))
                for powers, coefficient in self.terms
            ),
            Fraction(0),
        )

    def add(self, other: Polynomial) -> Polynomial:
        terms = dict(self.terms)
        for powers, value in other.terms:
            terms[powers] = terms.get(powers, Fraction(0)) + value
        return self.create(self.variables, terms)

    def scale(self, scalar: Fraction) -> Polynomial:
        return self.create(self.variables, {powers: value * scalar for powers, value in self.terms})

    def multiply(self, other: Polynomial) -> Polynomial:
        terms: dict[tuple[int, ...], Fraction] = {}
        for left, a in self.terms:
            for right, b in other.terms:
                powers = tuple(x + y for x, y in zip(left, right, strict=True))
                terms[powers] = terms.get(powers, Fraction(0)) + a * b
        return self.create(self.variables, terms)


@dataclass(frozen=True, slots=True)
class DomainValue:
    polynomial: Polynomial
    modulus: int | None = None

    def evaluate(self, values: tuple[int, ...]) -> Fraction:
        value = self.polynomial.evaluate(values)
        if self.modulus is not None:
            if value.denominator != 1:
                msg = "Congruences require integer values."
                raise ExpressionError(msg)
            return Fraction(value.numerator % self.modulus)
        return value


type ComparisonOperator = Literal["lt", "le", "eq", "ne", "ge", "gt"]


@dataclass(frozen=True, slots=True)
class Comparison:
    left: DomainValue
    operator: ComparisonOperator
    right: DomainValue

    def evaluate(self, values: tuple[int, ...]) -> bool:
        left, right = self.left.evaluate(values), self.right.evaluate(values)
        match self.operator:
            case "lt":
                return left < right
            case "le":
                return left <= right
            case "eq":
                return left == right
            case "ne":
                return left != right
            case "ge":
                return left >= right
            case "gt":
                return left > right


@dataclass(frozen=True, slots=True)
class Junction:
    operator: Literal["and", "or", "not"]
    children: tuple[Comparison | Junction, ...]

    def evaluate(self, values: tuple[int, ...]) -> bool:
        match self.operator:
            case "and":
                return all(child.evaluate(values) for child in self.children)
            case "or":
                return any(child.evaluate(values) for child in self.children)
            case "not":
                return not self.children[0].evaluate(values)


type Predicate = Comparison | Junction


class ExpressionParser:
    """Parse against a fixed parameter namespace; limits apply before expansion."""

    def __init__(self, variables: tuple[str, ...]) -> None:
        if (
            len(variables) > 3
            or len(set(variables)) != len(variables)
            or any(not _NAME.fullmatch(name) or name in _KEYWORDS for name in variables)
        ):
            msg = "Use up to three distinct ASCII parameter names, excluding boolean operators."
            raise ExpressionError(msg)
        self.variables = variables

    def polynomial(self, source: str) -> Polynomial:
        tree, text = self._parse(source)
        return self._polynomial(tree.body, text)

    def predicate(self, source: str) -> Predicate:
        tree, text = self._parse(source)
        return self._predicate(tree.body, text)

    def _parse(self, source: str) -> tuple[ast.Expression, str]:
        if len(source) > 512:
            msg = "Expression exceeds 512 characters."
            raise ExpressionError(msg)
        source = source.replace("\N{MULTIPLICATION SIGN}", "*").replace("·", "*").replace("≤", "<=").replace("≥", ">=")
        tokens: list[str] = []
        position = 0
        while position < len(source):
            if source[position].isspace():
                position += 1
                continue
            match = _TOKEN.match(source, position)
            if match is None:
                msg = "Unsupported expression character."
                raise ExpressionError(msg)
            tokens.append(match.group())
            position = match.end()
        if len(tokens) > 128:
            msg = "Expression exceeds 128 tokens."
            raise ExpressionError(msg)
        output: list[str] = []
        for index, token in enumerate(tokens):
            if index:
                previous = tokens[index - 1]
                left = previous == ")" or previous in self.variables or previous[0].isdigit()
                right = token == "(" or token in self.variables
                if left and right:
                    output.append("*")
            output.append({"^": "**", "=": "==", ";": "and"}.get(token, token))
        text = " ".join(output)
        try:
            tree = ast.parse(text, mode="eval")
        except (SyntaxError, RecursionError) as error:
            msg = "Unsupported expression syntax."
            raise ExpressionError(msg) from error
        if sum(1 for _ in ast.walk(tree)) > 192:
            msg = "Expression exceeds the syntax tree limit."
            raise ExpressionError(msg)
        return tree, text

    def _polynomial(self, node: ast.AST, text: str) -> Polynomial:
        match node:
            case ast.Constant(value=value) if type(value) in (int, float):
                literal = ast.get_source_segment(text, node)
                assert literal is not None
                return Polynomial.constant(self.variables, Fraction(literal))
            case ast.Name(id=name) if name in self.variables:
                powers = tuple(int(variable == name) for variable in self.variables)
                return Polynomial.create(self.variables, {powers: Fraction(1)})
            case ast.UnaryOp(op=ast.USub(), operand=operand):
                return self._polynomial(operand, text).scale(Fraction(-1))
            case ast.UnaryOp(op=ast.UAdd(), operand=operand):
                return self._polynomial(operand, text)
            case ast.BinOp(left=left, op=ast.Pow(), right=ast.Constant(value=power)) if type(power) is int:
                if not 0 <= power <= MAX_DEGREE:
                    msg = "Powers must be integers between zero and three."
                    raise ExpressionError(msg)
                base = self._polynomial(left, text)
                result = Polynomial.constant(self.variables, Fraction(1))
                for _ in range(power):
                    result = result.multiply(base)
                return result
            case ast.BinOp(left=left, op=op, right=right):
                a, b = self._polynomial(left, text), self._polynomial(right, text)
                match op:
                    case ast.Add():
                        return a.add(b)
                    case ast.Sub():
                        return a.add(b.scale(Fraction(-1)))
                    case ast.Mult():
                        return a.multiply(b)
                    case ast.Div() if b.degree == 0:
                        divisor = b.evaluate((0,) * len(self.variables))
                        if divisor:
                            return a.scale(1 / divisor)
        msg = "Expected a degree-three polynomial; division requires a nonzero constant."
        raise ExpressionError(msg)

    def _domain_value(self, node: ast.AST, text: str) -> DomainValue:
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
            modulus = self._polynomial(node.right, text)
            value = modulus.evaluate((0,) * len(self.variables))
            if modulus.degree != 0 or value.denominator != 1 or value <= 0:
                msg = "A modulus must be a positive integer constant."
                raise ExpressionError(msg)
            return DomainValue(self._polynomial(node.left, text), value.numerator)
        return DomainValue(self._polynomial(node, text))

    def _predicate(self, node: ast.AST, text: str) -> Predicate:
        match node:
            case ast.BoolOp(op=op, values=values) if isinstance(op, ast.And | ast.Or):
                return Junction(
                    "and" if isinstance(op, ast.And) else "or", tuple(self._predicate(v, text) for v in values)
                )
            case ast.UnaryOp(op=ast.Not(), operand=operand):
                return Junction("not", (self._predicate(operand, text),))
            case ast.Compare(left=left, ops=ops, comparators=comparators):
                comparisons: list[Predicate] = []
                names: dict[type[ast.cmpop], ComparisonOperator] = {
                    ast.Lt: "lt",
                    ast.LtE: "le",
                    ast.Eq: "eq",
                    ast.NotEq: "ne",
                    ast.GtE: "ge",
                    ast.Gt: "gt",
                }
                for (a, b), op in zip(pairwise([left, *comparators]), ops, strict=True):
                    if type(op) not in names:
                        break
                    comparisons.append(
                        Comparison(self._domain_value(a, text), names[type(op)], self._domain_value(b, text))
                    )
                else:
                    return Junction("and", tuple(comparisons))
        msg = "Expected comparisons joined with and, or, or not."
        raise ExpressionError(msg)
