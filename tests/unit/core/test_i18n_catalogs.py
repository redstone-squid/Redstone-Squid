"""Translation catalog sanity checks: every .po parses cleanly and its .mo is up to date."""

from pathlib import Path

from babel.messages.mofile import read_mo
from babel.messages.pofile import read_po

from squid.core.i18n import locales_dir


def _po_paths() -> list[Path]:
    """Catalogs as the application itself locates them, not as a second hard-coded walk."""
    root = locales_dir()
    paths = sorted(root.glob("*/LC_MESSAGES/squid.po"))
    assert paths, f"No .po catalogs found under {root}"
    return paths


def _normalized(value: str | list[str] | tuple[str, ...] | None) -> str | tuple[str, ...] | None:
    """Use one representation for Babel's PO tuples and MO lists."""
    return tuple(value) if isinstance(value, list | tuple) else value


def _has_translation(value: str | list[str] | tuple[str, ...] | None) -> bool:
    return any(value) if isinstance(value, list | tuple) else bool(value)


def test_every_catalog_parses_without_errors() -> None:
    for po_path in _po_paths():
        with po_path.open("rb") as handle:
            read_po(handle)


def test_no_translated_entry_is_left_fuzzy() -> None:
    for po_path in _po_paths():
        with po_path.open("rb") as handle:
            catalog = read_po(handle)
        fuzzy = [message.id for message in catalog if message.id and message.fuzzy]
        assert not fuzzy, f"{po_path} has fuzzy (unreviewed) entries: {fuzzy}"


def test_compiled_mo_matches_committed_po_translations() -> None:
    """Catch a `.po` edited without re-running `just i18n-compile`."""
    for po_path in _po_paths():
        mo_path = po_path.with_suffix(".mo")
        assert mo_path.exists(), f"{po_path} has no compiled .mo -- run `just i18n-compile`"

        with po_path.open("rb") as handle:
            po_catalog = read_po(handle)
        with mo_path.open("rb") as handle:
            mo_catalog = read_mo(handle)

        translated = {
            _normalized(message.id): _normalized(message.string)
            for message in po_catalog
            if message.id and _has_translation(message.string) and not message.fuzzy
        }
        compiled = {_normalized(message.id): _normalized(message.string) for message in mo_catalog if message.id}

        for msgid, msgstr in translated.items():
            assert compiled.get(msgid) == msgstr, f"{mo_path} is stale for {msgid!r} -- run `just i18n-compile`"
