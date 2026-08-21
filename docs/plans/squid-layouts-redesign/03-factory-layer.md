# 03 — Factory layer for composition

## Problem

Every semantic container takes `children: tuple[...]` as its first field, so real render
code is double-parens, trailing-comma tuples, and `children: list[sl.LayoutNode] = [...]`
with conditional `append` blocks (`squid/bot/settings_view.py` `_server_nodes` /
`_voting_nodes` are the canonical shape). Conditional inclusion — the most common thing a
render function does — has no direct expression, which drives the imperative list-building
style and the indentation depth.

This verbosity is also a root cause of the semantic layer losing to its own escape hatch:
`primitives.Button`/`Row` appear ~73 times in `squid/bot` versus ~5 uses of semantic
`Actions` — `Button(label, handler, key)` is terse, semantic `Actions` costs container-key
plus tuple ceremony. Plan 04 closes the capability half of that gap; this plan closes the
ergonomic half.

## Design

Keep the frozen dataclasses as the IR, unchanged. Add a factory module
(`squid_layouts/factories.py`, exported from the package root) of lowercase variadic
builders that normalize their children:

    sl.section(
        sl.paragraph(md(t"Count: {self.count}")),
        self.shows_voting and sl.button("Voting", self._show_voting, "voting"),
        [sl.field(k, v) for k, v in rows],
        heading="Counter",
    )

Normalization rules, applied recursively to `*children`:

- `None`, `False`, `True` → skipped (enables `cond and node`);
- `str` / `TextLike` → promoted to `Paragraph` (bare strings stay trusted markdown, per
  the existing text-dialect rules);
- non-node, non-string iterables → flattened;
- nodes → passed through.

Scope of builders: every semantic container (`section`, `article`, `aside`, `group`,
`stack`, `cluster`, `details`, `item`/`items`) plus the high-traffic leaves where
terseness matters (`paragraph`, `heading`, `field`/`fields`, `status`, `code`, `quote`,
`action`/`actions`, `link`, `choice`/`choices`, `navigation`, `media`). Primitives keep
only `row`/`button` conveniences if migration shows demand — do not build a parallel
factory set for the whole primitives layer.

Config parameters stay keyword-only after `*children`. The uppercase dataclasses remain
public and valid; factories are sugar, not a second API — one line in the README saying
"factories are the recommended authoring surface, dataclasses are the IR".

Deliberately rejected: a context-manager/dominate-style DSL. It fights the "render
returns a value" purity the runtime depends on.

## Verification

- New `packages/squid-layouts/tests/test_factories.py`: normalization rules (skip,
  promote, flatten, nesting), and that factory output equals hand-built dataclasses.
- `test_public_api.py` for the new exports.
- `just typecheck`; factories must be fully typed (`*children: ChildLike`) without
  suppressions.
- Land before plan 04 phase B — its migration snippets are written factory-style.
