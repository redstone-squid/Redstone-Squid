"""Static authoring and translation checks share the runtime grammar."""

import pytest

from squid_i18n import CatalogError, DeclarationError
from squid_i18n.catalogs import artifacts, extract, validate


def test_extraction_never_evaluates_python() -> None:
    found = extract('raise RuntimeError("do not import")\ntr(t"hello = Hello {(name := compute())}")', "sample.py")
    source, manifest = artifacts(found)
    assert "hello = Hello { $name }" in source
    assert "sample.py:2" in manifest
    assert found[0].declaration.slots[0].name == "name"


def test_attribute_only_and_options_extract() -> None:
    found = extract('tr(t"""price =\n    .label = {NUMBER(total, style="currency", currency="USD")}\n""")')
    source, manifest = artifacts(found)
    assert ".label = { NUMBER($total) }" in source
    assert "currency" in manifest
    assert "USD" not in source


@pytest.mark.parametrize(
    "source",
    [
        'tr("literal")',
        "tr(template)",
        'tr(t"key = {name!r}")',
        'tr(t"key = {NUMBER(n, use_grouping=group)}")',
        'tr(t"key = {user.name}")',
        'tr(t"key = {{ $missing }}")',
    ],
)
def test_invalid_authoring_fails_before_import(source: str) -> None:
    with pytest.raises(DeclarationError):
        extract(source)


def test_conflicting_duplicate_is_rejected() -> None:
    with pytest.raises(CatalogError, match="Conflicting"):
        artifacts(extract('tr(t"hello = Hello")\ntr(t"hello = Goodbye")'))


@pytest.mark.parametrize(
    "translation",
    [
        "hello = { $missing }",
        "hello = { helper }\nhelper = { $missing }",
        "hello = { absent }",
        "hello = { hello }",
        "hello = { INVALID($name) }",
        'hello = { NUMBER($name, style: "percent") }',
        "hello = first\nhello = second",
    ],
)
def test_invalid_translation_is_rejected(translation: str) -> None:
    declarations = [item.declaration for item in extract('tr(t"hello = Hello {name}")')]
    with pytest.raises(CatalogError):
        validate("de", (translation,), declarations)


def test_terms_and_missing_attributes_are_allowed() -> None:
    declarations = [item.declaration for item in extract('tr(t"""hello = Hello {name}\n    .label = Name\n""")')]
    validate("de", ("-brand = Squid\nhello = { -brand } { $name }",), declarations)
