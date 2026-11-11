"""Maintain each distribution's Fluent resources and source revision manifest."""

import argparse
import json
from pathlib import Path

from squid_i18n.catalogs import artifacts, collect, validate
from squid_i18n.compiler import parse_resource
from squid_i18n.errors import CatalogError


def distributions(root: Path) -> list[tuple[Path, Path]]:
    """Locate application and workspace resources using their distribution boundaries."""
    owners = [(root / "squid", root / "locales")]
    owners.extend(
        (source, source / "locales")
        for source in sorted((root / "packages").glob("*/src/*"))
        if source.is_dir() and source.name != "squid_i18n"
    )
    return owners


def catalog_matches(english: Path, source: str, metadata: str) -> bool:
    """Compare generated content while allowing outdated source-location metadata."""
    source_path = english / "messages.ftl"
    metadata_path = english / "messages.json"
    if not source_path.exists() or not metadata_path.exists():
        return False
    # Extraction writes source locations as top-level Fluent comments.
    stored_source = "\n".join(line for line in source_path.read_text().splitlines() if not line.startswith("#"))
    expected_source = "\n".join(line for line in source.splitlines() if not line.startswith("#"))
    stored_metadata = json.loads(metadata_path.read_text())
    expected_metadata = json.loads(metadata)
    for manifest in (stored_metadata, expected_metadata):
        for entry in manifest.values():
            entry.pop("locations", None)
    return stored_source == expected_source and stored_metadata == expected_metadata


def run(command: str, root: Path, locale: str | None = None) -> list[str]:
    reports: list[str] = []
    for source_root, locales in distributions(root):
        found = collect(source_root.rglob("*.py"))
        if not found and not locales.exists():
            continue
        english = locales / "en"
        supplemental = tuple(path.read_text() for path in sorted(english.glob("*.ftl")) if path.name != "messages.ftl")
        source, metadata = artifacts(found, supplemental)
        if command in {"extract", "update", "init"}:
            english.mkdir(parents=True, exist_ok=True)
            (english / "messages.ftl").write_text(source)
            (english / "messages.json").write_text(metadata)
        elif not catalog_matches(english, source, metadata):
            msg = f"{english}: generated catalog is stale; run squid-i18n extract"
            raise CatalogError(msg)
        revisions = {key: entry["fingerprint"] for key, entry in json.loads(metadata).items()}
        if command == "init":
            if locale is None or locale == "en":
                msg = "Choose a translation locale other than en"
                raise CatalogError(msg)
            target = locales / locale
            target.mkdir(parents=True, exist_ok=True)
            (target / "messages.ftl").touch(exist_ok=True)
        for directory in sorted(locales.iterdir()):
            if not directory.is_dir() or directory.name == "en":
                continue
            files = sorted(directory.glob("*.ftl"))
            if not files:
                continue
            resources = tuple(path.read_text() for path in files)
            validate(directory.name, resources, (item.declaration for item in found))
            ids = {
                entry.id.name
                for resource in resources
                for entry in parse_resource(resource).body
                if hasattr(entry, "id")
            }
            revision_path = directory / "source-revisions.json"
            previous = json.loads(revision_path.read_text()) if revision_path.exists() else {}
            changed = sorted(key for key in ids & revisions.keys() if previous.get(key) != revisions[key])
            removed = sorted(previous.keys() - revisions.keys())
            reports.extend(f"{directory}: review {key}" for key in changed)
            reports.extend(f"{directory}: obsolete {key}" for key in removed)
            if command == "review" and (locale is None or locale == directory.name):
                revision_path.write_text(
                    json.dumps({key: revisions[key] for key in sorted(ids & revisions.keys())}, indent=2) + "\n"
                )
    return reports


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("extract", "check", "update", "init", "review"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--locale")
    args = parser.parse_args()
    try:
        for report in run(args.command, args.root, args.locale):
            print(report)
    except (CatalogError, ValueError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
