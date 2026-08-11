"""Bounded local Atheris launcher contracts."""

from pathlib import Path

import pytest

from scripts.run_fuzz_target import LONG_RUN_SECONDS, TARGETS, command_for, parse_run


def test_parse_run_defaults_to_the_smoke_budget() -> None:
    run = parse_run(("target=search_parser",), allow_long_run=False)

    assert run.target == TARGETS["search_parser"]
    assert run.seconds == 20


@pytest.mark.parametrize(
    "settings",
    [
        (),
        ("target=unknown",),
        ("target=cursor_codec", "seconds=0"),
        ("target=cursor_codec", "seconds=not-an-integer"),
        ("target=cursor_codec", "target=version_parser"),
    ],
)
def test_parse_run_rejects_unknown_or_unbounded_settings(settings: tuple[str, ...]) -> None:
    with pytest.raises(ValueError, match=r"[Ff]uzz"):
        parse_run(settings, allow_long_run=False)


def test_parse_run_requires_an_explicit_override_for_long_runs() -> None:
    settings = ("target=version_parser", f"seconds={LONG_RUN_SECONDS + 1}")

    with pytest.raises(ValueError, match="--allow-long-run"):
        parse_run(settings, allow_long_run=False)
    assert parse_run(settings, allow_long_run=True).seconds == LONG_RUN_SECONDS + 1


def test_command_uses_separate_corpus_and_artifact_directories(tmp_path: Path) -> None:
    seeds = tmp_path / "tests" / "fuzz" / "corpus" / "cursor_codec"
    seeds.mkdir(parents=True)
    (seeds / "valid").write_bytes(b"cursor")
    run = parse_run(("target=cursor_codec", "seconds=7"), allow_long_run=False)

    command = command_for(run, tmp_path)

    assert command[2:4] == ["tests.fuzz.fuzz_cursor_codec", str(tmp_path / ".fuzz/corpus/cursor_codec")]
    assert "-max_total_time=7" in command
    assert command[-1] == f"-artifact_prefix={tmp_path}/.fuzz/artifacts/cursor_codec/"
    assert (tmp_path / ".fuzz/corpus/cursor_codec/valid").read_bytes() == b"cursor"
