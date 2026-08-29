"""Core translation lookup tests."""

import ast
from pathlib import Path

import pytest
from babel.messages.catalog import Catalog
from babel.messages.mofile import write_mo

from squid.core.extract import deferred_msgid
from squid.core.i18n import (
    _catalog,
    locales_dir,
    localization_for,
    negotiate_locale,
    negotiate_locale_candidates,
    tr,
)
from squid_ui.text import Message, current_localization, localization_scope


def test_deferred_msgid_extracts_plural_templates_as_a_pair() -> None:
    tree = ast.parse('tr(t"{count} build", plural=t"{count} builds")')
    call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call))

    assert deferred_msgid(call) == ("{count} build", "{count} builds")


def test_deferred_msgid_preserves_static_format_specifications() -> None:
    tree = ast.parse('tr(t"Took {seconds:.1f}s")')
    call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call))

    assert deferred_msgid(call) == "Took {seconds:.1f}s"


def test_tr_template_defers_interpolation_until_resolution() -> None:
    title = "[unsafe](link) @everyone"

    message = tr(t"Build {title}")

    assert isinstance(message, Message)
    assert message.template == "Build {title}"
    assert tr(message) == "Build \\[unsafe\\]\\(link\\) @\u200beveryone"


def test_tr_preserves_conversion_and_format_specification() -> None:
    seconds = 2.54
    value = "hello"

    assert tr(tr(t"Try again in {seconds:.1f} seconds: {value!r}")) == "Try again in 2\\.5 seconds: 'hello'"


def test_tr_plural_template_defers_a_paired_message() -> None:
    count = 2

    message = tr(t"{count} build", plural=t"{count} builds")

    assert isinstance(message, Message)
    assert message.plural == "{count} builds"
    assert tr(message) == "2 builds"


def test_tr_plural_template_requires_matching_placeholders() -> None:
    count = 2
    total = 2

    with pytest.raises(ValueError, match="same placeholders"):
        tr(t"{count} build", plural=t"{total} builds")


def test_tr_uses_and_restores_ambient_localization() -> None:
    translated = localization_for("zh-CN")
    original = current_localization()

    with localization_scope(translated):
        assert current_localization() is translated

    assert current_localization() is original


def test_tr_falls_back_to_source_string_for_unknown_locale() -> None:
    with localization_scope(localization_for("xx-XX")):
        assert tr("Hello") == "Hello"


def test_tr_falls_back_to_source_string_for_missing_key() -> None:
    with localization_scope(localization_for("en")):
        assert tr("This msgid does not exist anywhere.") == "This msgid does not exist anywhere."


def test_tr_applies_format_params_after_translation() -> None:
    with localization_scope(localization_for("en")):
        assert tr("Try again in {seconds:.1f} seconds.", seconds=2.5) == "Try again in 2.5 seconds."


def test_tr_none_locale_uses_default() -> None:
    with localization_scope(localization_for(None)):
        assert tr("Hello") == "Hello"


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


def test_locales_dir_points_at_the_shipped_catalogs() -> None:
    """The walk out of `squid/core/` breaks silently: a wrong directory just stops translating."""
    assert (locales_dir() / "zh_CN" / "LC_MESSAGES" / "squid.po").is_file()


def test_catalog_maps_a_bcp47_tag_onto_its_gettext_directory(tmp_path: Path) -> None:
    """A `zh-CN` tag has to find `zh_CN/LC_MESSAGES/squid.mo`; nothing else pins that conversion.

    Compiled catalogs are produced by `just i18n-compile` and are not committed, so a test
    reading the shipped tree would assert the untranslated fallback on a clean checkout and
    prove nothing. Injecting `localedir` lets this assert a translation actually being found.
    """
    messages = tmp_path / "zh_CN" / "LC_MESSAGES"
    messages.mkdir(parents=True)
    catalog = Catalog(locale="zh_CN")
    catalog.add("Hello", "你好")
    with (messages / "squid.mo").open("wb") as handle:
        write_mo(handle, catalog)

    assert _catalog("zh-CN", tmp_path).gettext("Hello") == "你好"
