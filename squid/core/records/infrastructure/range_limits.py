"""Process and transport bounds for a single record-range analysis."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RangeProcessLimits:
    timeout_seconds: float = 15
    cpu_seconds: int = 10
    memory_bytes: int = 512 * 1024 * 1024
    input_bytes: int = 2 * 1024 * 1024
    output_bytes: int = 4 * 1024 * 1024


DEFAULT_PROCESS_LIMITS = RangeProcessLimits()
