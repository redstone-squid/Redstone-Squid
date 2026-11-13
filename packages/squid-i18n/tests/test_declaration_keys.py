"""Explicit keys preserve Fluent's first-line parsing rules."""

import pytest

from squid_i18n import source_text, tr
from squid_i18n.compiler import compile_template


@pytest.mark.parametrize("body", ["**Hello**", "*Hello*", ". Hello", "Hello\n.label = Label"])
def test_explicit_keys_keep_the_same_declaration_as_embedded_keys(body: str) -> None:
    explicit = compile_template((body,), (), key="greeting")
    embedded = compile_template(("greeting = " + body.replace("\n", "\n    "),), ())
    assert explicit == embedded


def test_explicit_key_supports_an_attribute_only_declaration() -> None:
    message = tr(".label = Label", key="greeting")
    assert source_text(message.attribute("label")) == "Label"
