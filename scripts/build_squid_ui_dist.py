"""Build every lockstep Squid UI distribution into one directory."""

import argparse
import subprocess
from pathlib import Path

DISTRIBUTIONS = (
    "squid-reactivity",
    "squid-replication",
    "squid-storage",
    "squid-ui",
    "squid-ui-discord",
    "squid-ui-widgets",
)


def main() -> None:
    """Build wheels and source distributions through uv's isolated backend path."""
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for distribution in DISTRIBUTIONS:
        subprocess.run(
            ["uv", "build", "--package", distribution, "--out-dir", str(args.output)],
            check=True,
        )


if __name__ == "__main__":
    main()
