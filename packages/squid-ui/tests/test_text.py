"""Portable text composition and destination-specific serialization."""

from datetime import UTC, datetime

import pytest

from squid_i18n import (
    Localization,
    Text,
    current_localization,
    discord_text,
    inline_code,
    localization_scope,
    md,
    mention,
    plain,
    resolve_text,
    source_text,
    strong,
    text_link,
    timestamp_text,
    tr,
)


def discord(value: str | Text) -> str:
    return discord_text(resolve_text(value, current_localization()))


def french() -> Localization:
    return Localization("fr", ("test-hello = Bonjour\ntest-bye = Au revoir { $name }",))


def test_all_constructors_return_the_same_text_type() -> None:
    for value in (plain("literal"), md("**formatted**"), tr("Save", key="save"), strong("name")):
        assert type(value) is Text


def test_bindings_are_literal_and_mentions_are_neutralized_at_the_destination() -> None:
    title = "**surprise** @everyone <#123>"
    value = md(t"Build: {title}")
    assert source_text(value, format="plain") == "Build: " + title
    assert discord(value) == "Build: \\*\\*surprise\\*\\* @\u200beveryone \\<\u200b#123>"


def test_markdown_interpolation_preserves_existing_text_structure() -> None:
    title = plain("*Door*")
    status = md("**Approved**")
    assert discord(md(t"{title}: {status}")) == r"\*Door\*: **Approved**"
    assert discord(md("{title}: {status}", title=title, status=status)) == r"\*Door\*: **Approved**"


def test_python_values_can_be_formatted_before_capture() -> None:
    assert source_text(md("Took {seconds:.1f}s", seconds=2.54)) == "Took 2.5s"


def test_text_formatting_never_eagerly_resolves_translations() -> None:
    label = tr("Hello", key="test-hello")
    with pytest.raises(TypeError, match="stays deferred"):
        md(t"{label:>10}")


def test_localization_scope_is_nested_and_reset_safe() -> None:
    first, second = Localization("first"), Localization("second")
    original = current_localization()
    with localization_scope(first):
        assert current_localization() is first
        with localization_scope(second):
            assert current_localization() is second
        assert current_localization() is first
    assert current_localization() is original


def test_a_text_cannot_become_a_string_by_accident() -> None:
    message = tr("Hello", key="test-hello")
    with pytest.raises(TypeError, match="no text until resolved"):
        str(message)
    with pytest.raises(TypeError, match="no text until resolved"):
        _ = f"{message}!"


def test_composition_stays_deferred_until_the_render_locale() -> None:
    name = "*Ann*"
    joined = Text.join("\n", [tr("Hello", key="test-hello"), md("**trusted**"), tr(t"Bye {name}", key="test-bye")])
    with localization_scope(french()):
        assert discord(joined) == "Bonjour\n**trusted**\nAu revoir \\*Ann\\*"
    assert source_text(joined) == "Hello\n**trusted**\nBye \\*Ann\\*"


def test_markdown_fragments_cannot_complete_each_others_delimiters() -> None:
    joined = md("**") + tr("Hello", key="test-hello") + md("**")
    with localization_scope(french()):
        assert discord(joined) == r"\*\*Bonjour\*\*"
        assert discord(strong(tr("Hello", key="test-hello"))) == "**Bonjour**"


def test_plain_text_remains_literal_through_composition() -> None:
    combined = plain("*a*") + " **b**"
    assert source_text(combined) == "*a* **b**"
    assert discord(combined) == r"\*a\* \*\*b\*\*"


def test_mentions_and_timestamps_are_semantic_values() -> None:
    instant = datetime(2026, 8, 22, 14, 30, tzinfo=UTC)
    owner = mention(123)
    updated = timestamp_text(instant, style="R")
    value = tr(t"{owner} updated {updated}", key="updated")
    assert discord(value) == "<@123> updated <t:1787409000:R>"
    assert source_text(value, format="plain") == "@123 updated 2026-08-22T14:30:00+00:00"
    assert discord(plain(t"Updated {instant}")) == "Updated <t:1787409000:f>"


def test_literal_code_and_link_destinations_cannot_inject_structure() -> None:
    label = "[click](https://evil.example)"
    assert source_text(inline_code("a`b")) == "`` a`b ``"
    assert source_text(text_link(label, "https://example.org/(a)")) == (
        r"[\[click\](https://evil.example)](<https://example.org/(a)>)"
    )
    with pytest.raises(ValueError, match="safe destination"):
        text_link("click", "javascript:alert(1)")
