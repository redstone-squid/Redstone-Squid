"""Discord syntax stays opt-in and preserves Markdown escaping and code boundaries."""

import pytest
from squid_ui_html.markup import markdown


def test_subtext_is_one_line_and_can_interrupt_a_paragraph() -> None:
    rendered = markdown("Normal\n-# Small **note**\nNormal again\n-# Another", block=True, discord=True)

    assert rendered == (
        '<p>Normal</p><p class="squid-subtext">Small <strong>note</strong></p>'
        '<p>Normal again</p><p class="squid-subtext">Another</p>'
    )


@pytest.mark.parametrize("source", ["-#missing space", "middle -# text", r"\-# escaped"])
def test_subtext_requires_an_unescaped_line_prefix(source: str) -> None:
    assert 'class="squid-subtext"' not in markdown(source, block=True, discord=True)


def test_spoilers_keep_inline_formatting_and_multiple_independent_reveal_controls() -> None:
    rendered = markdown("Before ||**secret** `||`|| and ||second|| after", block=True, discord=True)

    assert rendered.count('type="checkbox"') == 2
    assert '<span class="squid-text-spoiler__content"><strong>secret</strong> <code>||</code></span>' in rendered
    assert '<span class="squid-text-spoiler__content">second</span>' in rendered
    assert rendered.endswith(" after</p>")


@pytest.mark.parametrize("source", ["`||literal||`", "```\n-# ||literal||\n```", r"\|\|escaped\|\|", "||unclosed"])
def test_code_escaped_and_unclosed_spoilers_stay_literal(source: str) -> None:
    rendered = markdown(source, block=True, discord=True)

    assert 'class="squid-text-spoiler"' not in rendered
    assert 'class="squid-subtext"' not in rendered
    assert "||" in rendered


def test_spoilers_preserve_html_and_url_safety() -> None:
    rendered = markdown("||<script>alert(1)</script> [bad](javascript:alert(1))||", block=True, discord=True)

    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert 'href="javascript:' not in rendered
    assert 'type="checkbox"' in rendered


def test_native_markdown_does_not_interpret_discord_extensions() -> None:
    assert markdown("-# ||literal||", block=True) == "<p>-# ||literal||</p>"
