# Restore the BasedPyright gate

Status: triaged, unstarted. Recorded 2026-09-09.

## Why this exists

`reportImplicitOverride` is configured as an error, yet 704 methods across 192 files were
missing `@override` when this was measured. That is not a lapse in review: **Pyrefly, the
local type checker, does not implement the check at all**, and `just typecheck` is what
anyone runs before pushing. Only CI's BasedPyright would have objected, and CI has not
completed a run recently — the last ten `Continuous Integration` attempts are all
`startup_failure` or `action_required`.

So the repository has a second type checker whose rules nothing enforces. Whatever it
reports accumulates silently. The `@override` sweep is done; this plan covers the rest.

## Current measurement

`uvx --from basedpyright==1.39.9 basedpyright --level=warning`, CI's exact invocation,
after the sweep and the `site` exclusion:

| Severity | Count |
|---|---|
| error | 723 |
| warning | 1295 |
| information | 984 |

Errors decide the exit code, so the gate is red at 723.

Passing `--pythonpath .venv/bin/python` changes nothing, so **none of this is venv
resolution noise** — the findings are real, subject to the caveat below.

### Errors by area

`packages` 557, `squid` 71, `tests` 35, `docs` 32, `benchmarks` 21, `scripts` 6, `alembic` 1.

Three quarters sit in the squid-ui workspace members.

### Errors by rule

| Rule | Count | Note |
|---|---|---|
| `reportArgumentType` | 228 | mostly `packages` (176) |
| `reportAttributeAccessIssue` | 220 | mostly `packages` (199) |
| `reportPrivateImportUsage` | 129 | mostly `packages` (100) |
| `reportUnnecessaryComparison` | 24 | |
| `reportPossiblyUnbound` | 16 | |
| `reportAssertTypeFailure` | 14 | |
| `reportIncompatibleMethodOverride` | 11 | 8 in `benchmarks` |
| `reportReturnType` | 11 | |

The dominant warnings are `reportPrivateUsage` (713) and `reportMissingParameterType` (480).

## Suggested order

1. **Find out why CI cannot start.** Everything else re-accumulates without it, and this
   is why the drift was invisible. It may not be diagnosable from a sandbox.
2. **`reportPrivateImportUsage`, 129 sites.** The cheapest real cluster: BasedPyright
   prints the intended module for each one, e.g. `ResourceCost` imported from
   `squid_ui.planning.target` when it is exported by `squid_ui.planning.resources`. These
   are genuine layering statements, not annotation noise.
3. **`reportIncompatibleMethodOverride`, 11 sites.** Small, and each is a real
   Liskov violation — mostly benchmark `Component.render` implementations whose return
   type does not match the base.
4. **Decide `packages/` policy.** 557 of the 723 errors are there. Either commit to
   fixing them or narrow BasedPyright's scope deliberately, but record which.
5. **The two warning clusters last**, and only after deciding whether `reportPrivateUsage`
   at 713 is telling us something structural or is mis-tuned for this codebase.

## Caveats

- The two checkers genuinely disagree in places. `_Projection.settings_customise_sources`
  in `squid/config.py` is the known case: its base is a type variable, so Pyrefly rejects
  `@override` while BasedPyright demands it. Expect more of these; suppress per-line for
  the checker that is wrong, and say which in the comment, as CLAUDE.md requires.
- Inserting a decorator above a method can retarget a `# pyrefly: ignore` comment that sat
  directly above the `def`. `DevTools.cog_check` was silently un-suppressed this way during
  the sweep. Pyrefly's *suppression count* catches this where the error count does not.
- A sweep driven by tool JSON must be re-measured between runs. Line numbers from a stale
  report point at unrelated methods once earlier edits land.

## Unrelated failures seen while measuring

Not caused by this work, and reproducing on a clean tree: 5 failures in `tests/unit`
(`test_app_main`, `test_routes`, two in `test_config`, `test_runtime`) and 6 in
`tests/architecture` (localization catalog, structured errors, four naming checks). They
deserve their own look, and are more evidence that nothing has been gating the tree.
