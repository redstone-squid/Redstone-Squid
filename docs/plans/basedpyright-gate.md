# Restore the BasedPyright gate

Status: cleared. 723 errors to 13, all of them in files that cannot run. Recorded and
completed 2026-09-09.

## Why this exists

`reportImplicitOverride` is configured as an error, yet 704 methods across 192 files were
missing `@override` when this was measured. That is not a lapse in review: **Pyrefly, the
local type checker, does not implement the check at all**, and `just typecheck` is what
anyone runs before pushing. Only CI's BasedPyright would have objected, and CI has not
completed a run recently — the last ten `Continuous Integration` attempts are all
`startup_failure` or `action_required`.

So the repository has a second type checker whose rules nothing enforces. Whatever it
reports accumulates silently. The `@override` sweep is done; this plan covers the rest.

## Outcome

| | errors |
|---|---|
| Before | 723 |
| After | 13 |

All 13 survivors are in the five dead files listed below; every error in code that runs is
gone. Pyrefly holds at zero, 5334 tests pass with the same 11 pre-existing failures a
clean tree gives, and Ruff and format are clean.

`--pythonpath .venv/bin/python` changed nothing before or after, so none of this was venv
resolution noise.

Roughly half the total came from six root causes rather than from six hundred judgement
calls: an `__all__` omission (95), four protocols declaring `__dict__` (66), one union
read in a test (133), a bound where the other 47 declarations use a default (10), a local
declaration typing every match capture in its function (12), and scope alignment (33).

## Still open

- **`reportPrivateUsage` (713) and `reportMissingParameterType` (480)** are warnings, so
  they do not fail the gate. Whether `reportPrivateUsage` at that volume is telling us
  something structural or is mis-tuned for this codebase is undecided.
- **Three invariance consequences reach real call sites**, not just tests, and each needs
  its owner: `Window`/`WindowSource` makes `SourceRankedList(source=...)` effectively
  uncallable unless the source's item type is spelled exactly `RankedEntry | EntryT`
  (`squid_ui/sources.py`); `_OperationDescriptor[OwnerT]` forbids overriding an
  `@sl.operation` in a subclass (`squid_reactivity/operations.py`); and
  `squid_ui_discord.durability` types as `object` for every consumer because it is in
  `__all__` with no static binding, which a `TYPE_CHECKING` import in the package
  `__init__` would fix globally.

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

## Fixed while measuring: a bare RoutedButton could not be planned

`RoutedButton` is public and documented, and the planner rejected it. Measurement passed
through `File | Sep | Thumbnail | PremiumButton | Button | LinkButton` and let a routed
button fall to the catch-all, which raises "must be normalized before measuring" — while
`control_validation.py:73` accepted a bare one and both dialects knew how to convert one.
Validation passed, and measurement then blamed the caller for skipping a step it had not
skipped.

The surroundings made this an omission rather than a design decision: `_clamp_button`
already accepted `RoutedButton`, one inside a `Row` was already clamped and passed
through, `MeasuredSection.accessory` already included one, and its sibling `RoutedSelect`
was already in `Realized`.

Fixed by adding the top-level pass-through arm and the `Realized` entry, which also made
the `case RoutedButton()` arms in both planners reachable, so their dead-code suppressions
are gone. Verified end to end through `plan()` against both the ComponentsV2 and classic
Discord targets. `packages/squid-ui/tests/test_top_level_controls_measure.py` covers every
control the dialects convert, and was confirmed to fail on the previous behaviour.

## Dead files found while measuring — still open

Five files import modules that do not exist, so they raise `ImportError` before running a
line. This is rot, not a typing complaint, and it is left for a decision rather than
quietly excluded or renamed. These are the only remaining BasedPyright errors.

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

Separately, `DraftLifecycleStateMachine` in `tests/fuzz/api/draft_lifecycle.py` is
referenced nowhere outside its own definition, so its `configure` is never called and
`initialize_scenario` would raise on every run.

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
