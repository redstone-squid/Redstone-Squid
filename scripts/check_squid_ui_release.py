"""Verify that a framework release tag matches every workspace manifest."""

import argparse
import re
import tomllib
from pathlib import Path

DISTRIBUTIONS = (
    "squid-reactivity",
    "squid-replication",
    "squid-storage",
    "squid-ui",
    "squid-ui-discord",
    "squid-ui-widgets",
)
TAG_PREFIX = "squid-ui-v"


def main() -> None:
    """Reject a malformed or version-skewed framework release tag."""
    parser = argparse.ArgumentParser()
    parser.add_argument("tag")
    args = parser.parse_args()
    assert args.tag.startswith(TAG_PREFIX), f"release tag must start with {TAG_PREFIX!r}"
    version = args.tag.removeprefix(TAG_PREFIX)
    assert re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+a[0-9]+", version), "release tag must name an alpha version"

    for distribution in DISTRIBUTIONS:
        manifest = Path("packages") / distribution / "pyproject.toml"
        project = tomllib.loads(manifest.read_text(encoding="utf-8"))["project"]
        assert project["name"] == distribution
        assert project["version"] == version, f"{distribution} is {project['version']}, tag is {version}"

    print(version)


if __name__ == "__main__":
    main()
