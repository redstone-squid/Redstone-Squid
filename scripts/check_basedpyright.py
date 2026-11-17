"""Check installed workspace packages and repository code with consistent import identities."""

import json
import subprocess
import sys
import sysconfig
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    config = dict(project["tool"]["basedpyright"])
    site_packages = Path(sysconfig.get_path("purelib"))
    packages: set[Path] = set()
    auxiliary: list[str] = []
    for member in project["tool"]["uv"]["workspace"]["members"]:
        metadata = tomllib.loads((ROOT / member / "pyproject.toml").read_text())
        module = (
            metadata.get("tool", {})
            .get("uv", {})
            .get("build-backend", {})
            .get("module-name", metadata["project"]["name"].replace("-", "_"))
        )
        packages.add(site_packages / module.split(".")[0])
        auxiliary.extend(
            str(path.relative_to(ROOT))
            for path in (ROOT / member).iterdir()
            if path.is_dir() and path.name != "src" and not path.name.startswith(".")
        )
    config["include"] = ["app.py", "squid", "tests", "scripts", *auxiliary, *map(str, sorted(packages))]
    # The installed packages are under .venv; explicit includes keep other hidden directories out.
    config["exclude"] = [pattern for pattern in config["exclude"] if pattern != "**/.*"]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", prefix=".basedpyright-", dir=ROOT) as stream:
        json.dump(config, stream)
        stream.flush()
        return subprocess.run(
            [
                "uvx",
                "--from",
                "basedpyright==1.39.9",
                "basedpyright",
                "--project",
                stream.name,
                "--pythonpath",
                sys.executable,
                "--level=error",
                *sys.argv[1:],
            ],
            cwd=ROOT,
            check=False,
        ).returncode


if __name__ == "__main__":
    sys.exit(main())
