"""Local API fuzz smoke launcher behavior without Docker or a fuzzer process."""

import json
from pathlib import Path

import pytest

from scripts import run_api_fuzz
from tests.fuzz.api.schemathesis import CampaignOutcome, CampaignPaths, CampaignState


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (CampaignState.PASS, 0),
        (CampaignState.PRODUCT_FINDING, 1),
        (CampaignState.HARNESS_ERROR, 2),
        (CampaignState.INFRASTRUCTURE_ERROR, 2),
        (CampaignState.BUDGET_EXHAUSTED, 2),
        (CampaignState.INCOMPATIBLE_REPLAY, 2),
    ],
)
def test_exit_code_for_preserves_terminal_state_semantics(state: CampaignState, expected: int) -> None:
    assert run_api_fuzz.exit_code_for(state) == expected


def test_main_runs_fixed_smoke_and_prints_bounded_report(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = CampaignPaths(
        root=run_api_fuzz._ROOT / ".fuzz" / "api" / "run",
        config=Path("config"),
        cache=Path("cache"),
        events=Path("events"),
        console=Path("console"),
    )
    observed: list[int] = []

    async def fake_run_smoke(*, seed: int) -> CampaignOutcome:
        observed.append(seed)
        return CampaignOutcome(CampaignState.PASS, paths, returncode=0)

    monkeypatch.setattr(run_api_fuzz, "run_smoke", fake_run_smoke)

    assert run_api_fuzz.main(["--seed", "42"]) == 0
    assert observed == [42]
    assert json.loads(capsys.readouterr().out) == {
        # `str(Path)`, because the runner reports a relative path and its separator is
        # the platform's -- the value under test is which directory, not how it prints.
        "artifact_directory": str(Path(".fuzz") / "api" / "run"),
        "forced_kill": False,
        "returncode": 0,
        "state": "pass",
    }


@pytest.mark.parametrize("seed", ["-1", str(2**64)])
def test_main_rejects_seed_outside_unsigned_64_bit_range(seed: str) -> None:
    with pytest.raises(SystemExit) as raised:
        run_api_fuzz.main(["--seed", seed])

    assert raised.value.code == 2
