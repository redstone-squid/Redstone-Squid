# Public API narrowing: a three-tier root surface

`squid_layouts` exports 372 names from its root (`src/squid_layouts/__init__.py`, 767
lines, all eager explicit re-exports). The current policy is effectively "if it's
supported, put it in `sl.__all__`", and `tests/test_public_api.py` encoded that with
*subset* assertions (`{...} <= set(sl.__all__)`) — nothing forbade additions, so the
surface only grew.

Measured against real usage: 100 of the 372 root names have zero call sites anywhere in
the repo, and in-repo consumers touch only ~70 distinct root names. `WindowLoader`,
`AmbiguousTimePolicy`, `DecisionState`, `ActionMiddleware`, `CollectionEditor`, and
`PlanReport` all sat at the same level as `sl.section`.

The package is pre-1.0 with no external consumers, and every call site is in-tree —
this is the cheapest this reorganization will ever be.

## Policy

`squid_layouts` exposes three tiers:

1. **`import squid_layouts as sl`** — the authoring language: verbs, component-model
   primitives, semantic factories, and the vocabulary you unavoidably type in a
   component's signature.
2. **`sl.<domain>`** — supported but qualified subsystems (`sl.patterns`, `sl.forms`,
   `sl.runtime`, `sl.discord`, …).
3. **Deeper modules** — implementation detail and expert escape hatches
   (`sl.primitives`, `sl.discord.routing`, …).

**Promotion rule:** a symbol starts in its domain namespace and is promoted to root
only once qualifying it is demonstrably noise. This replaces the old default-to-root
policy. The contract in `tests/test_public_api.py` enforces it structurally: exact set
equality on `sl.__all__` against a literal allowlist, plus assertions that specialist
types do **not** leak to root (`test_specialists_live_in_namespaces_and_not_at_root`).
Adding a name to root now requires a conscious edit to that allowlist and a reviewer
asking whether it clears the bar — not just importing it in `__init__.py`.

**What clears the bar for root:** verbs (`section`, `state`, `computed`), the component
model (`Component`, `ContextKey`, `resource`), every semantic factory (mechanically
required by `TestDrift` — one root factory per `SemanticNode` union member), the full
event vocabulary (kept complete rather than usage-trimmed, so an author writing
`on_open` never has to learn that `OpenEvent` is the one exception living in a
namespace), and the small set of adaptation verbs and central nouns (`Palette`,
`Tone`) that appear in ordinary component bodies.

**What does not:** anything nominal that a component body references by type only in
annotations or construction — `sl.patterns.Wizard`, `sl.forms.DateTimeField`,
`sl.sources.WindowLoader`, `sl.interactions.ActionMiddleware`. These stay one
qualifier away.

Same rule, one level down, for `sl.discord`: its root gets workflow entry points
(`Mount`, `mount`, `compose`, `respond_to`, `Reactor`), not every type those workflows
touch (`RouteRequest`, `SessionPolicy`, `AuditReport` move into `sl.discord.routing` /
`sessions` / `inspection`).

## Target shape

~105 root names (16 namespaces, the component model, 52 mandated semantic factories,
event types, adaptation verbs, and a handful of central nouns), down from 372. Full
allowlist, namespace map, and the five structural landmines that must be defused
first (module/factory name shadowing, a latent `topics.py` ↔ `runtime` circular
import, an undertested `ReactiveCycleError` export, six ambiguously-named symbols,
and a dirty working tree) are tracked in the implementation notes for this plan.

## Commit sequence

Commits 1–5 are pure mechanics (renames, moves, added `__all__`) and must be no-ops
against the test baseline. Commit 6 is the large call-site codemod but removes
nothing from root, so it cannot fail on its own. Commit 7 is the actual root
narrowing and touches only `__init__.py` and `test_public_api.py`. Commit 8 applies
the same policy inside `sl.discord`.

| # | Commit | Scope |
|---|--------|-------|
| 1 | `layouts: rename the actions module to interactions` | pure rename, ~20 files |
| 2 | `layouts: rename the entities module to entity` | pure rename, ~12 files |
| 3 | `layouts: move the topic bus under runtime` | ~13 files; fixes a latent import cycle |
| 4 | `layouts: unshadow the measure and history modules` | `measure.py`→`measurement.py`, `history.py`→`histories.py`, `compose.py`→`composition.py`, `conform.py`→`conformance.py` |
| 5 | `layouts: give every module an explicit __all__` | ~13 modules; adds `ReactiveCycleError` to `runtime.__all__` |
| 6 | `layouts: move specialized names to their namespaces` | codemod over call sites; root still exports everything |
| 7 | `layouts: narrow the root to the authoring vocabulary` | delete ~267 root exports; rewrite `test_public_api.py` |
| 8 | `layouts: reorganize the discord namespace` | narrow `sl.discord.__all__` from 164 to ~25-30 |

## Verification

After each of commits 1–5: zero delta against the frozen baseline
(`../../../../packages/squid-layouts/tests`, `../../../../tests/unit`, `../../../../tests/architecture`, and — run
explicitly, since it is not in pytest `testpaths` — `../../../../tests/integration/layouts`).

After commit 7: `ruff check` on `__init__.py` for stale `__all__` entries (`F822`),
the full suite again, and `pyrefly check` diffed against the pre-change baseline.

The two subprocess isolation tests in `test_public_api.py` (which import the package
under a `MetaPathFinder` blocking `discord`/`anyio`/`asyncpg`) are the end-to-end
layering check: if the reorg accidentally makes a core namespace pull in the Discord
adapter, they fail. Keep both unchanged and treat a failure as a layering regression,
not a test to adjust.

## Docs to update (commit 7 / 8)

- `../../../../packages/squid-layouts/README.md`
- `../../../squid-layouts-architecture.md`
- `../../../../packages/squid-layouts/docs/migrating.md`, `classic-messages.md`, `durable-mounts.md`
