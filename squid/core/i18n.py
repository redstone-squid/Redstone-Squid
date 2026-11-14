"""Negotiate application locales and load immutable Fluent resource snapshots."""

from collections.abc import Sequence
from functools import cache
from importlib.util import find_spec
from pathlib import Path

from babel import Locale, UnknownLocaleError

from squid_i18n import Localization
from squid_i18n import tr as tr

DEFAULT_LOCALE = "en"
SUPPORTED_LOCALES = frozenset({"en", "zh-CN"})


def locales_dir() -> Path:
    """Return the application's shipped Fluent catalogs."""
    return Path(__file__).resolve().parent.parent / "locales"


@cache
def _resources(locale: str) -> tuple[str, ...]:
    resources = [path.read_text() for path in sorted((locales_dir() / locale).glob("*.ftl"))]
    for package in ("squid_ui", "squid_ui_widgets", "squid_ui_discord"):
        # Locate resources without importing the framework or Discord adapter into API processes.
        spec = find_spec(package)
        if spec is not None and spec.submodule_search_locations is not None:
            for location in spec.submodule_search_locations:
                directory = Path(location) / "locales" / locale
                resources.extend(path.read_text() for path in sorted(directory.glob("*.ftl")))
    return tuple(resources)


@cache
def localization_for(locale: str | None) -> Localization:
    """Build a reader's catalog snapshot with the application's English fallback."""
    resolved = negotiate_locale(locale)
    return Localization(resolved, _resources(resolved), _resources("en"))


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
