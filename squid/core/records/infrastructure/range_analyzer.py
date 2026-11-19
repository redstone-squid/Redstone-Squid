"""Request-scoped subprocess execution for public range queries."""

import os
import signal
import sys
import tempfile
from collections.abc import Sequence
from subprocess import DEVNULL

import anyio
from pydantic import ValidationError

from squid.core.errors import ServiceUnavailableError
from squid.core.records.application.expansion_regions import DoorRangeAnalysis, DoorRangeQuery
from squid.core.records.application.models import CandidateBuild
from squid.core.records.application.range_snapshots import RangeSource
from squid.core.records.domain.regions import DEFAULT_REGION_LIMITS, RegionAnalysisStatus
from squid.core.records.infrastructure.range_limits import DEFAULT_PROCESS_LIMITS, RangeProcessLimits
from squid.core.records.infrastructure.range_wire import RANGE_RESULT, RangeWorkerRequest, failed_analysis


class SubprocessRangeAnalyzer:
    """Analyze in a fresh child, terminating it before cancellation or timeout returns.

    The service owns request admission. This adapter owns the child lifetime;
    no background task or idle process survives a request. Output uses a temporary
    file so a faulty worker cannot grow an unbounded parent-side pipe buffer.
    """

    def __init__(self, limits: RangeProcessLimits = DEFAULT_PROCESS_LIMITS) -> None:
        self._limits = limits

    def _command(self) -> tuple[str, ...]:
        limits = self._limits
        return (
            sys.executable,
            "-m",
            "squid.core.records.infrastructure.range_worker",
            "--memory",
            str(limits.memory_bytes),
            "--cpu",
            str(limits.cpu_seconds),
            "--input",
            str(limits.input_bytes),
            "--output",
            str(limits.output_bytes),
        )

    async def analyze(self, sources: Sequence[CandidateBuild], query: DoorRangeQuery) -> DoorRangeAnalysis:
        if len(sources) > DEFAULT_REGION_LIMITS.candidates:
            return failed_analysis(RegionAnalysisStatus.LIMIT_EXCEEDED, "Too many source builds for range analysis.")
        payload = (
            RangeWorkerRequest(query=query, sources=tuple(RangeSource.from_candidate(s) for s in sources))
            .model_dump_json()
            .encode()
        )
        if len(payload) > self._limits.input_bytes:
            return failed_analysis(RegionAnalysisStatus.LIMIT_EXCEEDED, "Range input exceeds the worker byte limit.")
        # The child needs the interpreter environment, not database credentials
        # or the API process's integration secrets.
        environment = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT") if key in os.environ}
        environment["PYTHONHASHSEED"] = "0"
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            with tempfile.TemporaryFile() as output:
                with anyio.fail_after(self._limits.timeout_seconds):
                    process = await anyio.run_process(
                        self._command(),
                        input=payload,
                        stdout=output,
                        stderr=DEVNULL,
                        env=environment,
                        check=False,
                    )
                if process.returncode != 0:
                    resource_signals = {getattr(signal, name, None) for name in ("SIGXCPU", "SIGXFSZ")}
                    if process.returncode == 75 or -process.returncode in resource_signals:
                        return failed_analysis(
                            RegionAnalysisStatus.LIMIT_EXCEEDED, "Range worker exceeded its resource limit."
                        )
                    raise ServiceUnavailableError(
                        context={"reason": "range_worker_exit", "returncode": process.returncode}
                    )
                output.seek(0)
                data = output.read(self._limits.output_bytes + 1)
        except TimeoutError:
            return failed_analysis(RegionAnalysisStatus.TIMED_OUT, "Range analysis exceeded its elapsed-time limit.")
        except (OSError, anyio.BrokenResourceError, ExceptionGroup) as error:
            raise ServiceUnavailableError(context={"reason": "range_worker_communication"}) from error
        if len(data) > self._limits.output_bytes:
            return failed_analysis(RegionAnalysisStatus.LIMIT_EXCEEDED, "Range output exceeds the worker byte limit.")
        try:
            return RANGE_RESULT.validate_json(data)
        except ValidationError as error:
            raise ServiceUnavailableError(context={"reason": "range_worker_response"}) from error
