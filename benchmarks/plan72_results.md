# Plan 72 render-cache benchmark evidence

These are development-machine observations, not public latency promises. The pytest contracts check
cache behavior and work invariants; only the pre-existing 100-component unchanged-path ceiling is a
timing gate.

Environment for both runs: Windows 11, Python 3.14.7, discord.py 2.7.1. Network delivery is excluded.

## Incremental mount pipeline

Run with `uv run python benchmarks/plan72_render_caching.py`. The representative-change cases use a
warmed 1,000-component tree and 50 samples.

| Change | p50 | p95 |
| --- | ---: | ---: |
| One visible leaf | 6.565 ms | 8.552 ms |
| Ten visible leaves | 7.754 ms | 9.545 ms |
| Conditional layout branch | 6.505 ms | 9.180 ms |
| Text crosses planner headroom | 7.607 ms | 11.032 ms |
| Atomic resource resolves | 40.253 ms | 152.936 ms |
| Keyed subtree mounts/unmounts | 12.190 ms | 15.105 ms |

The atomic-resource p95 includes deterministic async settlement but no I/O. Its wide p50/p95 spread is
why these cases are evidence rather than timing gates.

Cold versus fully unchanged p95 for 1, 100 and 1,000 components was 2.051/0.021 ms,
4.382/0.020 ms and 78.214/0.019 ms respectively. The unchanged path remains effectively independent
of tree size in this fixture.

## Fresh LayoutView construction

Run with `uv run python benchmarks/plan72_discordpy_comparison.py`. Renderer/construction lanes use
1,000 samples; the full Squid pipeline uses 200. Class declaration, scene construction, initial
planning/program compilation, state mutation and network delivery are excluded. Every sample creates
fresh Discord objects. Ratios use imperative discord.py p95 as 1.00x.

| Logical controls | Total V2 nodes | Decorators | Imperative | Squid warm renderer | Squid warm pipeline |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 2 | 0.024 ms / 3.41x | 0.007 ms / 1.00x | 0.041 ms / 5.86x | 0.750 ms / 107.20x |
| 5 | 6 | 0.038 ms / 1.93x | 0.020 ms / 1.00x | 0.074 ms / 3.77x | 1.743 ms / 89.36x |
| 20 | 24 | 0.130 ms / 1.30x | 0.100 ms / 1.00x | 0.404 ms / 4.04x | 4.703 ms / 46.98x |
| 30 | 36 | 0.153 ms / 0.55x | 0.278 ms / 1.00x | 0.453 ms / 1.63x | 4.572 ms / 16.46x |

The full-pipeline column intentionally includes partial component expansion and planning, so its ratio
is not an object-construction comparison. It answers how much local CPU a visible Squid update spends
before delivery; the renderer column is the direct comparison with rebuilding a LayoutView.

For the separate 40-node nested fixture (five Containers, TextDisplays, ActionRows and 25 Buttons),
imperative discord.py measured 0.160 ms p95 and Squid's warm renderer measured 0.502 ms p95 (3.14x).
The result matches the expected lower bound: Squid executes a cached constructor program around the
same required discord.py allocations, so it does not beat direct imperative construction in isolation.
