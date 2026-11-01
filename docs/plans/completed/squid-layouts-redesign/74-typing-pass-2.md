# 74 — The type checker that was not checking

## What this round found first

The pass was planned against a baseline of 122 errors in `squid-ui` and 123 in
`squid-ui-discord`, taken with scoped invocations (`pyrefly check packages/<p>/src`) because the
machine plan 73 ran on could not hold the whole tree. Those numbers were right. What was wrong
was the assumption that the scoped runs were a workaround for a memory limit, rather than the
only thing looking at those files at all.

`just typecheck` — the documented local command, and the `prek` pre-push hook — was skipping
every file under `packages/*/src`. All 175 of them.

uv installs the six workspace members as editable `.pth` files pointing at their `src`
directories. That puts those directories on Pyrefly's *site-package* path, where everything is
third-party by definition and excluded from checking, and `project-includes` does not override
the classification. The arithmetic was visible the whole time: the include globs match 1193
files and Pyrefly reported covering 1018.

Demonstrated rather than deduced — a `-> int` function returning a string literal, appended to
`squid-ui`'s `planning/target.py`:

| run | canary reported |
|---|---|
| `pyrefly check --config pyproject.toml` (`just typecheck`, the pre-push hook) | no |
| `pyrefly check packages/squid-ui/src --config pyproject.toml` | yes |

`search-path` is consulted before the site-package path, so listing the workspace `src`
directories there claims them back as first-party. The tree went from 344 reported errors to
626. The 282 difference had never been looked at, which is why this round's content is mostly
bugs rather than annotations.

CI was partly covered by accident: BasedPyright has no `include` filter so it does check these
files, and `prek run --all-files` in CI runs pre-commit hooks while Pyrefly is registered
pre-push, so CI never ran Pyrefly either.

Because another session works this branch, the fix landed with `pyrefly-baseline.json` recording
the errors that already existed, and both the `just` recipe and the hook passing `--baseline`.
The hook stays green on known errors and fails on new ones — verified by re-injecting the canary
with the baseline active. The baseline is meant only to shrink.

## Before and after

| | start | end |
|---|---:|---:|
| whole tree, as `just typecheck` reported it | 344 | — |
| whole tree, actually | 626 | 497 |
| `packages/*/src` — the files that were invisible | 282 | 116 |
| &nbsp;&nbsp;squid-ui | 122 | 100 |
| &nbsp;&nbsp;squid-ui-discord | 115 | 16 |
| &nbsp;&nbsp;squid-replication | 35 | 0 |
| &nbsp;&nbsp;squid-ui-widgets | 8 | 0 |
| &nbsp;&nbsp;squid-reactivity | 2 | 0 |

Of the 116 remaining, 48 are the authored-versus-lowered text family, which the spike below
scoped and deliberately did not implement.

## The bugs, which is the point

None of these was found by reading. Each is a thing the checker said the moment it was allowed
to look at these files.

- **The devtools dashboard crashed on three panels.** The profiler tab read `health.recent`,
  `health.slow`, `health.failed` and `health.deadline_misses`; `ProfilerHealth` spells all four
  `retained_*`, so opening it raised `AttributeError`. The message-root and session detail panels
  called `sl.bullets(...)` without its required keyword-only `key` — `TypeError`. All three run
  only when a developer clicks into them, which is why they sat there. The new tests render every
  section and both detail panels.
- **Attaching to a durable session raised.** `DurableSession.attach` added a required `recipe`
  keyword that `Session.attach` has no concept of. Every caller reaching a session through
  `SessionManager.session_for` holds a plain `Session`, so `SessionSpec.attach` and
  `SessionSpec.respond` onto a durable parent raised `TypeError`. Reproduced against the live
  durable runtime before touching it. `recipe` now defaults, and attaching without one is refused
  with `RejectionReason.RECIPE_REQUIRED`: serving it would put a mount in the graph that recovery
  cannot rebuild, so the child would vanish at the next restart.
- **A control in a layout position.** `multi_choice` used `controls.form(...)` as a `fallback`
  alternate. That call answers `FormTrigger | RoutedActionControl`, and only the first is a layout
  node — a `RoutedActionControl` is not in the `SemanticNode` union at all. Under the routed
  driver this put a control where a node belongs. `collection.py` already narrows the same union
  the same way.
- **Two missing guards in the Loro text engine.** `LoroTextEngine.export_since` had neither check
  its `LoroEngine` counterpart has: a non-bytes version reached the Rust frontier decoder
  directly, and a missing version vector reached `ExportMode.Updates` as `None`.
- **Entity prefills reached discord.py untyped.** The modal path wrapped whatever the store
  returned into `default_values`. discord.py can only infer a missing entity kind from the
  select's own type, and rejects an untyped object outright on a mentionable select — where an id
  alone genuinely does not say whether it means a user or a role.

## Clusters retired

- **The bare-default trap.** `Component[ModeT]` and `MessageRoot[ModeT, AdapterT]` default every
  parameter, so internal machinery annotated with the bare names rejected `Self`. `AnyComponent`
  and `AnyMessageRoot`, in the spirit of the existing `AnyTarget`.
- **`Axis` finished.** Plan 71 converted the limits objects and left the cost side keyed by `str`;
  `Mapping` is invariant in its key, so the enum bought nothing at the boundary between them.
  Converting the signatures first *raised* the count, because a wrongly typed parameter had been
  masking its own call sites.
