# Restore the BasedPyright gate

Status: cleared. 723 errors to 0. Recorded 2026-09-09, completed 2026-09-10.

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
| After | 0 |

Pyrefly holds at zero, 5334 tests pass with the same 11 pre-existing failures a
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

## The variance divergence, and `__replace__`

The largest single source of disagreement. For a PEP 695 parameter on a frozen dataclass,
BasedPyright infers **invariance**; Pyrefly infers **covariance**.

The mechanism is `__replace__`, not field mutability. Since 3.13 a dataclass generates
`__replace__(self, *, value: T) -> Self`, which puts `T` in a **contravariant** position,
so auto-variance correctly concludes invariant. BasedPyright is right about the class as
declared; Pyrefly is the one ignoring a generated member. An earlier revision of this
document had that backwards.

Four classes hit it, accounting for roughly twenty errors across eleven files:
`AdapterProfile` (`squid_ui/planning/adapter.py`), `PlanResult` (`squid_ui/scene/model.py`),
`Document` (`squid_ui/document.py`) and `Presented` (`squid_ui_discord/response.py`).

### The fix to apply later

Upstream's answer is `__replace__ = None` in the class body, which disables the member and
should restore covariance inference. Tracked at
[basedpyright#1589](https://github.com/DetachHead/basedpyright/issues/1589) and in the
[typing discussion](https://discuss.python.org/t/make-replace-stop-interfering-with-variance-inference/96092/17).

Two things were measured here rather than assumed:

- **It does not work yet.** Verified on BasedPyright 1.39.9 (CI's pin) and 1.40.0: adding
  `__replace__ = None` changes nothing, both still report invariance. Applying it now
  would add four inert lines and keep every suppression.
- **It is runtime-safe for this repository, when the time comes.** `__replace__` is
  consulted by `copy.replace`, not by `dataclasses.replace`. This codebase calls
  `dataclasses.replace` at 98 sites in squid-ui alone and `copy.replace` **zero** times,
  so disabling it costs nothing. Confirmed directly: with `__replace__ = None` applied,
  `dataclasses.replace(PlanResult(...))` still works while `copy.replace` raises
  `TypeError`, and the full suite ran with no new failures.

So this is a one-line-per-class change to make once BasedPyright ships the support, and it
removes the suppressions rather than adding any. The pre-695 `TypeVar(..., covariant=True)`
form remains the alternative if that never lands, though it would be declaring covariance
for a class that genuinely is not covariant while `__replace__` exists.

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

## The "dead" files were mostly renames

Five files raised `ImportError` before running a line. Four were not rot at all — every
unresolvable import was a rename nobody had followed:

| Was | Is |
|---|---|
| `squid_reactive` | `squid_reactivity` |
| `squid_replicated` | `squid_replication` |
| `squid_replicated.fake` | `squid_replication.reference` |
| `add_action_outcome_sink` | `add_action_result_sink` |

All four plan-68 benchmarks now type-check and run, verified by executing two of them end
to end. Excluding them from type checking, which was the first instinct, would have
preserved them in exactly the broken state that hid this.

`scripts/populate_db_with_logs_historical_messages.py` is the one genuine casualty. It
depends on capabilities `11808b7a` removed rather than renamed, so it carries a per-file
pragma and a docstring saying what it would take to revive it.

The lesson generalizes: reach for an exclusion only after checking whether the thing is
broken or merely stale. Four of five here were stale.

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
