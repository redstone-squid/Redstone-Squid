"""Core translation lookup tests."""

from squid.core.i18n import negotiate_locale, ntranslate, translate


def test_translate_falls_back_to_source_string_for_unknown_locale() -> None:
    assert translate("xx-XX", "Hello") == "Hello"


def test_translate_falls_back_to_source_string_for_missing_key() -> None:
    assert translate("en", "This msgid does not exist anywhere.") == "This msgid does not exist anywhere."


def test_translate_applies_format_params_after_translation() -> None:
    assert translate("en", "Try again in {seconds:.1f} seconds.", seconds=2.5) == "Try again in 2.5 seconds."


def test_translate_none_locale_uses_default() -> None:
    assert translate(None, "Hello") == "Hello"


def test_negotiate_locale_exact_match() -> None:
    assert negotiate_locale("zh-CN") == "zh-CN"


def test_negotiate_locale_language_only_match() -> None:
    assert negotiate_locale("zh-TW") == "zh-CN"


def test_negotiate_locale_unsupported_falls_back_to_default() -> None:
    assert negotiate_locale("fr") == "en"


def test_negotiate_locale_none_falls_back_to_default() -> None:
    assert negotiate_locale(None) == "en"


def test_ntranslate_selects_plural_form() -> None:
    singular = "{n} build found."
    plural = "{n} builds found."
    assert ntranslate("en", singular, plural, 1) == "1 build found."
    assert ntranslate("en", singular, plural, 5) == "5 builds found."
