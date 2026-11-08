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
from string.templatelib import Interpolation, Template
from typing import overload

from babel import Locale, UnknownLocaleError

from squid_ui.text import Localization, Message, TextLike, current_localization, discord_text, resolve_text

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
def tr(message: str, /, **params: object) -> str: ...


def tr(message: str | Template, /, **params: object) -> str | Message:
    """Defer a template string until render time, when the reader's locale is known.

    `tr(t"Hello {name}")` returns a Message that renderers translate later. Prefer it
    to `tr_now` wherever the text ends up in a component, so it follows the render's
    locale. A plain string is a legacy form, translated immediately and formatted
    with any keyword arguments.

    Template placeholders must be simple identifiers. Plural templates must use the
    same placeholders as the singular form and include an integer `count`.
    """
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
    text = current_localization().gettext(message)
    return text.format(**params) if params else text


def tr_now(message: Template | Message | str, /, *, plural: Template | None = None) -> str:
    """Translate into the current localization immediately and return Discord Markdown.

    Use this only where text has to be a string now: composed into a larger string,
    or translated inside an explicit `localization_scope` chosen for a reason the
    render does not know about. Interpolated values are escaped as with `tr`. A plain
    string is looked up as a catalogue key, as `tr` does.
    """
    if isinstance(message, Template):
        message = tr(message, plural=plural)
    elif plural is not None:
        detail = "plural forms belong to the template, not to an existing message"
        raise TypeError(detail)
    if isinstance(message, str):
        return tr(message)
    return resolve_text(message, current_localization()).content


def resolve_now(text: TextLike, /) -> str:
    """Translate text into the current localization and return it as Discord Markdown.

    Prefer passing a Message to a renderer, which translates it for the reader. Use this
    only where a string is required now: a discord.py argument that takes `str`, or text
    built inside a `localization_scope` chosen for a reason the render does not know. A
    bare string is returned as trusted Markdown without a catalogue lookup.
    """
    return discord_text(resolve_text(text, current_localization()))


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


def _match_locale(requested: str) -> str | None:
    """Match a single locale tag against `SUPPORTED_LOCALES`, or None if none fit."""
    return _exact_match(requested) or _language_match(requested)


def negotiate_locale(requested: str | None) -> str:
    """Choose a supported locale by exact match, then language, then the default.

    For example, zh-TW falls back to zh-CN. Missing and unrecognized tags use en.
    """
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
