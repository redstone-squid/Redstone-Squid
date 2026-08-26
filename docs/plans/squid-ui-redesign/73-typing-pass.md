# 73 — The type parameter that was there all along

## Why

`Renderable[ModeT]` (`target_types.py:28`) has always made a dialect mismatch a static error:
`ModeT` sits in a parameter position, so it is contravariant, and `Renderable[X]` is usable
where `Renderable[M]` is wanted exactly when `M <: X`. The primitives declare their dialect —
`Text(Renderable[DiscordTarget])`, `Panel(Renderable[ComponentsV2Target])`,
`Card(Renderable[ClassicTarget])` — and `typing_targets.py` pinned that
`plan(Panel(...), target=classic())` does not check.

It only ever worked for a node written at the root. One level down the mode died:

```python
plan(sl.stack(Panel((Text("v2"),))), target=classic())   # silently accepted
```

Three breaks, one seam:

| Break | Site |
|---|---|
| `SemanticNode` and `Adaptation` were unparameterized unions, yet members of `LayoutNode[ModeT]` | `semantic.py:769,807,809` |
| Every container held `children: tuple[LayoutNode, ...]`, i.e. `LayoutNode[Any]` | `semantic.py:140-205,676-760` |
| `Component`, `RenderResult` and `Document` all defaulted the parameter to `Any` | `component.py:99,188`, `document.py:14,22` |

`MessageRoot[ModeT](component: Component[ModeT], *, target: Target[..., ModeT, ...])` was
already wired for this and was only ever handed `Any` on both sides.

The rest of the pass is the debt that shared the same cause: signatures that described
themselves inaccurately and a suppression on the next line.

## What was measured, not argued

Propagating a mode through a container needs the checker to compute a **meet** — one
contravariant variable, two upper bounds — which the typing spec does not require anyone to
do. Two designs were on the table: one generic signature, or an ordered three-overload ladder
per factory (~54 extra overloads). `spikes/73/` measured both under pyrefly and basedpyright.
Plain inference computes the meet in both, including through nesting, through an adaptation
wrapper, and through the realistic union parameter shape. Design B was never written.

A `ModeT = DiscordTarget` default on each factory's type parameter is load-bearing: without
it an all-neutral container infers `Stack[Unknown]`.

`Component` needed *explicit* contravariance. `ModeT` reaches it only through
`RenderResult[ModeT]`, and neither checker infers variance through that nesting — both settle
on invariant, which is the one answer that makes the design useless: a portable
`sl.Component` could not be mounted on a Components V2 screen, and every consumer would have
to restate a dialect it does not care about. PEP 695 has no syntax for variance, so
`Component` uses `TypeVar` and `Generic` in a file that otherwise does not.

## What it found

The `Any` default had been hiding four library bugs, each surfaced by migrating one consumer:

- `Component.boundary(child: Component)` accepted portable children only — every child except
  the ones a V2 screen is built from.
- `MessageRootDefaults.mount`, `ClientRuntime.mount` and `StackNavigator` erased the dialect
  in passing.
- `render_static`, `contribute`, `render_item` and `render_message` each fixed a dialect
  through their `target` default while declaring a portable document.
- `_node_types` needed a `get_origin` branch: `Stack[ModeT]` is not a `type`, so every
  container silently fell out of the runtime `_NODE_TYPES` table and
  `is_layout_node(sl.stack(...))` went quietly False.

And two design conflations that the suppressions had been standing in for:

- **`TransactionContribution.token` was `Any`**, so neither `sl.history()`'s two calls on it
  nor any backend's implementation was checked. Now a `ChangeToken[InverseT]` protocol,
  generic because every real backend narrows `stage_inverse` to its own prepared type.
- **The `inconsistent-overload` suppressions were real.** The arity ladders on
  `semantic.fallback` and `Variants.of` named their parameters while the implementation is
  variadic, so each overload promised a keyword call the implementation could never accept.
  Positional-only makes them agree.

## Known gaps, recorded rather than papered over

- **A container mixing two dialects** works in neither, but contravariance makes the union the
  solver's natural answer, so `Stack[Classic | ComponentsV2]` reads as "accepts either" and
  pyrefly allows it against both targets. Basedpyright rejects it at the call, so CI covers
  what the pre-push hook misses, and the planner still raises. An overload ladder would have
  the same hole with a worse message.
- **Widget content slots are dialect-erased.** `normalize_content` classifies an `object`, so
  there is no static type on the way in to carry a mode. Tracking it means a `ModeT` on
  `StateMachine`, `MachineControls` and the ten machines implementing them.
- **`Option` is both the authored type and the lowered IR.** `Option.label` is `TextLike` when
  an author writes it and `str` by the time the planner reads it — the lowering pass resolves
  it through `_resolve`. Nothing says so, which is why `classic.py`, `v2.py` and
  `realization.py` all carry `TextLike`-versus-`str` errors. Fixing it means splitting
  authored from lowered node types, which is a design change rather than an annotation.
- **`SubmitEvent` was going to gain a `FormT`**, until it turned out `Form._submit` already
  binds submitted values to the instance, so a `Form` subclass's handler reads
  `self.<field>` typed. The remaining casts in `squid/` are in code that builds a bare
  `FormSpec` inline; migrating those call sites is an application refactor.
- **`GuardLedger.read`'s cast stays.** The store is heterogeneous and the value type is
  already inferred from `default`; a typed key would duplicate what `default` carries.

## Where it landed

| | before | after |
|---|---:|---:|
| squid-ui | 130 | 122 |
| squid-ui-discord | 126 | 113 |
| squid-ui-widgets | 8 | 8 |
| squid/ + app.py | 0 | 0 |
| `cast(...)` across the four packages | 54 | 33 |
| suppression comments | 46 | 22 |

## Verification

`typing_modes.py` is the file this plan exists for: it pins the meet, the propagation through
nesting and wrappers, and that a `Panel` three containers deep is an error against `classic()`
and clean against `v2()`. Every `pyrefly: ignore` in it asserts that its line *is* an error, so
an unused one means propagation has regressed.

`just typecheck` OOMs a 3 GB machine, so per-package `pyrefly check` runs over explicit file
lists were used throughout, with counts taken against `git stash` baselines rather than
claimed. CI carries the whole-tree check.
