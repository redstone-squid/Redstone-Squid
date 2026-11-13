"""Resolved text dialect and interpolation safety."""

from datetime import UTC, datetime

import pytest

from squid_i18n import tr
from squid_ui.text import (
    Localization,
    Markup,
    Message,
    ResolvedText,
    current_localization,
    discord_md,
    discord_text,
    localization_scope,
    md,
    plain,
    resolve_text,
    source_text,
)


def test_bare_markdown_is_trusted() -> None:
    text = md("**Build complete**")

    assert text.content == "**Build complete**"
    assert text.markup is Markup.MARKDOWN


def test_template_string_interpolations_are_escaped_and_mentions_are_neutralized() -> None:
    title = "**surprise** @everyone"
    text = md(t"Build: {title}")

    assert text.content == "Build: \\*\\*surprise\\*\\* @\u200beveryone"


def test_template_string_can_opt_into_trusted_markdown() -> None:
    label = discord_md("**safe author markup**")

    assert md(t"Result: {label}").content == "Result: **safe author markup**"


def test_translated_format_strings_escape_named_values() -> None:
    text = md("Build {title} by {author}", title="[x](bad)", author="@here")

    assert text.content == "Build \\[x\\]\\(bad\\) by @\u200bhere"


def test_translated_format_strings_apply_specs_before_escaping() -> None:
    text = md("Took {seconds:.1f}s", seconds=2.54)

    assert text.content == "Took 2\\.5s"


def test_localization_scope_is_nested_and_reset_safe() -> None:
    first = Localization("first")
    second = Localization("second")
    original = current_localization()

    with localization_scope(first):
        assert current_localization() is first
        with localization_scope(second):
            assert current_localization() is second
        assert current_localization() is first

    assert current_localization() is original


def test_plain_text_is_escaped_only_when_drawn_for_discord() -> None:
    text = plain("**literal**")

    assert text.content == "**literal**"
    assert discord_text(text) == "\\*\\*literal\\*\\*"


def test_timestamp_interpolation_uses_discord_tokens_and_plain_iso() -> None:
    import squid_ui as sl

    instant = datetime(2026, 8, 22, 14, 30, tzinfo=UTC)

    assert discord_md(t"Updated {instant}").content == "Updated <t:1787409000:f>"
    assert discord_md(t"Updated {sl.timestamp(instant, style=sl.semantic.TimeStyle.RELATIVE)}").content == (
        "Updated <t:1787409000:R>"
    )
    assert plain(instant).content == "2026-08-22T14:30:00+00:00"


def french() -> Localization:
    return Localization("fr", ("test-hello = Bonjour\ntest-bye = Au revoir { $name }",))


def test_a_message_cannot_become_a_string_by_accident() -> None:
    message = tr(t"test-hello = Hello")

    with pytest.raises(TypeError, match="no text until resolved"):
        str(message)
    with pytest.raises(TypeError, match="no text until resolved"):
        _ = f"{message}!"


def test_joined_text_stays_deferred_until_the_render_locale() -> None:
    joined = Message.join(
        "\n", [tr(t"test-hello = Hello"), md("**trusted**"), tr(t"test-bye = Bye {(name := '*Ann*')}")]
    )

    assert resolve_text(joined, french()).content == "Bonjour\n**trusted**\nAu revoir \\*Ann\\*"
    assert source_text(joined) == "Hello\n**trusted**\nBye \\*Ann\\*"


def test_joined_plain_text_is_escaped_inside_markdown() -> None:
    joined = Message.join(" {} ", [plain("*literal*"), md("{braces}")])

    assert resolve_text(joined, french()).content == "\\*literal\\* \\{\\} {braces}"


def test_adding_text_keeps_messages_deferred() -> None:
    greeting = md("> ") + tr(t"test-hello = Hello") + " " + plain("*Ann*")

    assert isinstance(greeting, Message)
    assert resolve_text(greeting, french()).content == "> Bonjour \\*Ann\\*"


def test_adding_literal_text_does_not_choose_markdown() -> None:
    combined = plain("*a*") + " **b**"

    assert resolve_text(combined, Localization()) == ResolvedText("*a* **b**")
    assert resolve_text("**a** " + plain("*b*"), Localization()) == ResolvedText("**a** *b*")


def test_resolve_uses_the_current_localization() -> None:
    with localization_scope(french()):
        assert tr(t"test-hello = Hello").resolve() == "Bonjour"
        assert plain("*a*").resolve() == "*a*"


def test_markdown_interpolation_composes_typed_text_without_a_trust_wrapper() -> None:
    title = plain("*Door*")
    status = md("**Approved**")

    assert md(t"{title}: {status}").content == r"\*Door\*: **Approved**"
    assert md("{title}: {status}", title=title, status=status).content == r"\*Door\*: **Approved**"


def test_discord_serialization_is_explicit_and_neutralizes_plain_mentions() -> None:
    text = resolve_text("Build #7. @everyone **literal**", Localization())

    assert text.markup is Markup.PLAIN
    assert text.resolve() == "Build #7. @everyone **literal**"
    assert discord_text(text) == r"Build #7. @" + "\u200b" + r"everyone \*\*literal\*\*"
