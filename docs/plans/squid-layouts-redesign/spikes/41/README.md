# Spike: reactivity prototypes for [41](../../41-reactivity-cells.md)

Three candidate propagation models behind the same attribute surface, plus the probes
that chose between them. Evidence, not a staging area — nothing here is meant to be
promoted into the package.

Run from this directory:

```sh
uv run --locked python compare.py    # the scenario table across all three
uv run --locked python probe.py      # glitch check, and read cost against the real package
uv run --locked python probe2.py     # the leak measurement, and derived-read cost
```

| File | What it is |
|---|---|
| `immutability.py` | Shared by all three: the `hash()`-based value check and the conservative equality. |
| `collector.py` | **A** — ContextVar read collection, eager recompute-compare-propagate. Closest to today's `_state_changed`. |
| `graph.py` | **B** — a conventional signal graph: cells hold dependent lists and push staleness. |
| `pull.py` | **C** — pull with versions and a global write epoch. No dependent lists. **Chosen.** |
| `compare.py` | The scenario harness. Bodies are written once and parameterised by module, which is itself a result: all three keep `self.count` and drop `depends=`, so author-facing code is identical. |
| `probe.py` | Whether an inconsistent intermediate value is observable, and per-read cost against the shipping `_State`. |
| `probe2.py` | Whether a source retains a dropped reader, and derived-read cost at depth. |

## What decided it

B and C tie on every scenario. C wins on one measurement:

```text
cross-component reader dropped; is the source still holding it?
  A collector  reader alive after drop: False
  B graph      reader alive after drop: True
  C pull       reader alive after drop: False
```

B needs `cell.dependents` to push staleness, and that edge points from source back at
reader. Components here are per-message. C needs no back-edge because invalidation is
already whole-component, so the only job left for the graph is deciding whether a computed
is still valid — which a version comparison answers at read time.

C's first draft paid for that by walking its source chain on every read, at roughly four
times A's cost in the same run. The global write epoch — a node settled in the current
epoch cannot be stale — closed the gap.

**Read the timings as orders of magnitude, not as a ranking.** Run to run on this machine
the three swap places, even at `min` of seven repeats; the only timing result that survives
repetition is pull-without-an-epoch being several times slower. The decision rests on the
measurements that are deterministic: the recompute counts in `compare.py` and the leak
check above.

A remains a defensible answer and 41's *Rejected alternatives* says why it lost: it
refreshes computeds nobody reads, and recomputes a diamond's shared node twice.
