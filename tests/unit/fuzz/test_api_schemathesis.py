"""Bounded Schemathesis command, watchdog, and event classification tests."""

import json
import signal
import subprocess
from pathlib import Path

import httpx
import pytest

from tests.fuzz.api.environment import RunIdentity, RunningApi, SeededIds
from tests.fuzz.api.schemathesis import (
    ANONYMOUS,
    AUTHORIZATION_ENV,
    CHECKS,
    LOCAL_SMOKE,
    CampaignPaths,
    CampaignState,
    EventSummary,
    InvalidCampaignArtifactError,
    Persona,
    SupervisionResult,
    classify_campaign,
    command_for,
    config_text_for,
    read_event_summary,
    subprocess_environment,
    supervise,
    verify_live_contract,
)


class FakeProcess:
    def __init__(self, outcomes: list[int | subprocess.TimeoutExpired]) -> None:
        self.pid = 4321
        self.outcomes = outcomes
        self.timeouts: list[float | None] = []

    def wait(self, timeout: float | None = None) -> int:
        self.timeouts.append(timeout)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, subprocess.TimeoutExpired):
            raise outcome
        return outcome


def paths(tmp_path: Path) -> CampaignPaths:
    return CampaignPaths.create(
        tmp_path,
        run_id="0123456789abcdef0123456789abcdef",
        persona=ANONYMOUS,
        profile=LOCAL_SMOKE,
    )


def test_command_is_single_worker_explicitly_bounded_and_contains_no_credential(tmp_path: Path) -> None:
    campaign_paths = paths(tmp_path)
    credential = "Bearer synthetic-secret-canary"
    persona = Persona(name="service", authorization=credential)

    command = command_for(
        python_executable=Path("/venv/bin/python"),
        base_url="http://127.0.0.1:8123",
        paths=campaign_paths,
        profile=LOCAL_SMOKE,
        seed=42,
    )
    config = config_text_for(campaign_paths, persona)

    assert command[:7] == [
        "/venv/bin/python",
        "-m",
        "schemathesis.cli",
        "--config-file",
        str(campaign_paths.config),
        "--no-color",
        "run",
    ]
    assert command[7] == "http://127.0.0.1:8123/openapi.json"
    assert command[command.index("--workers") + 1] == "1"
    assert command[command.index("--phases") + 1] == "fuzzing"
    assert command[command.index("--checks") + 1] == ",".join(CHECKS)
    assert command[command.index("--max-examples") + 1] == "1"
    assert command[command.index("--request-timeout") + 1] == "2"
    assert command[command.index("--max-response-time") + 1] == "2"
    assert credential not in " ".join(command)
    assert credential not in config
    assert f"${{{AUTHORIZATION_ENV}}}" in config


def test_subprocess_environment_is_allowlisted_and_holds_persona_secret_only() -> None:
    credential = "Bearer synthetic-secret-canary"
    environment = subprocess_environment(Persona(name="service", authorization=credential))

    assert environment[AUTHORIZATION_ENV] == credential
    assert "HOME" not in environment
    assert "PATH" not in environment
    assert set(environment) == {AUTHORIZATION_ENV, "NO_PROXY", "PYTHONUTF8", "PYTHONUNBUFFERED", "no_proxy"}


def test_watchdog_returns_before_budget_without_signals() -> None:
    process = FakeProcess([0])
    signals: list[tuple[int, signal.Signals]] = []

    result = supervise(
        process, budget_seconds=20, cleanup_grace_seconds=3, signal_group=lambda pid, sig: signals.append((pid, sig))
    )

    assert result == SupervisionResult(returncode=0, timed_out=False, forced_kill=False)
    assert process.timeouts == [20]
    assert signals == []


def test_watchdog_interrupts_then_force_kills_the_process_group() -> None:
    process = FakeProcess([subprocess.TimeoutExpired("st", 20), subprocess.TimeoutExpired("st", 3), -signal.SIGKILL])
    signals: list[tuple[int, signal.Signals]] = []

    result = supervise(
        process, budget_seconds=20, cleanup_grace_seconds=3, signal_group=lambda pid, sig: signals.append((pid, sig))
    )

    assert result == SupervisionResult(returncode=-signal.SIGKILL, timed_out=True, forced_kill=True)
    assert process.timeouts == [20, 3, None]
    assert signals == [(4321, signal.SIGINT), (4321, signal.SIGKILL)]


