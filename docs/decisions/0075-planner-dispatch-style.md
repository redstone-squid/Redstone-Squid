# ADR 0075: Dispatch on planner and renderer node types with closed unions and `match`

Status: accepted (2026-08-28)

## Context

The planner and renderer pipeline in `packages/squid-ui` walks four node representations: the semantic IR authors
write (`semantic.py`), the Discord-shaped widget IR (`primitives/nodes.py`), the target-resolved scene
(`scene/model.py`), and each renderer's private draw program (for example `_V2Instruction` in
`squid_ui_discord/renderer.py`). Every one of them is a PEP 695 `type` union of frozen `slots=True` dataclasses with
no behaviour, and every traversal over them is a free function or small class containing a `match` that closes with a
raised `LayoutInvariantError` or `DrawInvariantError`.

The recurring question is whether these traversals should instead go through a visitor: `accept` methods on the node
classes, or a type-keyed dispatch table, so a new node type could be added in one place. The prompts are real —
some `match` statements are long, and the two Discord dialects looked duplicated.

Three observations settled it.

The node classes are deliberately behaviour-free. A visitor needs either an `accept` on all of them — 35 semantic
classes and 18 scene classes — or a dispatch table, and the repository has neither. Its one type-keyed dict
(`scene/model.py`, `_KIND_OWNERS`) is a uniqueness check whose comment states that dispatch is still `match`. There
is no `functools.singledispatch` anywhere in the tree.

When a second traversal over the same tree is genuinely needed, the established answer is to lower to a new union
rather than add a method: `Node` to `Realized` to `scene.Node` to `_V2Instruction`. The `_V2Instruction` docstring
argues that case in place. This keeps each pass reading only the shape it was handed.

The perceived `v2.py`/`classic.py` duplication was two different things. The shared four-method `DiscordDialect`
protocol (`normalize`, `validate`, `paginate`, `body`) is a contract implemented twice, not copy-paste: `_V2Converter`
produces `scene.Node` while `_ClassicConverter` produces embeds through helpers V2 has no counterpart for. But the
two validators had each grown a real, byte-identical copy of the shared component rules, because both dialects check
the same controls against the same `ComponentLimits`. That was ordinary duplication with an ordinary fix.

## Decision

Keep the closed-union-plus-`match` idiom for node dispatch in the planner and renderer. Do not introduce a visitor,
an `accept` protocol, or a type-to-handler registry for these node types.

The unions are closed on purpose. Adding a semantic node *should* cost an arm in each backend that must render it,
because that is the type checker naming the targets not yet answered for. A dispatch table converts that into a
runtime lookup that silently returns nothing.

Two seams already exist for the cases where per-node extension genuinely belongs, and they stay the answer:

- **The open node set** goes through `target.extensions.get(kind)` (`planning/v2.py`), a string-keyed adapter lookup
  with a declared `fallback` when nothing is registered. This is the supported way to add a node type the core does
  not know.
- **Per-case behaviour** goes through `GeneratedHandler` (`planning/generated.py`) and the family of small frozen
  dataclasses in `planning/semantic_adaptation/handlers.py`, each carrying its own `__call__`. Note that these do not
  replace a `match`; they are what its arms construct.

Two cleanups address the pains that prompted the question, without changing the idiom:

- Genuinely shared rules are extracted into a shared function that both dialects delegate to, typed against the
  narrowest limits class that declares what it reads. `planning/control_validation.py` is the worked example, and
  `MessageLimits` already documents this contract: "a shared planning layer may read only what this class declares".
- A `match` that has grown past readability is split into stage methods partitioned by what an arm needs, each
  returning `None` for a node it does not claim. `_Compiler.compile` in `planning/html_planner.py` is the worked
  example. Splitting is by arm behaviour, not by adding a dispatch layer.

## Consequences

Adding a semantic node that a backend must draw remains a multi-file change, and that is the intended cost. The
compiler cannot prove exhaustiveness where a traversal ends in `case _: raise`, so the runtime rejection stays the
safety net; a traversal that can be written without a catch-all should be.

Splitting a long `match` into claim-or-`None` stages trades a single exhaustive-looking statement for a chain. The
rejection behaviour is identical, but a reviewer must check that the stages are tried in an order where no node is
claimed by the wrong one. This is safe for the current semantic and scene unions because their members are all
siblings — none inherits another — and that property should be re-checked before splitting a union where it does not
hold.

`tests/architecture/test_naming.py` would reject a class named `Visitor` until `"Visitor": "visit"` is added to
`AGENT_NOUNS`. That check is not the reason for this decision, but it does mean introducing one is a deliberate,
reviewable act rather than a drive-by.

## Addendum (2026-08-28): the worked example evolved

The claim-or-`None` staging this ADR named as the worked example (`_Compiler.compile`) has been
replaced by a stronger form of the same idiom, not a revisiting of the decision. `compile` now
rejects the open `Renderable` escape before matching, then dispatches through one grouping `match`
over the closed `PortableNode` union whose stages are typed against private sub-union aliases, and
every planner traversal `match` (`html_planner.py`, `semantic_adaptation/lowering.py`,
`semantic_adaptation/decisions.py`) ends in `case _ as unreachable: assert_never(unreachable)`.

This delivers the exhaustiveness this ADR's Consequences section said catch-alls forfeit: a
forgotten member is a `Never` type error naming it, under both Pyrefly and BasedPyright, with the
runtime raise preserved for genuine escapes. It also removes the one reviewer burden the
Consequences flagged — the checker, not the reviewer, now verifies that no member is claimed by
the wrong stage, because each stage's parameter is its sub-union. Dispatch remains `match`; no
visitor, `accept`, or registry was introduced.
`tests/architecture/test_boundaries.py::test_planner_traversals_keep_their_exhaustiveness_proof`
keeps the terminal arms honest.
