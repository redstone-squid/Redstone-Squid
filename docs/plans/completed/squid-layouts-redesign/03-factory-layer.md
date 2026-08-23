# 03 — Factory layer for composition

## Problem

Every semantic container takes `children: tuple[...]` as its first field, so real render
code is double-parens, trailing-comma tuples, and `children: list[sl.LayoutNode] = [...]`
with conditional `append` blocks (`squid/bot/settings_view.py` `_server_nodes` /
`_voting_nodes` are the canonical shape). Conditional inclusion — the most common thing a
render function does — has no direct expression, which drives the imperative list-building
style and the indentation depth.

This verbosity is also a root cause of the semantic layer losing to its own escape hatch:
`primitives.Button`/`primitives.Row` appear 65 times in `squid/` versus 3 uses of semantic
`Actions` — `Button(label, handler, key)` is terse, semantic `Actions` costs container-key
plus tuple ceremony. Plan 04 closes the capability half of that gap; this plan closes the
ergonomic half.

## Design

The frozen dataclasses stay the IR, unchanged and public. `squid_layouts/factories.py`
adds lowercase builders, exported from the package root.

    sl.section(
        t"Count: {self.count}",
        self.shows_voting and sl.action("Voting", self._show_voting, key="voting"),
        *(sl.field(name, value) for name, value in rows),
        heading="Counter",
    )

### One signature shape

**Content is positional; identity and configuration are keyword-only.** Even required
config (`key=`, `on_change=`, `current=`) is keyword, so no factory has an argument order
to memorize and the reconciliation key is always conspicuous at the call site. This also
keeps the IR's inconsistent field order (`Actions(items, key)` versus
`Choices(key, choices, ...)`) out of the authoring surface without touching the IR.

`key` is required exactly where the runtime reads it back — nodes owning session state or
custom ids (`actions`, `choices`, `items`, `details`, `navigation`, `bullets`, `media`,
`table`, and the `action`/`choice`/`destination`/`item` records). The five records whose
key no target reads today (`Field`, `Column`, `TableRow`, `MediaItem`, `ListItem`) default
it to `""` rather than making authors invent identities nothing consumes. If a later plan
starts keying them, dropping the default turns every call site into a type error, which is
the right way to find them.

### Three tiers

1. **Child containers** — `group`, `stack`, `cluster`, `section`, `article`, `aside`,
   `details`, `item`. `*children: ChildLike`.
2. **Collection nodes** — `actions`, `action_group`, `choices`, `fields`, `items`,
   `media`, `navigation`, `table`, `bullets`. Variadic over their own element type; skip
   rules apply, but text is *not* promoted — a bare string is not an `Action`. `bullets`
   and `media` are the exceptions: their elements are pure content records, so text and
   URLs promote. `table` keeps `columns=` keyword (two collections, no variadic winner).
3. **Leaves** — `paragraph`, `heading`, `status`, `code`, `quote`, `field`, `bullet`,
   `column`, `table_row`, `media_item`, `figure`, `progress`, `measure`, `action`, `link`,
   `choice`, `destination`.

### Normalization

    type Conditional[ItemT] = ItemT | None | Literal[False]
    type ChildLike = Conditional[LayoutNode | TextLike | Template]

- `None` / `False` → skipped, so `cond and node` composes directly.
- `str` / `ResolvedText` → `Paragraph`; bare strings stay trusted Markdown.
- `Template` → `md(template)` → `Paragraph`, so `sl.section(t"Count: {n}")` works and
  escapes its interpolations. Accepted in every `TextLike` keyword too (`heading=`,
  `summary=`, `label=`); the IR's `TextLike` stays `str | ResolvedText`.
- Anything else → `TypeError` naming the argument position, with a targeted hint for the
  four likely mistakes: `True`, a sequence, a mapping, a `Component`.

**`True` is rejected, not skipped.** `x and y` evaluates to `y` or to the falsy `x`, so
the supported idiom can never produce `True`; only a mistake can. Typing the parameter as
`Literal[False]` rather than `bool` is what makes the checker catch it, and the same
narrowness makes React's `count and node` "renders a literal 0" bug a type error here.
Verified against pyrefly 1.2.0: it narrows `bool` to `Literal[False]` in the falsy operand
of `and`, and narrows `int` to `Literal[0] | Node`, so both the idiom and its rejection
work as designed.

**Sequences are not flattened.** `*` already exists, says it at the call site, and costs
one character: `sl.section(*(sl.field(k, v) for k, v in rows))`. Flattening would
reimplement unpacking at runtime, need a `Mapping` guard and a str-before-`Iterable`
ordering, and — because `str` is `Iterable[str]` — would make `Iterable[Conditional[T]]`
co-recursively accept a bare string in every collection factory, turning
`sl.actions("Vote")` into a runtime-only error. Passing a list raises a `TypeError` that
names the fix. Conditionally including a *group* is `*(entries if cond else ())`.

### Deliberately out of scope

- No primitives factories (`row`/`button`). Those 65 call sites are plan 04's
  migration to semantic `action`, not a parallel sugar layer to migrate twice.
- No context-manager/dominate-style DSL. It fights the "render returns a value" purity the
  runtime depends on.
- Framework internals keep constructing dataclasses directly, so the sugar layer stays
  independently testable.
- `sl.list` would shadow the builtin, and under PEP 649 a `list[LayoutNode]` annotation
  inside `factories.py` would then resolve to the factory. The `List` builder is
  `sl.bullets`.
- Consumer migration (including `layout_showcase.py`'s user-visible `_SOURCE_EXAMPLES`)
  belongs to plan 04, which touches those files anyway.

### Notes for later plans

- Plan 04 adds `Section(accent=, footer=, media=)` and `Field(fallbacks=)`; it must extend
  the factories in the same commit, or the drift test fails.
- Plan 14's `RoutedAction`/`Router` needs the same treatment when it lands.
- Plan 11: `semantic.Field` has zero call sites and its `key` is never read by lowering;
  same for `Column`, `TableRow`, `MediaItem`, `ListItem`. Consider defaulting them in the
  IR so the factory default stops being a factory-only convention.

## Verification

Landed with:

- `packages/squid-layouts/tests/test_factories.py` — skip/promote rules, t-string
  escaping, the four rejections, factory-output-equals-dataclass parity for every factory,
  and a drift guard that walks the `SemanticNode` union and asserts each member has an
  exported factory.
- An emptiness test proving `plan(sl.section(None, cond and node))` yields no components:
  the solver already prunes empty `Text`/`Panel`/`Row`/`Gallery`
  (`planning/solve.py:190`, `planning/solve.py:508`), so factories need no emptiness rules
  of their own. Conditional composition relies on this.
- `test_public_api.py` covers the new exports.
- `just typecheck` clean; no suppressions in `factories.py`.
- Docs re-teaching the style: `packages/squid-layouts/README.md` headline example plus an
  authoring rule, and the semantic-authoring section of
  `docs/squid-layouts-architecture.md`.

Lands before plan 04 phase B — its migration snippets are written factory-style.
