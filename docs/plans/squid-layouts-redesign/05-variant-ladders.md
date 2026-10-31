# 05 — Variant ladders: merge Fold into Choice

## Problem

Two structural-fallback mechanisms each carry half the semantics:

- `primitives.Fold(primary, fallback, priority)` (`primitives/nodes.py:224-238`) is the
  only node the solver's component-pressure loop can step (`planning/solve.py:664-680`,
  `_folds`/`_resolve_folds` at solve.py:592-633) — but it is binary.
- `primitives.Choice(variants, priority)` with `Variant.requires` is an ordered N-way
  ladder — but the planner resolves it by **target capability only**
  (`planning/planner.py:216`) before the solver runs; budget pressure never steps it.

A 3-step budget ladder today requires nesting `Fold(a, Fold(b, c))`, and because the
collapse loop compares `priority` globally across nesting levels with `min()`, the
collapse order of nested folds is genuinely hard to reason about.

## Design

Unify on `Choice`; `Fold` becomes sugar.

1. **Semantics**: a `Choice`'s variants are first filtered by capability
   (`Variant.requires`, unchanged, still resolved at adaptation/planning). The surviving
   ordered variants form a budget ladder: the solver starts every Choice at variant 0 and,
   under component pressure, steps the lowest-priority Choice to its next variant, one
   rung per iteration, re-solving each time — the exact shape of today's fold loop.
2. **Solver**: generalize `collapsed: set[_FoldPath]` to
   `positions: dict[_ChoicePath, int]` (path → selected variant index). `_folds` becomes
   `_choices` (walk selected branches, report choices not yet at their last variant);
   `_resolve_folds` becomes `_resolve_choices` (substitute the selected variant).
   `Fold(primary, fallback, priority)` is normalized to
   `Choice((Variant(primary), Variant(fallback)), priority)` before solving — either in
   `__post_init__`-adjacent normalization or at the top of `solve()`.
3. **API**: keep the `Fold` dataclass as a thin constructor for the two-variant case
   (most call sites read better with it); document that it is sugar. No deprecation
   churn.
4. **Priority across nesting**: document the rule that priorities compare globally and
   a nested Choice only becomes steppable once its ancestor's selected variant exposes
   it — this is today's behavior, now stated instead of discovered.
5. **No scene/codec impact**: choices resolve before scene construction, as today.
6. Coordinate with plan 04's shared text-stepping helper: `Ladder` (text axis) and
   Choice stepping (component axis) are different loops; they must not be merged, only
   named consistently.

## Verification

- `test_solve.py`: 3+-variant ladders step one rung at a time; capability filtering
  composes with budget stepping; nested-choice ordering matches the documented rule;
  existing Fold tests pass unchanged through the sugar path.
- `test_compositor.py` / `test_structure.py` regression pass:
  `cd packages/squid-layouts && uv run pytest tests/test_solve.py tests/test_compositor.py tests/test_structure.py --no-cov`.
- `just typecheck`.
