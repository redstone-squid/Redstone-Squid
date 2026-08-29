"""Move every Squid UI distribution and its documented pins to a new version.

The suite releases in lockstep, so the version literal appears in each package manifest,
in the intra-suite ``==`` pins, in install instructions, and in the metadata tests that
keep those manifests honest. This script rewrites all of them in one pass; the current
version is read from ``packages/squid-ui/pyproject.toml`` rather than passed in, so a
half-applied bump cannot be applied twice.

Run ``uv lock`` afterwards; the lockfile records the workspace versions.
"""

import argparse
import re
import sys
import tomllib
from pathlib import Path

DISTRIBUTIONS = (
    "squid-reactivity",
    "squid-replication",
    "squid-storage",
    "squid-ui",
    "squid-ui-discord",
    "squid-ui-slack",
    "squid-ui-widgets",
)
# Final releases and alpha/beta/rc pre-releases, matching check_squid_ui_release.py.
VERSION_PATTERN = r"[0-9]+\.[0-9]+\.[0-9]+(?:(?:a|b|rc)[0-9]+)?"

# Files that must carry the release version literal: the manifests and the metadata
# tests that pin them. One of these losing the literal means the release process moved
# and this script must be updated with it.
REQUIRED_FILES = (
    *(Path("packages") / name / "pyproject.toml" for name in DISTRIBUTIONS),
    Path("packages/squid-ui/tests/test_public_api.py"),
    Path("packages/squid-ui-discord/tests/test_public_api.py"),
    Path("packages/squid-ui-slack/tests/test_public_api.py"),
    Path("packages/squid-ui-widgets/tests/test_public_api.py"),
    Path("tests/architecture/test_boundaries.py"),
)
# Files that may pin the version in install instructions; not every README does.
OPTIONAL_FILES = (
    *(Path("packages") / name / "README.md" for name in DISTRIBUTIONS),
    Path("docs/squid-ui.md"),
    Path("docs/squid-ui-quickstart.md"),
    Path("docs/squid-ui-slack-quickstart.md"),
)
# Files that legitimately carry the literal without pinning the release: adapter
# profiles state a compatibility *floor*, which does not move on every bump.
EXEMPT_FILES = frozenset(
    {
        Path("packages/squid-ui/src/squid_ui/html/target.py"),
        # This script names versions in its own docstring and example text.
        Path("scripts/bump_squid_ui_version.py"),
    }
)


def _current_version() -> str:
    manifest = Path("packages/squid-ui/pyproject.toml")
    return tomllib.loads(manifest.read_text(encoding="utf-8"))["project"]["version"]


def _strays(old: str) -> list[Path]:
    """Files outside the allowlist that still carry the version literal."""
    allowed = {*REQUIRED_FILES, *OPTIONAL_FILES, *EXEMPT_FILES}
    strays: list[Path] = []
    for root in ("packages", "docs", "scripts", "tests"):
        for path in Path(root).rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".toml", ".md", ".json"}:
                continue
            if path in allowed or "plans" in path.parts:
                continue
            if old in path.read_text(encoding="utf-8"):
                strays.append(path)
    return strays


def main() -> None:
    """Rewrite the release version everywhere it is pinned."""
    parser = argparse.ArgumentParser()
    parser.add_argument("version", help="the new lockstep version, for example 0.1.0a2")
    args = parser.parse_args()
    new = args.version
    assert re.fullmatch(VERSION_PATTERN, new), f"{new!r} is not a release or pre-release version"
    old = _current_version()
    if old == new:
        sys.exit(f"the suite is already at {new}")

    # Validate everything before writing anything, so a stale list cannot leave the
    # tree half-bumped.
    for path in REQUIRED_FILES:
        assert old in path.read_text(encoding="utf-8"), f"{path} no longer contains {old}; update REQUIRED_FILES"
    if strays := _strays(old):
        joined = ", ".join(str(path) for path in strays)
        sys.exit(f"{old} appears outside the versioned file list; move or exempt: {joined}")

    for path in (*REQUIRED_FILES, *OPTIONAL_FILES):
        text = path.read_text(encoding="utf-8")
        if count := text.count(old):
            path.write_text(text.replace(old, new), encoding="utf-8")
            print(f"{path}: {count} occurrence(s)")
    print(f"{old} -> {new}; run `uv lock` to record the new workspace versions")


if __name__ == "__main__":
    main()
