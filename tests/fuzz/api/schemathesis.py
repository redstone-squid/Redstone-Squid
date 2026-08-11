"""Bounded Schemathesis subprocess campaigns against an attested API stack."""

import asyncio
import json
import os
import re
import signal
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO, Protocol, Self

import httpx

from tests.fuzz.api.environment import RunningApi, UnsafeEnvironmentError, validate_target_url

SCHEMATHESIS_VERSION = "4.24.2"
AUTHORIZATION_ENV = "SQUID_FUZZ_AUTHORIZATION"
MAX_NDJSON_BYTES = 16 * 1024 * 1024
MAX_NDJSON_LINE_BYTES = 4 * 1024 * 1024
MAX_CONSOLE_BYTES = 1024 * 1024
MAX_SCHEMA_BYTES = 2 * 1024 * 1024
CHECKS = (
    "not_a_server_error",
    "status_code_conformance",
    "content_type_conformance",
    "response_headers_conformance",
    "response_schema_conformance",
    "negative_data_rejection",
    "positive_data_acceptance",
    "missing_required_header",
    "unsupported_method",
    "use_after_free",
    "ensure_resource_availability",
    "ignored_auth",
)
TRANSPORT_ERROR_TYPES = frozenset(
    {
        "ChunkedEncodingError",
        "ConnectTimeout",
        "ConnectionError",
        "ProxyError",
        "ReadTimeout",
        "RetryError",
        "SSLError",
    }
)
_SAFE_NAME = re.compile(r"[a-z][a-z0-9_-]{0,47}")


class CampaignState(StrEnum):
    """Terminal state shared by API fuzz campaign reporting."""

    PASS = "pass"
    PRODUCT_FINDING = "product_finding"
    HARNESS_ERROR = "harness_error"
    INFRASTRUCTURE_ERROR = "infrastructure_error"
    BUDGET_EXHAUSTED = "budget_exhausted"
    INCOMPATIBLE_REPLAY = "incompatible_replay"


@dataclass(frozen=True, slots=True)
class CampaignProfile:
    """Audited arguments and resource limits for one independent campaign."""

    name: str
    exploration_seconds: float
    phases: tuple[str, ...]
    max_examples: int
    request_timeout_seconds: float = 2
    max_response_time_seconds: float = 2
    rate_limit: str = "30/s"
    cleanup_grace_seconds: float = 3

    def __post_init__(self) -> None:
        if _SAFE_NAME.fullmatch(self.name) is None:
            msg = "Campaign profile names must be short lowercase path components."
            raise ValueError(msg)
        if not 0 < self.exploration_seconds <= 7_200:
            msg = "Campaign exploration time must be between zero and 7200 seconds."
            raise ValueError(msg)
        if not self.phases or set(self.phases) - {"examples", "coverage", "fuzzing"}:
            msg = "Independent campaigns support only examples, coverage, and fuzzing phases."
            raise ValueError(msg)
        if not 1 <= self.max_examples <= 10_000:
            msg = "Campaign max_examples must be between 1 and 10000."
            raise ValueError(msg)
        if self.request_timeout_seconds <= 0 or self.max_response_time_seconds <= 0:
            msg = "Campaign request and response time limits must be positive."
            raise ValueError(msg)
        if not 0 < self.cleanup_grace_seconds <= 30:
            msg = "Campaign cleanup grace must be between zero and 30 seconds."
            raise ValueError(msg)


LOCAL_SMOKE = CampaignProfile(
    name="smoke",
    exploration_seconds=20,
    phases=("fuzzing",),
    max_examples=1,
)


@dataclass(frozen=True, slots=True)
class Persona:
    """One campaign credential shard; secrets are passed only through child environment."""

    name: str
    authorization: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if _SAFE_NAME.fullmatch(self.name) is None:
            msg = "Persona names must be short lowercase path components."
            raise ValueError(msg)
        if self.authorization is not None and not self.authorization:
            msg = "Persona authorization must be non-empty when supplied."
            raise ValueError(msg)


ANONYMOUS = Persona(name="anonymous")


