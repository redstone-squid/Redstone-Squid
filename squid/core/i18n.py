"""Locale-agnostic translation lookup shared by the bot and the REST API."""

import gettext
from collections.abc import Sequence
from functools import cache
from pathlib import Path
from string.templatelib import Interpolation, Template
from typing import overload

from babel import Locale, UnknownLocaleError

from squid_ui.text import Localization, Message, current_localization, resolve_text

DOMAIN = "squid"
DEFAULT_LOCALE = "en"
SUPPORTED_LOCALES = frozenset({"en", "zh-CN"})


def _placeholder(interpolation: Interpolation) -> str:
    name = interpolation.expression
    if not name.isidentifier():
        detail = f"template string interpolation {name!r} is not a placeholder name"
        raise ValueError(detail)
    conversion = "" if interpolation.conversion is None else f"!{interpolation.conversion}"
    format_spec = "" if not interpolation.format_spec else f":{interpolation.format_spec}"
    return f"{{{name}{conversion}{format_spec}}}"


def _message_from_template(template: Template) -> Message:
    values: dict[str, object] = {}
    parts: list[str] = []
    for string, interpolation in zip(template.strings, template.interpolations, strict=False):
        parts.append(string)
        parts.append(_placeholder(interpolation))
        values[interpolation.expression] = interpolation.value
    parts.append(template.strings[-1])
    return Message("".join(parts), values)


@overload
def tr(message: Template, /, *, plural: Template | None = None) -> Message: ...


@overload
def tr(message: Message, /) -> str: ...


@overload
def tr(message: str, /, **params: object) -> str: ...


def tr(message: str | Template | Message, /, **params: object) -> str | Message:
    """Create deferred template text or resolve text through the ambient localization."""
    if isinstance(message, Template):
        plural = params.pop("plural", None)
        if params:
            detail = "template strings already contain their interpolation values"
            raise TypeError(detail)
        singular_message = _message_from_template(message)
        if plural is None:
            return singular_message
        if not isinstance(plural, Template):
            detail = "plural template must be a Python template string"
            raise TypeError(detail)
        plural_message = _message_from_template(plural)
        if singular_message.params.keys() != plural_message.params.keys():
            detail = "singular and plural templates must use the same placeholders"
            raise ValueError(detail)
        count = singular_message.params.get("count")
        if not isinstance(count, int):
            detail = "plural templates require an integer 'count' interpolation"
            raise TypeError(detail)
        return Message(singular_message.template, singular_message.params, plural=plural_message.template)
    if isinstance(message, Message):
        if params:
            detail = "deferred messages already contain their interpolation values"
            raise TypeError(detail)
        return resolve_text(message, current_localization()).content
    text = current_localization().gettext(message)
    return text.format(**params) if params else text


def locales_dir() -> Path:
    """Directory holding the compiled gettext catalogs shipped with this source tree.

    Resolved on demand rather than at import, so a test can point `_catalog` at a fixture
    tree without reloading the module, and so importing `squid.core.i18n` touches no disk.
    """
    return Path(__file__).resolve().parent.parent.parent / "locales"


@cache
def _catalog(locale: str, localedir: Path | None = None) -> gettext.NullTranslations:
    # On-disk catalog directories follow gettext/babel's underscore convention
    # (e.g. locales/zh_CN/), while `locale` elsewhere is the BCP-47 hyphenated
    # form used by Discord and HTTP Accept-Language (e.g. "zh-CN").
    return gettext.translation(
        DOMAIN,
        localedir=locales_dir() if localedir is None else localedir,
        languages=[locale.replace("-", "_")],
        fallback=True,
    )


def catalog_for(locale: str | None) -> gettext.NullTranslations:
    """Return the catalog for a negotiated application locale."""
    return _catalog(negotiate_locale(locale))


def localization_for(locale: str | None) -> Localization:
    """Build a localization backed by the negotiated application catalog."""
    resolved = negotiate_locale(locale)
    catalog = catalog_for(resolved)
    return Localization(locale=resolved, gettext=catalog.gettext, ngettext=catalog.ngettext)


def _parse(tag: str) -> Locale | None:
    try:
        return Locale.parse(tag, sep="-")
    except UnknownLocaleError, ValueError:
        return None


def _exact_match(requested: str) -> str | None:
    candidate = _parse(requested)
    if candidate is None:
        return None
    for supported in sorted(SUPPORTED_LOCALES):
        if Locale.parse(supported, sep="-") == candidate:
            return supported
    return None


def _language_match(requested: str) -> str | None:
    """Match by language only, e.g. "zh-TW" negotiates to "zh-CN"."""
    candidate = _parse(requested)
    if candidate is None:
        return None
    for supported in sorted(SUPPORTED_LOCALES):
        if Locale.parse(supported, sep="-").language == candidate.language:
            return supported
    return None


def _match_locale(requested: str) -> str | None:
    """Match a single locale tag against `SUPPORTED_LOCALES`, or None if none fit."""
    return _exact_match(requested) or _language_match(requested)


def negotiate_locale(requested: str | None) -> str:
    """Resolve a requested locale tag to one of `SUPPORTED_LOCALES`."""
    if requested is None:
        return DEFAULT_LOCALE
    return _match_locale(requested) or DEFAULT_LOCALE


def negotiate_locale_candidates(requested: Sequence[str]) -> str:
    """Resolve the best of several locale tags, in preference order.

    Exact matches outrank language-only fallback matches regardless of
    position, so an exact match later in `requested` isn't shadowed by a
    looser match earlier in the list; within each tier, earlier (higher
    priority) tags win.
    """
    for tag in requested:
        match = _exact_match(tag)
        if match is not None:
            return match
    for tag in requested:
        match = _language_match(tag)
        if match is not None:
            return match
    return DEFAULT_LOCALE
