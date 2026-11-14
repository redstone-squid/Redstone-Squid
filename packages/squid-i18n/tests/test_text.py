"""Literal construction and deferred template composition."""

from copy import copy, deepcopy
from dataclasses import is_dataclass
from datetime import UTC, datetime

import pytest

from squid_i18n import Localization, Text, discord_text, localization_scope, md, plain, resolve_text, source_text, tr


def test_text_is_an_immutable_slotted_value() -> None:
    text = Text("literal")
    assert not is_dataclass(text)
    assert not hasattr(text, "__dict__")
    with pytest.raises(AttributeError, match="immutable"):
        text._parts = ()
    with pytest.raises(AttributeError, match="immutable"):
        del text._parts
    assert source_text(text) == "literal"


def test_equality_and_hashing_follow_authored_structure() -> None:
    assert Text("literal") == Text("literal")
    assert Text("literal") != Text("different")
    assert Text("literal") != "literal"
    assert Text("literal") != md("literal")
    assert len({Text("literal"), Text("literal")}) == 1
    assert tr("Hello", key="greeting") == tr("Hello", key="greeting")
    assert tr("Hello", key="greeting") != tr("Hello", key="other")


def test_copying_preserves_unresolved_structure() -> None:
    text = Text("literal") + md("**styled**")
    assert copy(text) == text
    assert deepcopy(text) == text
    translated = tr("Hello", key="greeting")
    assert copy(translated) == translated
    assert "greeting" in repr(translated)


@pytest.mark.parametrize("value", ["", "Hello", "**literal** {name}"])
def test_strings_and_interpolated_strings_are_literal(value: str) -> None:
    for text in (Text(value), Text(t"{value}")):
        assert source_text(text) == value
        assert text.declaration is None


def test_template_preserves_structure_and_resolves_for_each_reader() -> None:
    greeting = tr("Hello", key="greeting")
    status = md("**Ready**")
    name = "*Ann*"
    text = Text(t"**{greeting}** {name}: {status}")

    with localization_scope(Localization("fr", ("greeting = Bonjour",))):
        assert text.resolve() == r"\*\*Bonjour\*\* \*Ann\*: **Ready**"
    assert source_text(text, format="plain") == "**Hello** *Ann*: Ready"
    assert text.resolve() == r"\*\*Hello\*\* \*Ann\*: **Ready**"


def test_template_formats_python_values() -> None:
    seconds = 2.54
    name = "Ann"
    assert source_text(Text(t"{name!r}: {seconds:.1f}s")) == "'Ann': 2.5s"
    assert source_text(Text(t"")) == ""
    assert source_text(Text(t"literal")) == "literal"


def test_template_captures_text_values_and_timestamps() -> None:
    class Label:
        def as_text(self) -> Text:
            return md("**Ready**")

    label = Label()
    instant = datetime(2026, 8, 22, 14, 30, tzinfo=UTC)
    text = Text(t"{label}: {instant}")
    assert discord_text(resolve_text(text, Localization())) == "**Ready**: <t:1787409000:f>"


def test_template_rejects_eager_formatting_of_deferred_text() -> None:
    greeting = tr("Hello", key="greeting")
    with pytest.raises(TypeError, match="stays deferred"):
        Text(t"{greeting!s}")
    with pytest.raises(TypeError, match="stays deferred"):
        Text(t"{greeting:>10}")


def test_plain_preserves_its_conversions() -> None:
    assert plain("literal") == Text("literal")
    name = "Ann"
    assert plain(t"Hello {name}") == Text(t"Hello {name}")
    assert source_text(plain(42)) == "42"
    instant = datetime(2026, 8, 22, tzinfo=UTC)
    assert source_text(plain(instant)) == instant.isoformat()


def test_existing_text_is_captured_without_resolving_it() -> None:
    original = tr("Hello", key="greeting")
    captured = Text(original)
    assert captured == original
    assert captured.declaration is original.declaration
    assert captured.bindings is original.bindings
    with localization_scope(Localization("fr", ("greeting = Bonjour",))):
        assert captured.resolve() == "Bonjour"


def test_templates_work_at_composition_and_rendering_boundaries() -> None:
    greeting = tr("Hello", key="greeting")
    name = "*Ann*"
    value = Text.join(t" / ", (t"{greeting}", t"{name}")) + t"!"
    localization = Localization("fr", ("greeting = Bonjour",))
    assert resolve_text(value, localization).plain() == "Bonjour / *Ann*!"
    assert resolve_text(t"{greeting}, {name}", localization).plain() == "Bonjour, *Ann*"


def test_literal_emptiness_does_not_resolve_translations() -> None:
    assert not Text("")
    assert not Text(t"")
    assert Text(" ")
    assert tr("Hello", key="greeting")