@dataclass(frozen=True, slots=True)
class CampaignPaths:
    """Fresh native artifact paths for one run, persona, and campaign profile."""

    root: Path
    config: Path
    cache: Path
    events: Path
    console: Path

    @classmethod
    def create(cls, artifact_root: Path, *, run_id: str, persona: Persona, profile: CampaignProfile) -> Self:
        """Create a new artifact directory and refuse accidental native-cache reuse."""
        if re.fullmatch(r"[0-9a-f]{32}", run_id) is None:
            msg = "Campaign run IDs must be 128-bit lowercase hexadecimal values."
            raise ValueError(msg)
        root = artifact_root / run_id / persona.name / profile.name
        root.mkdir(parents=True, exist_ok=False)
        cache = root / "native-cache"
        cache.mkdir()
        return cls(
            root=root,
            config=root / "schemathesis.toml",
            cache=cache,
            events=root / "events.ndjson",
            console=root / "console.log",
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class SupervisionResult:
    """Process exit facts independent of Schemathesis event semantics."""

    returncode: int
    timed_out: bool
    forced_kill: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class EventSummary:
    """Security-relevant evidence extracted from a bounded NDJSON report."""

    initialized: bool = False
    loaded: bool = False
    terminal: bool = False
    completed: bool = False
    finding: bool = False
    harness_error: bool = False
    infrastructure_error: bool = False


@dataclass(frozen=True, slots=True)
class CampaignOutcome:
    """One exact terminal campaign state and its bounded artifact directory."""

    state: CampaignState
    paths: CampaignPaths
    returncode: int | None = None
    forced_kill: bool = False


class WaitableProcess(Protocol):
    """Subset of subprocess.Popen used by the watchdog."""

    pid: int

    def wait(self, timeout: float | None = None) -> int:
        """Wait for completion or raise subprocess.TimeoutExpired."""
        ...


type GroupSignaler = Callable[[int, signal.Signals], None]


class InvalidCampaignArtifactError(ValueError):
    """A Schemathesis artifact is malformed or exceeds its audited limit."""


class ContractDriftError(RuntimeError):
    """The live OpenAPI document differs from the canonical committed contract."""


def config_text_for(paths: CampaignPaths, persona: Persona) -> str:
    """Return a secret-free Schemathesis configuration with a fresh native cache."""
    lines = [
        "[cache]",
        "enabled = true",
        f"directory = {json.dumps(str(paths.cache.resolve()))}",
    ]
    if persona.authorization is not None:
        lines.extend(("", "[headers]", f'Authorization = "${{{AUTHORIZATION_ENV}}}"'))
    return "\n".join((*lines, ""))


def command_for(
    *,
    python_executable: Path,
    base_url: str,
    paths: CampaignPaths,
    profile: CampaignProfile,
    seed: int,
) -> list[str]:
    """Build the complete allowlisted Schemathesis 4.24.2 command."""
    origin = validate_target_url(base_url)
    if not 0 <= seed < 2**64:
        msg = "Campaign seed must be an unsigned 64-bit integer."
        raise ValueError(msg)
    return [
        str(python_executable),
        "-m",
        "schemathesis.cli",
        "--config-file",
        str(paths.config),
        "--no-color",
        "run",
        f"{origin}/openapi.json",
        "--url",
        origin,
        "--workers",
        "1",
        "--phases",
        ",".join(profile.phases),
        "--checks",
        ",".join(CHECKS),
        "--continue-on-failure",
        "--max-failures",
        "1",
        "--max-response-time",
        str(profile.max_response_time_seconds),
        "--max-redirects",
        "0",
        "--request-timeout",
        str(profile.request_timeout_seconds),
        "--request-retries",
        "0",
        "--rate-limit",
        profile.rate_limit,
        "--mode",
        "all",
        "--max-examples",
        str(profile.max_examples),
        "--seed",
        str(seed),
        "--generation-database",
        ":memory:",
        "--generation-deterministic",
        "--report",
        "ndjson",
        "--report-ndjson-path",
        str(paths.events),
        "--output-sanitize",
        "true",
        "--output-truncate",
        "true",
    ]


def subprocess_environment(persona: Persona) -> dict[str, str]:
    """Return the complete allowlisted child environment without host credentials."""
    environment = {
        "NO_PROXY": "127.0.0.1,::1",
        "PYTHONUTF8": "1",
        "PYTHONUNBUFFERED": "1",
        "no_proxy": "127.0.0.1,::1",
    }
    if persona.authorization is not None:
        environment[AUTHORIZATION_ENV] = persona.authorization
    return environment


def supervise(
    process: WaitableProcess,
    *,
    budget_seconds: float,
    cleanup_grace_seconds: float,
    signal_group: GroupSignaler = os.killpg,
) -> SupervisionResult:
    """Enforce a wall-clock budget over the complete Schemathesis process group."""
    try:
        return SupervisionResult(returncode=process.wait(timeout=budget_seconds), timed_out=False, forced_kill=False)
    except subprocess.TimeoutExpired:
        signal_group(process.pid, signal.SIGINT)
    try:
        return SupervisionResult(
            returncode=process.wait(timeout=cleanup_grace_seconds), timed_out=True, forced_kill=False
        )
    except subprocess.TimeoutExpired:
        signal_group(process.pid, signal.SIGKILL)
        return SupervisionResult(returncode=process.wait(), timed_out=True, forced_kill=True)


def read_event_summary(path: Path) -> EventSummary:
    """Parse capped sanitized NDJSON without trusting the ambiguous child exit code."""
    if not path.is_file() or path.stat().st_size > MAX_NDJSON_BYTES:
        msg = "Schemathesis NDJSON is missing or exceeds its size limit."
        raise InvalidCampaignArtifactError(msg)
    state = EventSummary()
    with path.open("rb") as stream:
        for raw_line in stream:
            if len(raw_line) > MAX_NDJSON_LINE_BYTES:
                msg = "Schemathesis NDJSON contains an oversized event."
                raise InvalidCampaignArtifactError(msg)
            try:
                event = json.loads(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                msg = "Schemathesis NDJSON contains a malformed event."
                raise InvalidCampaignArtifactError(msg) from None
            if not isinstance(event, dict) or len(event) != 1:
                msg = "Schemathesis NDJSON events must be single-key objects."
                raise InvalidCampaignArtifactError(msg)
            name, payload = next(iter(event.items()))
            if not isinstance(payload, dict):
                msg = "Schemathesis NDJSON event payloads must be objects."
                raise InvalidCampaignArtifactError(msg)
            state = _record_event(state, name, payload)
    return state


def classify_campaign(supervision: SupervisionResult, events: EventSummary | None) -> CampaignState:
    """Map process and NDJSON evidence to exactly one terminal state."""
    if supervision.timed_out:
        return CampaignState.BUDGET_EXHAUSTED
    if events is None:
        return CampaignState.HARNESS_ERROR
    if events.infrastructure_error:
        return CampaignState.INFRASTRUCTURE_ERROR
    if events.harness_error:
        return CampaignState.HARNESS_ERROR
    if not events.initialized or not events.loaded or not events.terminal or not events.completed:
        return CampaignState.HARNESS_ERROR
    if events.finding:
        return CampaignState.PRODUCT_FINDING
    if supervision.returncode == 0:
        return CampaignState.PASS
    return CampaignState.HARNESS_ERROR


async def verify_live_contract(
    running: RunningApi,
    canonical_path: Path,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """Require the attested live schema to equal the committed canonical document."""
    origin = validate_target_url(running.base_url)
    timeout = httpx.Timeout(2)
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=False, trust_env=False, transport=transport
    ) as client:
        response = await client.get(f"{origin}/openapi.json")
    response.raise_for_status()
    if len(response.content) > MAX_SCHEMA_BYTES:
        msg = "Live OpenAPI document exceeds the fuzz preflight limit."
        raise ContractDriftError(msg)
    try:
        live = response.json()
        canonical = json.loads(canonical_path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        msg = "OpenAPI preflight could not parse the live and canonical documents."
        raise ContractDriftError(msg) from error
    if live != canonical:
        msg = "Live OpenAPI document differs from contracts/openapi.json."
        raise ContractDriftError(msg)


async def run_campaign(
    running: RunningApi,
    *,
    artifact_root: Path,
    canonical_path: Path,
    profile: CampaignProfile = LOCAL_SMOKE,
    persona: Persona = ANONYMOUS,
    seed: int = 0,
) -> CampaignOutcome:
    """Reset, preflight, and run one bounded independent Schemathesis campaign."""
    paths = CampaignPaths.create(artifact_root, run_id=running.identity.run_id, persona=persona, profile=profile)
    try:
        await running.attest()
        await running.reset()
        await verify_live_contract(running, canonical_path)
    except UnsafeEnvironmentError:
        return CampaignOutcome(CampaignState.INFRASTRUCTURE_ERROR, paths)
    except httpx.HTTPError:
        return CampaignOutcome(CampaignState.INFRASTRUCTURE_ERROR, paths)
    except ContractDriftError:
        return CampaignOutcome(CampaignState.HARNESS_ERROR, paths)
    except Exception:
        return CampaignOutcome(CampaignState.HARNESS_ERROR, paths)

    paths.config.write_text(config_text_for(paths, persona), encoding="utf-8")
    command = command_for(
        python_executable=Path(sys.executable),
        base_url=running.base_url,
        paths=paths,
        profile=profile,
        seed=seed,
    )
    try:
        supervision = await asyncio.to_thread(
            _launch_and_supervise,
            command,
            subprocess_environment(persona),
            paths,
            profile,
        )
    except OSError:
        return CampaignOutcome(CampaignState.INFRASTRUCTURE_ERROR, paths)
    if supervision.timed_out:
        state = CampaignState.BUDGET_EXHAUSTED
    else:
        try:
            events = read_event_summary(paths.events)
        except InvalidCampaignArtifactError:
            events = None
        state = classify_campaign(supervision, events)
    return CampaignOutcome(state, paths, supervision.returncode, supervision.forced_kill)


def _launch_and_supervise(
    command: Sequence[str], environment: Mapping[str, str], paths: CampaignPaths, profile: CampaignProfile
) -> SupervisionResult:
    with paths.console.open("wb") as console:
        process = subprocess.Popen(
            command,
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        assert process.stdout is not None
        capture = threading.Thread(
            target=_capture_bounded_output,
            args=(process.stdout, console),
            name="SchemathesisOutputCapture",
        )
        capture.start()
        result = supervise(
            process,
            budget_seconds=profile.exploration_seconds,
            cleanup_grace_seconds=profile.cleanup_grace_seconds,
        )
        capture.join(profile.cleanup_grace_seconds)
        if capture.is_alive():
            process.stdout.close()
            capture.join()
        return result


def _record_event(state: EventSummary, name: str, payload: dict[str, object]) -> EventSummary:
    initialized = state.initialized or (
        name == "Initialize" and payload.get("schemathesis_version") == SCHEMATHESIS_VERSION
    )
    loaded = state.loaded or name == "LoadingFinished"
    terminal = state.terminal or name == "EngineFinished"
    completed = state.completed or (name == "EngineFinished" and payload.get("stop_reason") == "completed")
    status = payload.get("status")
    finding = state.finding or (name in {"ScenarioFinished", "FuzzScenarioFinished"} and status == "failure")
    failures = payload.get("failures")
    if name == "EngineFinished" and isinstance(failures, list) and failures:
        finding = True
    error_type = _error_type(payload)
    error_event = name in {"FatalError", "NonFatalError"} or (
        name in {"ScenarioFinished", "FuzzScenarioFinished", "PhaseFinished"} and status == "error"
    )
    infrastructure_error = state.infrastructure_error or (error_event and error_type in TRANSPORT_ERROR_TYPES)
    wrong_version = name == "Initialize" and payload.get("schemathesis_version") != SCHEMATHESIS_VERSION
    harness_error = state.harness_error or wrong_version or (error_event and error_type not in TRANSPORT_ERROR_TYPES)
    return EventSummary(
        initialized=initialized,
        loaded=loaded,
        terminal=terminal,
        completed=completed,
        finding=finding,
        harness_error=harness_error,
        infrastructure_error=infrastructure_error,
    )


def _error_type(payload: dict[str, object]) -> str | None:
    for key in ("value", "exception"):
        error = payload.get(key)
        if isinstance(error, dict):
            name = error.get("type")
            if isinstance(name, str):
                return name
    return None


def _capture_bounded_output(source: BinaryIO, destination: BinaryIO) -> None:
    try:
        remaining = MAX_CONSOLE_BYTES
        while chunk := source.read(64 * 1024):
            if remaining:
                captured = chunk[:remaining]
                destination.write(captured)
                remaining -= len(captured)
    finally:
        source.close()
