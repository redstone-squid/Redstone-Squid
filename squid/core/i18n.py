"""Locale-agnostic translation lookup shared by the bot and the REST API."""

import gettext
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


def negotiate_locale(requested: str | None) -> str:
    """Resolve a requested locale tag to one of `SUPPORTED_LOCALES`."""
    if requested is None:
        return DEFAULT_LOCALE
    try:
        candidate = Locale.parse(requested, sep="-")
    except (UnknownLocaleError, ValueError):
        return DEFAULT_LOCALE
    for supported in SUPPORTED_LOCALES:
        if Locale.parse(supported, sep="-") == candidate:
            return supported
    # Fall back to a language-only match, e.g. "zh-TW" negotiates to "zh-CN".
    for supported in SUPPORTED_LOCALES:
        if Locale.parse(supported, sep="-").language == candidate.language:
            return supported
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