@pytest.mark.parametrize(
    ("supervision", "events", "expected"),
    [
        (
            SupervisionResult(returncode=1, timed_out=True, forced_kill=False),
            None,
            CampaignState.BUDGET_EXHAUSTED,
        ),
        (
            SupervisionResult(returncode=0, timed_out=False, forced_kill=False),
            EventSummary(initialized=True, loaded=True, terminal=True, completed=True),
            CampaignState.PASS,
        ),
        (
            SupervisionResult(returncode=1, timed_out=False, forced_kill=False),
            EventSummary(initialized=True, loaded=True, terminal=True, completed=True, finding=True),
            CampaignState.PRODUCT_FINDING,
        ),
        (
            SupervisionResult(returncode=1, timed_out=False, forced_kill=False),
            EventSummary(initialized=True, loaded=True, terminal=True, completed=True, harness_error=True),
            CampaignState.HARNESS_ERROR,
        ),
        (
            SupervisionResult(returncode=1, timed_out=False, forced_kill=False),
            EventSummary(initialized=True, loaded=True, terminal=True, completed=True, infrastructure_error=True),
            CampaignState.INFRASTRUCTURE_ERROR,
        ),
        (
            SupervisionResult(returncode=0, timed_out=False, forced_kill=False),
            EventSummary(initialized=True),
            CampaignState.HARNESS_ERROR,
        ),
        (
            SupervisionResult(returncode=1, timed_out=False, forced_kill=False),
            None,
            CampaignState.HARNESS_ERROR,
        ),
    ],
)
def test_campaign_classification_uses_events_not_ambiguous_exit_code(
    supervision: SupervisionResult, events: EventSummary | None, expected: CampaignState
) -> None:
    assert classify_campaign(supervision, events) is expected


def test_event_reader_distinguishes_findings_from_transport_errors(tmp_path: Path) -> None:
    event_path = tmp_path / "events.ndjson"
    records = [
        {"Initialize": {"schemathesis_version": "4.24.2"}},
        {"LoadingFinished": {"duration": 0.1}},
        {"ScenarioFinished": {"status": "failure"}},
        {"NonFatalError": {"value": {"type": "ReadTimeout", "message": "timed out"}}},
        {"EngineFinished": {"running_time": 1, "stop_reason": "completed"}},
    ]
    event_path.write_text("".join(f"{json.dumps(record)}\n" for record in records), encoding="utf-8")

    summary = read_event_summary(event_path)

    assert summary.initialized
    assert summary.loaded
    assert summary.terminal
    assert summary.completed
    assert summary.finding
    assert summary.infrastructure_error
    assert not summary.harness_error


def test_event_reader_refuses_malformed_ndjson(tmp_path: Path) -> None:
    event_path = tmp_path / "events.ndjson"
    event_path.write_text('{"Initialize": {}}\n{"EngineFinished":', encoding="utf-8")

    with pytest.raises(InvalidCampaignArtifactError, match="malformed"):
        read_event_summary(event_path)


def test_event_reader_refuses_an_unexpected_schemathesis_version(tmp_path: Path) -> None:
    event_path = tmp_path / "events.ndjson"
    records = [
        {"Initialize": {"schemathesis_version": "4.25.0"}},
        {"LoadingFinished": {"duration": 0.1}},
        {"EngineFinished": {"running_time": 1, "stop_reason": "completed"}},
    ]
    event_path.write_text("".join(f"{json.dumps(record)}\n" for record in records), encoding="utf-8")

    summary = read_event_summary(event_path)

    assert summary.harness_error
    assert not summary.initialized


async def test_live_contract_preflight_compares_the_attested_endpoint(tmp_path: Path) -> None:
    document = {"openapi": "3.1.0", "paths": {}}
    canonical = tmp_path / "openapi.json"
    canonical.write_text(json.dumps(document), encoding="utf-8")
    requested: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request)
        return httpx.Response(200, json=document)

    running = _unused_running_api()
    await verify_live_contract(running, canonical, transport=httpx.MockTransport(handler))

    assert requested[0].url == httpx.URL("http://127.0.0.1:8123/openapi.json")


def _unused_running_api() -> RunningApi:
    async def unused() -> None:
        raise AssertionError("not used")

    async def unused_checksum() -> str:
        raise AssertionError("not used")

    async def unused_seed() -> SeededIds:
        raise AssertionError("not used")

    async def unused_attestation():
        raise AssertionError("not used")

    from tests.fuzz.api.environment import ResetHooks

    return RunningApi(
        identity=RunIdentity.generate(),
        base_url="http://127.0.0.1:8123",
        network_id="unused",
        read_attestation=unused_attestation,
        reset_hooks=ResetHooks(
            unused,
            unused,
            unused,
            unused_seed,
            unused,
            unused,
            unused_checksum,
            SeededIds(1, 2, 3, 4, 1, "alice", "bob", "alice", "bob", "pending", "admin", "api"),
            "unused",
        ),
    )
