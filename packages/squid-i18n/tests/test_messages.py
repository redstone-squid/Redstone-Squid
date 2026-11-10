"""Declaration, binding, formatting, and rendering contracts."""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from squid_i18n import (
    DATETIME,
    NUMBER,
    DeclarationError,
    Localization,
    Markup,
    Message,
    ResolutionError,
    localization_scope,
    plain,
    raw_md,
    resolve_text,
    source_text,
    tr,
)


def test_values_remain_data_and_attributes_share_bindings() -> None:
    name = "}\ninjected = @everyone *x*"
    message = tr(t"greeting = Hello {name}\n    .label = Label {name}")
    assert source_text(message) == "Hello \\}\ninjected = @\u200beveryone \\*x\\*"
    assert source_text(message.attribute("label")).startswith("Label \\}")
    with pytest.raises(DeclarationError, match="Unknown attribute"):
        message.attribute("missing")
    with pytest.raises(TypeError):
        str(message)


@pytest.mark.parametrize("count", [0, 1, 2, 10000, Decimal("1.0")])
def test_named_values_and_number_expressions_share_one_binding(count: int | Decimal) -> None:
    message = tr(t"""
        builds = {{ {count} ->
            [one] {NUMBER(count, use_grouping=False)} build
           *[other] {NUMBER(count, use_grouping=False)} builds
        }}
    """)
    assert set(message.bindings) == {"count"}
    assert source_text(message).endswith(" build" if count == 1 and not isinstance(count, Decimal) else " builds")


def test_precision_affects_selection() -> None:
    count = 1
    message = tr(t"""precision = {{ {NUMBER(count, minimum_fraction_digits=1)} ->
        [one] wrong
       *[other] correct
    }}""")
    assert source_text(message) == "correct"


def test_developer_options_stay_with_the_argument() -> None:
    progress = 0.25
    message = tr(t"progress = {NUMBER(progress, style='percent')}")
    assert source_text(message) == "25%"
    assert message.declaration is not None
    assert "style:" not in message.declaration.source
    assert "NUMBER($progress)" in message.declaration.source


def test_walrus_binding_and_nested_messages() -> None:
    values = [1, 2]
    caption = tr(t"caption = {NUMBER(count := len(values))} items")
    assert count == 2
    message = tr(t"outer = [{caption}]")
    assert source_text(message) == "[2 items]"
    assert source_text(Message.join(" / ", [caption, plain("*literal*")])) == r"2 items / \*literal\*"
    trusted = raw_md("**safe**")
    assert source_text(tr(t"trusted = {trusted}")) == "**safe**"


def test_translation_is_deferred_and_source_text_ignores_reader_locale() -> None:
    name = "Alex"
    message = tr(t"hello = Hello {name}\n    .label = Label")
    locale = Localization("zh-CN", ("hello = 你好 { $name }\n",))
    with localization_scope(locale):
        assert message.resolve() == "你好 Alex"
        assert source_text(message) == "Hello Alex"
        assert message.attribute("label").resolve() == "Label"
    assert source_text(replace(message, markup=Markup.PLAIN)) == "Hello Alex"


def test_string_selector_uses_unescaped_value() -> None:
    state = "needs-review"
    message = tr(t"""status = {{ {state} ->
        [needs-review] {state}
       *[other] wrong
    }}""")
    assert source_text(message) == r"needs\-review"


def test_invalid_authoring_is_rejected() -> None:
    count = 2
    with pytest.raises(DeclarationError, match="local variable"):
        tr(t"computed = {len([1, 2])}")
    with pytest.raises(DeclarationError, match="literal"):
        tr(t"options = {NUMBER(count, minimum_fraction_digits=count)}")
    with pytest.raises(DeclarationError, match="format specs"):
        tr(t"format = {count:.1f}")
    with pytest.raises(DeclarationError, match="captured Python"):
        tr(t"missing = {{ $unknown }}")
    with pytest.raises(DeclarationError, match="Conflicting developer"):
        tr(t"conflict = {NUMBER(count, style='percent')} {count}")
    with pytest.raises(ResolutionError, match="no message value"):
        tr(t"attributes =\n    .label = Label").resolve()


def test_dates_are_explicit_and_aware() -> None:
    today = date(2026, 11, 10)
    assert "2026" in source_text(tr(t"date = {DATETIME(today, date_style='long')}"))
    with pytest.raises(DeclarationError, match="aware"):
        DATETIME(datetime(2026, 11, 10))  # noqa: DTZ001 - exercise naive date rejection
    instant = datetime(2026, 11, 10, tzinfo=UTC)
    assert "<t:" in source_text(tr(t"instant = {instant}"))


def test_bad_translation_falls_back_without_logging_values(caplog: pytest.LogCaptureFixture) -> None:
    name = "private-name"
    message = tr(t"hello = Hello {name}")
    locale = Localization("zh-CN", ("hello = { NUMBER($name) }\n",))
    assert resolve_text(message, locale).content == r"Hello private\-name"
    assert "private-name" not in caplog.text


def test_datetime_constructor_does_not_become_a_discord_timestamp() -> None:
    instant = datetime(2026, 11, 10, 12, tzinfo=UTC)
    message = tr(t"date = {DATETIME(instant, date_style='long', time_style='short')}")
    assert "12:00" in source_text(message)
    assert "<t:" not in source_text(message)
    zoned = tr(t"date-zoned = {DATETIME(instant, time_style='short', time_zone='Europe/Brussels')}")
    assert "1:00" in source_text(zoned)


def test_fallback_retains_reader_locale_for_nested_messages() -> None:
    child = tr(t"child = Hello")
    parent = tr(t"parent = Notice: {child}")
    with localization_scope(Localization("fr", ("child = Bonjour",))):
        assert parent.resolve() == "Notice: Bonjour"


def test_plain_diagnostics_remain_plain_through_nested_compositions() -> None:
    name = "*name* @everyone"
    child = tr(t"child = {name}")
    parent = tr(t"parent = {child}") + child
    assert source_text(parent, markup=Markup.PLAIN) == "*name* @everyone*name* @everyone"


def test_multiline_declarations_preserve_newlines_and_blank_lines() -> None:
    name = "Ada"
    message = tr(t"""
        greeting =
            Hello {name}.

            Welcome back.
        """)
    assert source_text(message) == "Hello Ada.\n\nWelcome back."
    assert message.declaration is not None
    assert "\\u000A" not in message.declaration.source
