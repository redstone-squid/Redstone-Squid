"""Application locale negotiation and shipped Fluent resources."""

from squid.core.i18n import locales_dir, localization_for, negotiate_locale, negotiate_locale_candidates
from squid_i18n import current_localization, localization_scope, tr


def test_tr_uses_and_restores_ambient_localization() -> None:
    translated = localization_for("zh-CN")
    original = current_localization()

    with localization_scope(translated):
        assert current_localization() is translated

    assert current_localization() is original


def test_tr_falls_back_to_source_string_for_unknown_locale() -> None:
    with localization_scope(localization_for("xx-XX")):
        assert tr(t"test-hello = Hello").resolve() == "Hello"


def test_tr_falls_back_to_source_string_for_missing_key() -> None:
    with localization_scope(localization_for("en")):
        assert (
            tr(t"test-missing = This msgid does not exist anywhere.").resolve() == "This msgid does not exist anywhere."
        )


def test_tr_none_locale_uses_default() -> None:
    with localization_scope(localization_for(None)):
        assert tr(t"test-hello = Hello").resolve() == "Hello"


def test_negotiate_locale_exact_match() -> None:
    assert negotiate_locale("zh-CN") == "zh-CN"


def test_negotiate_locale_language_only_match() -> None:
    assert negotiate_locale("zh-TW") == "zh-CN"


def test_negotiate_locale_unsupported_falls_back_to_default() -> None:
    assert negotiate_locale("fr") == "en"


def test_negotiate_locale_none_falls_back_to_default() -> None:
    assert negotiate_locale(None) == "en"


def test_negotiate_locale_candidates_prefers_exact_match_over_earlier_fuzzy_match() -> None:
    # "zh-TW" (first preference) only matches "zh-CN" via language fallback; "en" (second
    # preference) is an exact match and must win regardless of its position in the list.
    assert negotiate_locale_candidates(["zh-TW", "en"]) == "en"


def test_negotiate_locale_candidates_prefers_earlier_exact_match() -> None:
    assert negotiate_locale_candidates(["en", "zh-CN"]) == "en"


def test_negotiate_locale_candidates_falls_back_to_fuzzy_when_no_exact_match() -> None:
    assert negotiate_locale_candidates(["fr", "zh-TW"]) == "zh-CN"


def test_negotiate_locale_candidates_falls_back_to_default() -> None:
    assert negotiate_locale_candidates(["fr", "de"]) == "en"


def test_locales_dir_points_at_shipped_catalogs() -> None:
    assert (locales_dir() / "zh-CN" / "messages.ftl").is_file()


def test_shipped_translation_uses_a_stable_identifier() -> None:
    with localization_scope(localization_for("zh-CN")):
        assert tr(t"access-cancel = Cancel").resolve() == "取消"
