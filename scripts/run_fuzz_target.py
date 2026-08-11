"""Prepare and launch one allowlisted Atheris target with an explicit time budget."""

import argparse
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SECONDS = 20
LONG_RUN_SECONDS = 300
MAX_SECONDS = 7_200


@dataclass(frozen=True, slots=True)
class FuzzTarget:
    """One pure fuzz entry point and its independent native corpus."""

    module: str
    corpus: str


@dataclass(frozen=True, slots=True)
class FuzzRun:
    """Validated local execution request."""

    target: FuzzTarget
    seconds: int


TARGETS = {
    "cursor_codec": FuzzTarget("tests.fuzz.fuzz_cursor_codec", "cursor_codec"),
    "search_parser": FuzzTarget("tests.fuzz.fuzz_search_parser", "search_parser"),
    "version_parser": FuzzTarget("tests.fuzz.fuzz_version_parser", "version_parser"),
}


def parse_run(settings: Sequence[str], *, allow_long_run: bool) -> FuzzRun:
    """Validate `key=value` settings accepted by the generic Just recipe."""
    values: dict[str, str] = {}
    for setting in settings:
        key, separator, value = setting.partition("=")
        if not separator or key not in {"target", "seconds"} or key in values or not value:
            msg = f"Invalid fuzz setting {setting!r}; use target=<name> and seconds=<integer>."
            raise ValueError(msg)
        values[key] = value

    target_name = values.get("target")
    if target_name not in TARGETS:
        choices = ", ".join(sorted(TARGETS))
        msg = f"Unknown or missing fuzz target; choose one of: {choices}."
        raise ValueError(msg)
    try:
        seconds = int(values.get("seconds", str(DEFAULT_SECONDS)))
    except ValueError:
        msg = "Fuzz seconds must be an integer."
        raise ValueError(msg) from None
    if not 1 <= seconds <= MAX_SECONDS:
        msg = f"Fuzz seconds must be between 1 and {MAX_SECONDS}."
        raise ValueError(msg)
    if seconds > LONG_RUN_SECONDS and not allow_long_run:
        msg = f"Runs longer than {LONG_RUN_SECONDS} seconds require --allow-long-run."
        raise ValueError(msg)
    return FuzzRun(TARGETS[target_name], seconds)


def command_for(run: FuzzRun, root: Path) -> list[str]:
    """Prepare native directories and return the exact bounded subprocess command."""
    corpus = root / ".fuzz" / "corpus" / run.target.corpus
    artifacts = root / ".fuzz" / "artifacts" / run.target.corpus
    seeds = root / "tests" / "fuzz" / "corpus" / run.target.corpus
    corpus.mkdir(parents=True, exist_ok=True)
    artifacts.mkdir(parents=True, exist_ok=True)
    for seed in seeds.iterdir():
        if seed.is_file():
            shutil.copy2(seed, corpus / seed.name)
    return [
        sys.executable,
        "-m",
        run.target.module,
        str(corpus),
        f"-max_total_time={run.seconds}",
        "-max_len=4096",
        f"-artifact_prefix={artifacts}/",
    ]


def main(arguments: Sequence[str] | None = None) -> int:
    """Run one bounded fuzz target and return the engine's exit status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("settings", nargs="*", metavar="key=value")
    parser.add_argument("--allow-long-run", action="store_true")
    parsed = parser.parse_args(arguments)
    try:
        run = parse_run(parsed.settings, allow_long_run=parsed.allow_long_run)
    except ValueError as error:
        parser.error(str(error))
    root = Path(__file__).resolve().parents[1]
    return subprocess.run(command_for(run, root), check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
