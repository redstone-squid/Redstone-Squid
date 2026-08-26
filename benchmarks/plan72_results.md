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
| One visible leaf | 1.266 ms | 1.438 ms |
| Ten visible leaves | 2.375 ms | 2.682 ms |
| Conditional layout branch | 1.008 ms | 1.724 ms |
| Text crosses planner headroom | 1.122 ms | 1.944 ms |
| Atomic resource resolves | 1.411 ms | 1.810 ms |
| Keyed subtree mounts/unmounts | 7.488 ms | 9.202 ms |

The original 40.253/152.936 ms atomic result did not reproduce in isolated reruns; before optimization,
repeated p95 results were roughly 14-18 ms. Phase instrumentation then identified two component-tree
passes and sibling expansion as the actual avoidable work. Certified cached atomic dependencies now
settle before rendering, and typed subtree routes splice the final dirty leaf through cached structural
ancestors. Structural or metadata changes still fall back to full expansion.

The instrumented atomic run measured 1.950 ms p50 and 3.029 ms p95 for the operation. It used exactly
one render and one load per refresh; scheduler queueing and debounce were excluded. Per-phase p95 was
0.567 ms for runtime expansion, 0.135 ms for resource settlement, 1.515 ms for preflight (including
1.310 ms planning), and 0.128 ms for Discord rendering. This makes the async machinery a small part of
the local cost; real resource I/O remains outside the benchmark.

Cold versus fully unchanged p95 for 1, 100 and 1,000 components was 1.041/0.017 ms,
3.481/0.017 ms and 84.023/0.019 ms respectively. The unchanged path remains effectively independent
of tree size in this fixture.

## Fresh LayoutView construction

Run with `uv run python benchmarks/plan72_discordpy_comparison.py`. Renderer/construction lanes use
1,000 samples; the full Squid pipeline uses 200. Class declaration, scene construction, initial
planning/program compilation, state mutation and network delivery are excluded. Every sample creates
fresh Discord objects. Ratios use imperative discord.py p95 as 1.00x.

| Logical controls | Total V2 nodes | Decorators | Imperative | Squid warm renderer | Squid warm pipeline |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 2 | 0.011 ms / 0.93x | 0.012 ms / 1.00x | 0.029 ms / 2.39x | 0.477 ms / 39.42x |
| 5 | 6 | 0.029 ms / 1.01x | 0.028 ms / 1.00x | 0.061 ms / 2.16x | 0.772 ms / 27.17x |
| 20 | 24 | 0.101 ms / 1.27x | 0.079 ms / 1.00x | 0.192 ms / 2.42x | 2.613 ms / 32.99x |
| 30 | 36 | 0.149 ms / 1.09x | 0.137 ms / 1.00x | 0.261 ms / 1.91x | 3.015 ms / 22.06x |

The full-pipeline column intentionally includes partial component expansion and planning, so its ratio
is not an object-construction comparison. It answers how much local CPU a visible Squid update spends
before delivery; the renderer column is the direct comparison with rebuilding a LayoutView.

For the separate 40-node nested fixture (five Containers, TextDisplays, ActionRows and 25 Buttons),
imperative discord.py measured 0.145 ms p95 and Squid's warm renderer measured 0.261 ms p95 (1.79x).
The result matches the expected lower bound: Squid executes a cached constructor program around the
same required discord.py allocations, so it does not beat direct imperative construction in isolation.
