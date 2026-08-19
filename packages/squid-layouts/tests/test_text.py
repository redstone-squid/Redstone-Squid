"""Resolved text dialect and interpolation safety."""

from squid_layouts.text import TextDialect, discord_text, md, plain, raw_md


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
