"""Run one range analysis under OS resource limits and exit.

Imports of the arithmetic application happen after limits are installed, so
startup and JSON decoding also run under the memory and CPU budgets.
"""

import argparse
import errno
import sys

from squid.core.records.infrastructure.range_limits import DEFAULT_PROCESS_LIMITS, RangeProcessLimits

try:
    import resource
except ImportError:
    resource = None


def install_limits(limits: RangeProcessLimits) -> None:
    """Fail closed on platforms that cannot enforce the required process limits."""
    if resource is None:
        msg = "Record range workers require POSIX resource limits."
        raise RuntimeError(msg)
    resource.setrlimit(resource.RLIMIT_AS, (limits.memory_bytes, limits.memory_bytes))
    resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_seconds, limits.cpu_seconds + 1))
    resource.setrlimit(resource.RLIMIT_FSIZE, (limits.output_bytes, limits.output_bytes))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--memory", type=int, default=DEFAULT_PROCESS_LIMITS.memory_bytes)
    parser.add_argument("--cpu", type=int, default=DEFAULT_PROCESS_LIMITS.cpu_seconds)
    parser.add_argument("--input", type=int, default=DEFAULT_PROCESS_LIMITS.input_bytes)
    parser.add_argument("--output", type=int, default=DEFAULT_PROCESS_LIMITS.output_bytes)
    args = parser.parse_args()
    limits = RangeProcessLimits(
        cpu_seconds=args.cpu, memory_bytes=args.memory, input_bytes=args.input, output_bytes=args.output
    )
    install_limits(limits)
    # Loading application dependencies before install_limits would leave startup
    # allocations outside the child process's memory budget.
    from squid.core.records.application.expansion_regions import analyze_door_widths
    from squid.core.records.domain.regions import RegionAnalysisStatus
    from squid.core.records.infrastructure.range_wire import RANGE_RESULT, RangeWorkerRequest, failed_analysis

    try:
        payload = sys.stdin.buffer.read(limits.input_bytes + 1)
        if len(payload) > limits.input_bytes:
            result = failed_analysis(RegionAnalysisStatus.LIMIT_EXCEEDED, "Range input exceeds the worker byte limit.")
        else:
            request = RangeWorkerRequest.model_validate_json(payload)
            result = analyze_door_widths(
                tuple(source.to_candidate() for source in request.sources),
                height=request.query.height,
                depth=request.query.depth,
                minimum=request.query.minimum,
                maximum=request.query.maximum,
            )
        output = RANGE_RESULT.dump_json(result)
        if len(output) > limits.output_bytes:
            output = RANGE_RESULT.dump_json(
                failed_analysis(
                    RegionAnalysisStatus.LIMIT_EXCEEDED,
                    "Range output exceeds the worker byte limit.",
                )
            )
        if len(output) > limits.output_bytes:
            raise SystemExit(75)
        sys.stdout.buffer.write(output)
        sys.stdout.buffer.flush()
    except OSError as error:
        if error.errno == errno.EFBIG:
            raise SystemExit(75) from None
        raise
    except MemoryError:
        # A distinct exit code lets the parent report resource exhaustion even
        # when there is too little memory left to serialize a diagnostic.
        raise SystemExit(75) from None


if __name__ == "__main__":
    main()
