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

## Bug found while measuring: a bare RoutedButton cannot be planned

`RoutedButton` is public, documented, and rejected by the planner. Reproduced against the
real Discord V2 target, not a test double:

```python
plan(as_document([RoutedButton(label="x", route_id="r")]), target=DISCORD_V2_DPY27)
# LayoutInvariantError: RoutedButton must be normalized before measuring
```

A `LinkButton` in the same position plans fine.

The cause is a three-file gap. `layout_measurement/realization.py:308` passes through
`File() | Sep() | Thumbnail() | PremiumButton() | Button() | LinkButton()` and lets
`RoutedButton` fall to `case _`, so `Realized` (`layout_measurement/model.py:101`) never
includes it. Meanwhile `control_validation.py:73` accepts a bare `RoutedButton` as valid
and `discord_dialect.py:120` knows how to convert one — so validation passes and
measurement then fails with a message blaming a normalization step the caller never
skipped.

The dead `case` arms naming `RoutedButton` in `planning/classic.py:297` and
`planning/v2.py:182` are the symptom, and are kept with a suppression pointing here.
Adding `RoutedButton()` to realization's pass-through arm and to `Realized` is the
one-file fix; this needs whoever owns `layout_measurement/` to confirm that is the
intended behaviour rather than the validator being too permissive.

## Dead files found while measuring

Five files import modules that do not exist, so they raise `ImportError` before running a
line. This is rot, not a typing complaint, and it is left for a decision rather than
quietly excluded or renamed:

| File | Unresolvable import |
|---|---|
| `benchmarks/plan68.py` | `squid_reactive` (the package is `squid_reactivity`) |
| `benchmarks/plan68_backends.py` | `squid_replicated.backends.{loro,pycrdt}` |
| `benchmarks/plan68_backend_actions.py` | `squid_replicated.backends.{loro,pycrdt}` |
| `benchmarks/plan68_fake_replication.py` | `squid_replicated.fake` |
| `scripts/populate_db_with_logs_historical_messages.py` | `squid.bot.submission.media` |

`squid_replicated` is now `squid_replication`, and that rename fixes the two `backends`
imports, but `squid_replicated.fake` has no successor. The script additionally imports
three names that no longer exist (`create_application_runtime`, `BUILD_LOG_CHANNEL_IDS`,
`ApplicationServices`) and passes two parameters (`mirror`, `dry_run`) that no signature
accepts, so it needs more than a rename. Deleting these or repairing them is a judgement
call about whether the plan-68 benchmarks and the backfill script are still wanted.

## The variance divergence

The largest single source of disagreement between the two checkers. For a PEP 695
parameter on a frozen dataclass, BasedPyright infers **invariance**; Pyrefly infers
**covariance**. Verified with a standalone repro:

```python
@dataclass(frozen=True, slots=True)
class Box[T]:
    value: T

def takes(x: Box[object]) -> None: ...
def check(b: Box[int]) -> None:
    takes(b)          # BasedPyright: "T@Box is invariant". Pyrefly: clean.
```

Pyrefly is right: a frozen field is read-only, which is exactly when covariance is sound.
BasedPyright is being conservative about dataclass fields.

Four classes hit this and accounted for roughly twenty errors across eleven files:
`AdapterProfile` (`squid_ui/planning/adapter.py`), `PlanResult` (`squid_ui/scene/model.py`),
`Document` (`squid_ui/document.py`) and `Presented` (`squid_ui_discord/response.py`).

PEP 695 has no syntax for explicit variance, so the portable fix is the pre-695 form:
`TypeVar("T_co", covariant=True)`. `squid_ui.planning.target.Target` already does exactly
this, and its docstring argues for covariance on the same grounds. Declaring it is safe —
both checkers verify a declared variance against usage, so an unsound claim becomes an
error rather than a silent lie. Doing this at the four declarations would remove the whole
class of divergence and several of the suppressions added to work around it.

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
