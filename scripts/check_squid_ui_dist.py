"""Validate the contents and metadata of a built Squid UI release set."""

import argparse
import email
import tarfile
import zipfile
from pathlib import Path

from packaging.requirements import Requirement

VERSION = "0.1.0a1"
DISTRIBUTIONS = {
    "squid-reactivity": "squid_reactivity",
    "squid-replication": "squid_replication",
    "squid-storage": "squid_storage",
    "squid-ui": "squid_ui",
    "squid-ui-discord": "squid_ui_discord",
    "squid-ui-widgets": "squid_ui_widgets",
}


def _validate_wheel(path: Path, distribution: str, package: str) -> None:
    with zipfile.ZipFile(path) as wheel:
        members = wheel.namelist()
        metadata_names = [name for name in members if name.endswith(".dist-info/METADATA")]
        assert len(metadata_names) == 1, f"{path.name}: expected one METADATA file"
        metadata = email.message_from_bytes(wheel.read(metadata_names[0]))
        assert metadata["Name"] == distribution
        assert metadata["Version"] == VERSION
        assert metadata["License-Expression"] == "MIT"
        assert metadata["Requires-Python"] == ">=3.14"
        assert metadata.get_all("License-File") == ["LICENSE"]
        assert f"{package}/py.typed" in members
        assert any(name.endswith(".dist-info/licenses/LICENSE") for name in members)
        for raw_requirement in metadata.get_all("Requires-Dist", []):
            requirement = Requirement(raw_requirement)
            if requirement.name in DISTRIBUTIONS:
                assert str(requirement.specifier) == f"=={VERSION}"


def _validate_sdist(path: Path, distribution: str, package: str) -> None:
    prefix = f"{package}-{VERSION}"
    with tarfile.open(path, "r:gz") as sdist:
        members = {member.name for member in sdist.getmembers()}
    for required in ("LICENSE", "README.md", "pyproject.toml", f"src/{package}/py.typed"):
        assert f"{prefix}/{required}" in members, f"{path.name}: missing {required}"


def main() -> None:
    """Check that one complete, internally compatible release set was built."""
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    expected_files: set[str] = set()
    for distribution, package in DISTRIBUTIONS.items():
        wheel_name = f"{package}-{VERSION}-py3-none-any.whl"
        sdist_name = f"{package}-{VERSION}.tar.gz"
        expected_files.update({wheel_name, sdist_name})
        _validate_wheel(args.directory / wheel_name, distribution, package)
        _validate_sdist(args.directory / sdist_name, distribution, package)

    actual_files = {path.name for path in args.directory.iterdir() if path.name != ".gitignore"}
    assert actual_files == expected_files


if __name__ == "__main__":
    main()
