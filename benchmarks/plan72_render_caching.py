"""Deterministic cold and unchanged render-cache latency evidence for Plan 72.

Run with ``uv run python benchmarks/plan72_render_caching.py``. The benchmark uses the
Discord test adapter so it measures component expansion, planning, constructor programs and
mount preflight without network latency or a live Discord process.
"""

import gc
import json
import time
from dataclasses import asdict, dataclass

from squid_ui_discord import Everyone, Mount
from squid_ui_discord.testing import commit_render
from squid_ui import Component, computed, state
from squid_ui.primitives import Text


class _Leaf(Component):
    source: int = state(0)

    def __init__(self, *, observed: bool) -> None:
        self.observed = observed
        self.renders = 0

    @computed
    def even(self) -> bool:
        return self.source % 2 == 0

    def render(self):
        self.renders += 1
        return Text(str(self.even)) if self.observed else ()


class _Root(Component):
    def __init__(self, components: int) -> None:
        self.leaves = tuple(_Leaf(observed=index == 0) for index in range(components))
        self.renders = 0

    def render(self):
        self.renders += 1
        return tuple(self.boundary(leaf, key=str(index)) for index, leaf in enumerate(self.leaves))


@dataclass(frozen=True, slots=True)
class RenderCachingResult:
    components: int
    cold_p95_ms: float
    unchanged_p95_ms: float
    unchanged_fraction: float
    cold_samples: int
    unchanged_samples: int


def _p95(samples: list[int]) -> int:
    ordered = sorted(samples)
    return ordered[int((len(ordered) - 1) * 0.95)]


def measure_case(
    components: int,
    *,
    cold_samples: int = 7,
    unchanged_samples: int = 50,
) -> RenderCachingResult:
    """Measure one component count with fresh cold owners and one warmed owner."""
    cold: list[int] = []
    for _ in range(cold_samples):
        mount = Mount(_Root(components), access=Everyone(), timeout=None)
        started = time.perf_counter_ns()
        commit_render(mount)
        cold.append(time.perf_counter_ns() - started)
        mount._teardown()

    root = _Root(components)
    mount = Mount(root, access=Everyone(), timeout=None)
    commit_render(mount)
    unchanged: list[int] = []
    gc_enabled = gc.isenabled()
    gc.disable()
    try:
        for index in range(unchanged_samples):
            root.leaves[0].source = (index + 1) * 2
            started = time.perf_counter_ns()
            tree = mount.runtime.render(reuse_committed=True)
            candidate = mount._preflight(tree)
            assert mount._same_as_live(candidate)
            mount._suppress(candidate, None)
            unchanged.append(time.perf_counter_ns() - started)
    finally:
        if gc_enabled:
            gc.enable()
        mount._teardown()

    cold_p95 = _p95(cold)
    unchanged_p95 = _p95(unchanged)
    return RenderCachingResult(
        components=components,
        cold_p95_ms=cold_p95 / 1_000_000,
        unchanged_p95_ms=unchanged_p95 / 1_000_000,
        unchanged_fraction=unchanged_p95 / cold_p95,
        cold_samples=cold_samples,
        unchanged_samples=unchanged_samples,
    )


def main() -> None:
    results = [measure_case(components) for components in (1, 100, 1_000)]
    print(json.dumps([asdict(result) for result in results], indent=2))


if __name__ == "__main__":
    main()
