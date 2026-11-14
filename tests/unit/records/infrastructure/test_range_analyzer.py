"""Exercise real child cleanup and the JSON boundary, not just mocked timeouts."""

import os
import sys
from dataclasses import replace
from pathlib import Path

import anyio
import pytest

from squid.core.errors import ServiceUnavailableError
from squid.records.application.expansion_regions import DoorRangeQuery, analyze_door_widths
from squid.records.domain.regions import RegionAnalysisStatus
from squid.records.infrastructure.range_analyzer import SubprocessRangeAnalyzer
from squid.records.infrastructure.range_limits import RangeProcessLimits
from tests.unit.records.application.test_expansion_regions import branch, family, fixed

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Workers require POSIX resource limits.")
QUERY = DoorRangeQuery(height=3)


async def test_worker_round_trip_preserves_rounded_families_and_fixed_exceptions() -> None:
    sources = (family(branch(volume="ceil(3n/2)+20")), fixed(9999))
    result = await SubprocessRangeAnalyzer().analyze(sources, QUERY)
    assert result == analyze_door_widths(sources, height=3)
    assert result.analysis.status is RegionAnalysisStatus.EXACT


@pytest.mark.parametrize("limits", [RangeProcessLimits(input_bytes=1), RangeProcessLimits(output_bytes=1)])
async def test_transport_limits_return_no_awards(limits: RangeProcessLimits) -> None:
    result = await SubprocessRangeAnalyzer(limits).analyze((family(),), QUERY)
    assert result.analysis.status is RegionAnalysisStatus.LIMIT_EXCEEDED
    assert not result.analysis.regions


async def test_candidate_limit_returns_no_awards() -> None:
    result = await SubprocessRangeAnalyzer().analyze(tuple(family(build_id=i) for i in range(33)), QUERY)
    assert result.analysis.status is RegionAnalysisStatus.LIMIT_EXCEEDED
    assert not result.analysis.regions


@pytest.mark.parametrize("code", ["import sys; sys.exit(1)", "print('invalid JSON')"])
async def test_broken_worker_is_unavailable(monkeypatch: pytest.MonkeyPatch, code: str) -> None:
    monkeypatch.setattr(SubprocessRangeAnalyzer, "_command", lambda _: (sys.executable, "-c", code))
    with pytest.raises(ServiceUnavailableError):
        await SubprocessRangeAnalyzer().analyze((), QUERY)


def sleeping_child(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    code = f"import os,time; open({str(path)!r}, 'w').write(str(os.getpid())); time.sleep(60)"
    monkeypatch.setattr(SubprocessRangeAnalyzer, "_command", lambda _: (sys.executable, "-c", code))


def assert_reaped(path: Path) -> None:
    pid = int(path.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_timeout_terminates_and_reaps_child(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "pid"
    sleeping_child(monkeypatch, path)
    result = await SubprocessRangeAnalyzer(RangeProcessLimits(timeout_seconds=1)).analyze((), QUERY)
    assert result.analysis.status is RegionAnalysisStatus.TIMED_OUT
    assert not result.analysis.regions
    assert_reaped(path)


async def test_cancellation_terminates_and_reaps_child(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "pid"
    sleeping_child(monkeypatch, path)
    cancelled = anyio.Event()

    async def run() -> None:
        try:
            await SubprocessRangeAnalyzer().analyze((), QUERY)
        except anyio.get_cancelled_exc_class():
            cancelled.set()
            raise

    with anyio.fail_after(5):
        async with anyio.create_task_group() as group:
            group.start_soon(run)
            while not path.exists():
                await anyio.sleep(0.01)
            group.cancel_scope.cancel()
    assert cancelled.is_set()
    assert_reaped(path)


async def test_worker_enforces_cpu_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    # Keep the real limit installation but replace arithmetic with an infinite loop.
    code = (
        "from squid.records.infrastructure.range_worker import install_limits; "
        "from squid.records.infrastructure.range_limits import RangeProcessLimits; "
        "install_limits(RangeProcessLimits(cpu_seconds=1)); "
        "exec('while True: pass')"
    )
    monkeypatch.setattr(SubprocessRangeAnalyzer, "_command", lambda _: (sys.executable, "-c", code))
    limits = replace(RangeProcessLimits(), timeout_seconds=5)
    result = await SubprocessRangeAnalyzer(limits).analyze((), QUERY)
    assert result.analysis.status is RegionAnalysisStatus.LIMIT_EXCEEDED
    assert not result.analysis.regions
