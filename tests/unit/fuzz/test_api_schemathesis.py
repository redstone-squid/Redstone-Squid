"""Bounded Schemathesis command, watchdog, and event classification tests."""

import json
import signal
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from tests.fuzz.api.environment import RunIdentity, RunningApi, SeededIds
from tests.fuzz.api.schemathesis import (
    ANONYMOUS,
    AUTHORIZATION_ENV,
    CHECKS,
    FORCE_KILL_SIGNAL,
    LOCAL_SMOKE,
    CampaignPaths,
    CampaignStage,
    CampaignState,
    EventSummary,
    InvalidCampaignArtifactError,
    Persona,
    SupervisionResult,
    classify_campaign,
    command_for,
    config_text_for,
    read_event_summary,
    run_campaign,
    subprocess_environment,
    supervise,
    verify_live_contract,
)


class FakeProcess:
    """Script wait results and record timeouts without launching or signaling a process."""

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
        str(Path("/venv/bin/python")),
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
    process = FakeProcess(
        [
            subprocess.TimeoutExpired("st", 20),
            subprocess.TimeoutExpired("st", 3),
            -FORCE_KILL_SIGNAL,
        ]
    )
    signals: list[tuple[int, signal.Signals]] = []

    result = supervise(
        process, budget_seconds=20, cleanup_grace_seconds=3, signal_group=lambda pid, sig: signals.append((pid, sig))
    )

    assert result == SupervisionResult(returncode=-FORCE_KILL_SIGNAL, timed_out=True, forced_kill=True)
    assert process.timeouts == [20, 3, None]
    assert signals == [(4321, signal.SIGINT), (4321, FORCE_KILL_SIGNAL)]


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
    event_path.write_text('{"Initialize": {"schemathesis_version": "4.24.2"}}\n{"EngineFinished":', encoding="utf-8")

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
    """Supply only the URL; ownership and reset hooks must remain unused by contract comparison."""

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


@pytest.mark.parametrize(
    "record",
    [
        {"FutureEvent": {}},
        {"ScenarioFinished": {"status": "future"}},
        {"PhaseFinished": {}},
        {"FatalError": {"value": {"type": "ReadTimeout", "message": "wrong key"}}},
        {"NonFatalError": {"exception": {"type": "ReadTimeout", "message": "wrong key"}}},
    ],
)
def test_event_reader_rejects_unknown_events_and_invalid_fields(tmp_path: Path, record: dict) -> None:
    event_path = tmp_path / "events.ndjson"
    event_path.write_text(json.dumps(record))
    with pytest.raises(InvalidCampaignArtifactError):
        read_event_summary(event_path)


@pytest.mark.parametrize(("kind", "key"), [("FatalError", "exception"), ("NonFatalError", "value")])
def test_event_reader_decodes_each_pinned_error_envelope(tmp_path: Path, kind: str, key: str) -> None:
    event_path = tmp_path / "events.ndjson"
    event_path.write_text(json.dumps({kind: {key: {"type": "ReadTimeout", "message": "timed out"}}}))
    assert read_event_summary(event_path).infrastructure_error


@pytest.mark.parametrize("stage", list(CampaignStage))
async def test_campaign_failure_retains_redacted_stage_and_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: CampaignStage
) -> None:
    running = _unused_running_api()
    credential = "Bearer secret-diagnostic-canary"
    persona = Persona(name="service", authorization=credential)
    cause = ValueError(f"credential={credential}")
    error = RuntimeError(f"failed with {running.identity.sentinel} and {credential}")
    error.__cause__ = cause
    target = "tests.fuzz.api.schemathesis."
    monkeypatch.setattr(
        RunningApi, "attest", AsyncMock(side_effect=error if stage is CampaignStage.ATTESTATION else None)
    )
    monkeypatch.setattr(RunningApi, "reset", AsyncMock(side_effect=error if stage is CampaignStage.RESET else None))
    monkeypatch.setattr(
        target + "verify_live_contract", AsyncMock(side_effect=error if stage is CampaignStage.CONTRACT else None)
    )

    def launch(*args):
        if stage is CampaignStage.LAUNCH:
            raise error
        return SupervisionResult(returncode=0, timed_out=False, forced_kill=False)

    monkeypatch.setattr(target + "_launch_and_supervise", launch)
    # Use the parser's declared failure, with the chained underlying exception.
    malformed = InvalidCampaignArtifactError(str(error))
    malformed.__cause__ = cause

    def events(path):
        raise malformed

    monkeypatch.setattr(target + "read_event_summary", events)
    outcome = await run_campaign(
        running, artifact_root=tmp_path, committed_contract_path=tmp_path / "openapi.json", persona=persona
    )
    assert outcome.state is CampaignState.HARNESS_ERROR
    raw = (outcome.paths.root / "diagnostic.json").read_text()
    diagnostic = json.loads(raw)
    assert diagnostic["stage"] == stage.value
    assert diagnostic["cause"].startswith("ValueError:")
    assert diagnostic["traceback"]
    assert "secret-diagnostic-canary" not in raw
    assert running.identity.sentinel not in raw


def test_diagnostic_artifacts_are_bounded_for_multibyte_and_control_input(tmp_path: Path) -> None:
    campaign_paths = paths(tmp_path)
    error = RuntimeError("\x00界" * 100_000)
    campaign_paths.record_failure(CampaignStage.LAUNCH, error, secrets=())
    raw = (campaign_paths.root / "diagnostic.json").read_bytes()
    assert len(raw) <= 32_768
    assert json.loads(raw)["type"] == "RuntimeError"


def test_event_reader_enforces_report_and_event_size_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    event_path = tmp_path / "events.ndjson"
    event_path.write_text(json.dumps({"LoadingFinished": {"padding": "x" * 100}}))
    monkeypatch.setattr("tests.fuzz.api.schemathesis.MAX_NDJSON_BYTES", 20)
    with pytest.raises(InvalidCampaignArtifactError, match="size limit"):
        read_event_summary(event_path)
    monkeypatch.setattr("tests.fuzz.api.schemathesis.MAX_NDJSON_BYTES", 1000)
    monkeypatch.setattr("tests.fuzz.api.schemathesis.MAX_NDJSON_LINE_BYTES", 20)
    with pytest.raises(InvalidCampaignArtifactError, match="oversized"):
        read_event_summary(event_path)


def test_failure_limit_completion_retains_product_findings(tmp_path: Path) -> None:
    event_path = tmp_path / "events.ndjson"
    records = [
        {"Initialize": {"schemathesis_version": "4.24.2"}},
        {"LoadingFinished": {}},
        {"ScenarioFinished": {"status": "failure"}},
        {"EngineFinished": {"stop_reason": "failure_limit"}},
    ]
    event_path.write_text("\n".join(map(json.dumps, records)))
    assert (
        classify_campaign(
            SupervisionResult(returncode=1, timed_out=False, forced_kill=False), read_event_summary(event_path)
        )
        is CampaignState.PRODUCT_FINDING
    )
