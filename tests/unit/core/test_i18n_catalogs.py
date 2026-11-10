"""Shipped catalogs are validated from source declarations, without build artifacts."""

from pathlib import Path

from squid.core.i18n import localization_for
from squid_i18n.catalogs import collect, validate
from squid_i18n.cli import run


def test_generated_catalogs_match_declarations_and_translations_are_reviewed() -> None:
    assert run("check", Path(".")) == []


def test_combined_application_and_framework_catalogs_compile() -> None:
    declarations = [item.declaration for item in collect(Path("squid").rglob("*.py"))]
    for locale in ("en", "zh-CN"):
        snapshot = localization_for(locale)
        validate(locale, snapshot.resources, declarations)
