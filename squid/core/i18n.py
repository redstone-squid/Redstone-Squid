"""Locale-agnostic translation lookup shared by the bot and the REST API."""

import gettext
from collections.abc import Sequence
from functools import cache
from pathlib import Path

from babel import Locale, UnknownLocaleError

LOCALES_DIR = Path(__file__).resolve().parent.parent.parent / "locales"
DOMAIN = "squid"
DEFAULT_LOCALE = "en"
SUPPORTED_LOCALES = frozenset({"en", "zh-CN"})


def _(message: str) -> str:
    """Mark a string literal as translatable for `pybabel extract`.

    Returns the message unchanged; actual translation happens later via
    `translate()`, once a locale is known.
    """
    return message


@cache
def _catalog(locale: str) -> gettext.NullTranslations:
    return gettext.translation(
        DOMAIN,
        localedir=LOCALES_DIR,
        languages=[locale],
        fallback=True,
    )


def _parse(tag: str) -> Locale | None:
    try:
        return Locale.parse(tag, sep="-")
    except (UnknownLocaleError, ValueError):
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


def translate(locale: str | None, message: str, /, **params: object) -> str:
    """Translate `message` (used as the gettext msgid) into `locale`.

    `params` are applied with `str.format` *after* translation, so dynamic
    content is never baked into the msgid.
    """
    resolved = negotiate_locale(locale)
    text = _catalog(resolved).gettext(message)
    return text.format(**params) if params else text


def ntranslate(
    locale: str | None,
    singular: str,
    plural: str,
    n: int,
    /,
    **params: object,
) -> str:
    """Translate a pluralized message for `n` items into `locale`."""
    resolved = negotiate_locale(locale)
    text = _catalog(resolved).ngettext(singular, plural, n)
    return text.format(n=n, **params) if params else text.format(n=n)
