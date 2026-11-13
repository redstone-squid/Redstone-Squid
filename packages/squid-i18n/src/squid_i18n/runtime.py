"""Resolve immutable catalog snapshots with output-time escaping."""

import logging
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache
from typing import Final
from zoneinfo import ZoneInfo

from babel import UnknownLocaleError
from fluent_compiler.compiler import CompiledFtl, compile_messages
from fluent_compiler.resource import FtlResource
from fluent_compiler.types import fluent_date, fluent_number

from squid_i18n import text, tree
from squid_i18n.compiler import parse_resource
from squid_i18n.errors import CatalogError, ResolutionError
from squid_i18n.expressions import DateTimeExpression, NumberExpression

logger = logging.getLogger(__name__)


class _Binding(str):
    """An interpolation string: selectors see its original value, output escapes it."""


class _SafeText(str):
    """Fluent output retaining authored strings separately from captured nodes."""

    parts: tuple[str | tree.Node, ...]

    def __new__(cls, value: str, parts: tuple[str | tree.Node, ...] | None = None) -> _SafeText:
        result = super().__new__(cls, value)
        result.parts = (value,) if parts is None else parts
        return result


class _Escaper:
    output_type: Final = _SafeText
    use_isolating: Final = False
    name: Final = "squid-text"

    def select(self, message_id: str, **kwargs: object) -> bool:
        return True

    def escape(self, unescaped: str, /) -> _SafeText:
        if isinstance(unescaped, _SafeText):
            return unescaped
        return (
            _SafeText(unescaped, (tree.literal(unescaped),))
            if isinstance(unescaped, _Binding)
            else _SafeText(unescaped)
        )

    def mark_escaped(self, value: str, /) -> _SafeText:
        return _SafeText(value)

    def join(self, parts: Sequence[_SafeText], /) -> _SafeText:
        return _SafeText("".join(parts), tuple(part for output in parts for part in output.parts))


@lru_cache(maxsize=64)
def compile_catalog(locale: str, resources: tuple[str, ...]) -> CompiledFtl:
    # Validate duplicate IDs across files as well as parser junk before compilation.
    parse_resource("\n".join(resources))
    try:
        compiled = compile_messages(
            locale,
            [FtlResource.from_string(resource) for resource in resources],
            use_isolating=False,
            escapers=[_Escaper()],
        )
    except UnknownLocaleError as error:
        msg = f"Unsupported Fluent locale: {locale}"
        raise CatalogError(msg) from error
    if compiled.errors:
        raise CatalogError(
            "Invalid Fluent catalog: " + "; ".join(f"{identifier}: {error}" for identifier, error in compiled.errors)
        )
    return compiled


def _arguments(message: text._Translation, localization: text.Localization) -> dict[str, object]:
    args: dict[str, object] = {}
    for name, value in message.bindings.items():
        if isinstance(value, text.Text):
            resolved = text.resolve_text(value, localization)
            args[name] = _SafeText(resolved.plain(), (tree.Node("sequence", children=resolved.nodes),))
        elif isinstance(value, NumberExpression):
            args[name] = fluent_number(value.value, **dict(value.options))
        elif isinstance(value, DateTimeExpression):
            options: dict[str, object] = dict(value.options)
            zone = options.get("timeZone")
            if isinstance(zone, str):
                options["timeZone"] = ZoneInfo(zone)
            args[name] = fluent_date(value.value, **options)
        elif isinstance(value, text.TextValue | datetime):
            resolved = text.resolve_text(text._capture(value), localization)
            args[name] = _SafeText(resolved.plain(), (tree.Node("sequence", children=resolved.nodes),))
        elif isinstance(value, bool):
            args[name] = "true" if value else "false"
        elif isinstance(value, int | float | Decimal | date):
            args[name] = value
        else:
            args[name] = _Binding(str(value))
    return args


def render_message(message: text._Translation, localization: text.Localization) -> text._Document:
    declaration = message.declaration
    assert declaration is not None
    if message.selected_attribute is None and not declaration.has_value:
        msg = f"{declaration.identifier} has attributes but no message value"
        raise ResolutionError(msg)
    key = declaration.identifier + ("." + message.selected_attribute if message.selected_attribute else "")
    locale = localization.locale or "en"
    candidates = (
        ((locale, localization.resources), ("en", localization.source_resources))
        if locale != "en"
        else (("en", localization.source_resources or localization.resources),)
    )
    for candidate, resources in candidates:
        try:
            compiled = compile_catalog(candidate, resources)
            if candidate == "en" and key not in compiled.message_functions:
                compiled = compile_catalog(candidate, (*resources, declaration.source))
            function = compiled.message_functions.get(key)
            if function is not None:
                errors: list[Exception] = []
                output = function(_arguments(message, localization), errors)
                if not errors:
                    parts = output.parts if isinstance(output, _SafeText) else (str(output),)
                    if not message.markdown:
                        nodes = tuple(tree.literal(part) if isinstance(part, str) else part for part in parts)
                    else:
                        nodes = tree.parse(parts)
                    return text._Document(nodes)
        except CatalogError, TypeError, ValueError, ArithmeticError:
            pass
        if candidate != "en":
            logger.warning("Fluent fallback for %s in %s", key, candidate)
    msg = f"Cannot resolve source Fluent message {key}"
    raise ResolutionError(msg)