- **One splatted dict.** `_entity_kwargs` returned `dict[str, object]` into four different select
  constructors — 36 of `message_root.py`'s 77 errors from a single erased mapping.
- **`FormField.format`.** Declared `ValueT | None` while every override branched on `isinstance`
  first and passed anything else straight back. The narrow annotation was decoration over an
  `object` contract.
- **Two protocols in one no-op object.** `_NoOpOperation` inherited `_NoOpSpan`, so its
  `__enter__` advertised only the span half — and hid that the recording path returned the shared
  operation no-op where a span was declared.
- **`LoroValue` narrowing.** Loro's `get_value()` is the whole union because any container answers
  it; every caller here immediately indexes it as a map. `_map_contents` is where that assumption
  is now stated and checked.
- **Three unprovable status matches.** `browser`, `source_ranked` and `search_picker` split
  `previous` into the pattern, which is exhaustive at runtime but not to the checker. One arm per
  union member says the same thing provably.

## The spike

`spikes/74/` measured both candidates for the authored-versus-lowered text split, and
`test_lowering_resolves_text` pins the invariant they both depend on — lowering leaves no
deferred text behind, measured rather than assumed.

Candidate A, a `TextT` parameter per text-bearing leaf, is free: package-source errors were
byte-identical, 146 both ways. It is refuted by one call site the codebase contains, where a
select's option labels are data and its placeholder is prose. One parameter per class forces
every text field to the same kind and forbids that mix, which is a property of the *authored*
stage; only after lowering is a node uniformly one kind. That decides for Candidate B, and B is
not implemented here.

## Still open, deliberately

> Postscript, 2026-08-28: Candidate B's goal was reached by "planning: enforce the resolved
> primitive boundary" (`2ccdbbb5`), which drove package sources to zero errors; the last 48
> test-file errors were then fixed directly and `pyrefly-baseline.json` retired entirely.
> This closes Candidate B and the baseline cleanup. The remaining items are accounted for
> in the follow-up below.

- **Candidate B** — sixteen lowered counterpart classes and a conversion for each, since
  `dataclasses.replace` returns whatever type it was handed and cannot narrow. Retires the 48
  remaining text errors in `v2.py`, `classic.py`, `realization.py` and `dialect.py`.
- **Widget `ModeT` threading** — plan 73's gap 2 and this plan's phase 5. `_content.py`'s
  `ContentLike` is still dialect-erased. It carries no error signal, so nothing here forced it;
  `sl.paged`'s runtime rejection remains the backstop.
- **The cast triage that carries no errors** — `CachedPlan` generic in `BodyT`, `_stage_loaded`
  overloads on `preflight`, `inject()` overloads over an enum sentinel, `owner.py`'s `TypeGuard`,
  `_tree.py`'s generic `one`/`many`, and the ten `cast(str, event.values[...])` sites in `squid/`
  that want the `Form`-subclass migration. Three casts were retired where the check they stood in
  for was cheap to write: the profiler structural check, `OpenContext.of`'s duck-typed read, and
  `ActionCommit.patches` under `TYPE_CHECKING`.
- **A pycrdt stub bug** — `StackItem` is declared over the Rust `Doc` while the runtime accepts
  the Python wrapper, which `test_pycrdt_stack_item_groups_multiple_container_types` already
  demonstrates. The one suppression added this round names that test, so it can be dropped when
  the stub is fixed.

## Follow-up, 2026-10-31

The in-tree typing work is complete:

- Widget content and machine drivers carry their render target. `CachedPlan`, `inject()` and
  the tree helpers retain their value parameters; the obsolete owner `TypeGuard` is gone.
- `_stage_loaded()` now distinguishes drawn candidates from optional preflight results through
  overloads, including the disabled-render case. Its callers no longer cast the result.
- `Boundary[RenderTargetT]` carries a typed child, and `Component.boundary()` returns that same
  specialization. Nested layout factories preserve it. This also exposed and fixed
  `StackNavigator.render()` dropping its own target parameter.
- The bot's fixed form schemas use typed `Form` declarations for both presentation and submitted
  values. Dynamic build-editor fields check their text values explicitly. No casts of
  `event.values` remain. The migration caught two real empty-field defects: clearing a role weight
  called `strip()` on `None`, and optional build fields passed `None` to string parsers. Both now
  normalize blank input and have regression tests.

Two external limitations remain; zero type-checker errors does not close them:

- Plan 73's mixed-target inference hole reproduces in both the pinned Pyrefly 1.2.0 and an
  isolated 1.4.0 run. It also reproduces with a small contravariant generic and no Squid imports.
  Boundaries now preserve their target, but that does not change the checker's inference for
  incompatible siblings. The planner still rejects such trees.
- pycrdt 0.14.2 still annotates `StackItem` with its internal `Doc`. The existing suppression
  remains at the call that correctly passes the public wrapper. The upstream
  [`_pycrdt.pyi`](https://github.com/y-crdt/pycrdt/blob/main/python/pycrdt/_pycrdt.pyi)
  also still has that annotation when checked on this date.

## Verification

Per-workstream commits, each with focused tests for what it changed. The devtools and
durable-attach fixes each have a test that fails with the fix reverted and passes with it.
`packages/squid-ui/tests`, `packages/squid-ui-discord/tests`, `packages/squid-replication/tests`
and `packages/squid-reactivity/tests` all pass. The baseline was regenerated downward, 626 to
497, and `just typecheck` reports zero.
