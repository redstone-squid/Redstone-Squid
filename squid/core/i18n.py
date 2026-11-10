"""Create deferred messages and translate them for the current reader.

Application code can build a Message before a reader's locale is known. The
presentation code later renders it through the current Localization context.
Locale negotiation selects a supported language, and gettext catalogs supply
its translations; missing translations retain the source text.
"""

import gettext
from collections.abc import Sequence
from functools import cache
from pathlib import Path
from string.templatelib import Template
from typing import overload

from babel import Locale, UnknownLocaleError

from squid_ui.text import Localization, Message

DOMAIN = "squid"
DEFAULT_LOCALE = "en"
SUPPORTED_LOCALES = frozenset({"en", "zh-CN"})


@overload
def tr(message: Template, /, *, plural: Template | None = None) -> Message: ...


@overload
def tr(message: str, /) -> Message: ...


def tr(message: str | Template, /, *, plural: Template | None = None) -> Message:
    """Defer text until render time, when the reader's locale is known.

    `tr("Saved.")` and `tr(t"Hello {name}")` both return a Message that renderers translate
    later, in the reader's locale. Use a template string only to interpolate values; a
    plain string is the catalogue key as written, and braces in it stay literal. Pass the
    Message on, interpolate it into another `tr` template, or combine several with `+` or
    `Message.join`; call `.resolve()` only where a string is required.

    Template placeholders must be simple identifiers. Plurals need a template string:
    both forms must use the same placeholders and include an integer `count`.
    """
    if isinstance(message, str):
        if plural is not None:
            detail = "plural forms need a template string with an integer 'count'"
            raise TypeError(detail)
        return Message(message)
    singular_message = Message.from_template(message)
    if plural is None:
        return singular_message
    if not isinstance(plural, Template):
        detail = "plural template must be a Python template string"
        raise TypeError(detail)
    plural_message = Message.from_template(plural)
    if singular_message.params.keys() != plural_message.params.keys():
        detail = "singular and plural templates must use the same placeholders"
        raise ValueError(detail)
    count = singular_message.params.get("count")
    if not isinstance(count, int):
        detail = "plural templates require an integer 'count' interpolation"
        raise TypeError(detail)
    return Message(singular_message.template, singular_message.params, plural=plural_message.template)


def locales_dir() -> Path:
    """Return the directory containing this checkout's compiled gettext catalogs."""
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


def negotiate_locale(requested: str | None) -> str:
    """Choose a supported locale by exact match, then language, then the default.

    For example, zh-TW falls back to zh-CN. Missing and unrecognized tags use en.
    """
    if requested is None:
        return DEFAULT_LOCALE
    return _exact_match(requested) or _language_match(requested) or DEFAULT_LOCALE


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
