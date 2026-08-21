"""Resolved text dialect and interpolation safety."""

import pytest

from squid_layouts.text import Localization, Message, TextDialect, discord_text, md, plain, raw_md, resolve_text


def test_bare_markdown_is_trusted() -> None:
    text = md("**Build complete**")

    assert text.content == "**Build complete**"
    assert text.dialect is TextDialect.DISCORD_MARKDOWN


def test_template_string_interpolations_are_escaped_and_mentions_are_neutralized() -> None:
    title = "**surprise** @everyone"
    text = md(t"Build: {title}")

    assert text.content == "Build: \\*\\*surprise\\*\\* @\u200beveryone"


def test_template_string_can_opt_into_trusted_markdown() -> None:
    label = raw_md("**safe author markup**")

    assert md(t"Result: {label}").content == "Result: **safe author markup**"


def test_translated_format_strings_escape_named_values() -> None:
    text = md("Build {title} by {author}", title="[x](bad)", author="@here")

    assert text.content == "Build \\[x\\]\\(bad\\) by @\u200bhere"


def test_plain_text_is_escaped_only_when_drawn_for_discord() -> None:
    text = plain("**literal**")

    assert text.content == "**literal**"
    assert discord_text(text) == "\\*\\*literal\\*\\*"


def test_message_translates_at_resolution_and_escapes_params() -> None:
    localization = Localization("xx", gettext=lambda message: {"Build {title}": "Obra {title}"}[message])

    text = resolve_text(Message("Build {title}", {"title": "[x](bad) @here"}), localization)

    assert text.content == "Obra \\[x\\]\\(bad\\) @\u200bhere"


def test_plural_message_uses_catalog_plural_lookup() -> None:
    localization = Localization(
        "xx",
        ngettext=lambda singular, plural, count: singular if count == 1 else f"translated {plural}",
    )

    text = resolve_text(Message("{count} item", {"count": 2}, plural="{count} items"), localization)

    assert text.content == "translated 2 items"


def test_plural_message_requires_integer_count() -> None:
    with pytest.raises(ValueError, match="integer 'count'"):
        resolve_text(Message("one", {"count": "many"}, plural="many"), Localization())


def test_message_can_interpolate_another_deferred_message() -> None:
    translations = {"Section": "Sektion", "{section} page": "{section} Seite"}
    localization = Localization("de", gettext=lambda message: translations[message])

    text = resolve_text(Message("{section} page", {"section": Message("Section")}), localization)

    assert text.content == "Sektion Seite"
