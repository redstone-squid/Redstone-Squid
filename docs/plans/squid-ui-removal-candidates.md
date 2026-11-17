# Squid UI removal candidates

Audited against `45f14377a`. The removal pass is now authorized; the findings
below record the original evidence. Evidence comes from source, import
edges, callers, tests, and exports across the repository. “No caller” below means
no in-repository caller found; it does not establish absence of external users.

The highest-value cleanup is removing obsolete planning machinery and its dependent
concepts. Splitting these modules or improving their typing would preserve the
wrong thing. Related candidates below overlap; their sizes are not additive.

## 1. The old Discord semantic adaptation pipeline — strong

Location: `packages/squid-ui-discord/src/squid_ui/ext/discord/planning/adaptation/`.

The eight files contain 2,610 lines implementing decision nomination, semantic
lowering, generated controls, region adaptation, and their bookkeeping. The entry
points `nominate_decisions` (`decisions.py:133`) and `lower_semantics`
(`lowering.py:194`) are imported by direct tests, but not by production code outside
this subsystem. Current planning uses shared composition and realization offers.

Candidate: delete the subsystem, migrating useful behavioral coverage to the public
planner. Do not build a common abstraction between this old pipeline and the live
one. Check that direct lowering was not intentionally promised as a standalone API.

## 2. The old variant frontier — strongest standalone deletion

Location: `packages/squid-ui-discord/src/squid_ui/ext/discord/planning/frontier.py`.

This 260-line module implements its own variant paths, rung enumeration, static
cost estimates, degradation profiles, and guided stepping. Repository searches
found no imports or external calls to its functions, including `guided_step`,
`resolve_variants`, and `variant_state_bound`. It is separate from the live joint
search implementation. A comment in `primitives/nodes.py` still mentions it.

Candidate: remove the module and stale references. Preserve authored alternatives
and their live composition semantics; remove this unused implementation of them.

## 3. Strategy axes, strategy memory, and their helper family — strong, dependent

Locations: core `planning/search.py`, `presentation.py`, and
`planning/structure.py`; Discord `planning/adaptation/common.py` and
`durability/__init__.py`.

`StrategyAxis`, `StrategyCandidate`, and `choose_strategy` feed the old adaptation
pipeline. `iter_assignments` has test/export references; `ranked_candidates` and
`assignment_cost` have no callers. The live search uses its own occurrence-based
quality and allocation model. Keep `DEFAULT_SEARCH_BUDGET`, which is still live.

The only production strategy-memory reader found is the old nomination code. Its
`_select_strategy` writes `StrategyUpdate`. Nevertheless, `StrategyState` and the
`strategies` dictionary still occupy presentation state, durable serialization,
restoration, and developer-tool output.

Candidate: remove the old strategy model after candidate 1. Remove its exclusively
associated helpers: `branch_paths`, `fallback_rung`, `indexed_children`,
`PaletteHolder`/`scoped_palette`, and `toggle_action_key`. Keep live state-resolution
helpers such as `opened_item` and `navigation_current`. Existing durable records
need an explicit compatibility decision for the serialized `strategies` field.

## 4. The second Discord measurement/planning API — investigate retiring the whole API

Locations: Discord `planning/measurement/`, `planning/classic.py`,
`planning/v2.py`, and `planning/discord_dialect.py`.

`ClassicDialect` and `V2Dialect` still expose `normalize`, `validate`, `paginate`,
and `body`, with converters and measured intermediate nodes. The current
`TargetDialect` contract asks for `composer`, `input_policy`, and `navigation`;
production planning uses the composition rule implementations. The old
`DiscordDialect` protocol has no references beyond its own definition/docstring.

The low-level `measure` function is still explicitly exported and directly tested.
That is a stronger contract than an unreferenced private helper, but I found no
application caller. Decide whether a second layout-solving API is actually a
supported product. If not, delete it and its supporting converters, solver,
measurement-only diagnostics, and tests that merely preserve that implementation.
Preserve independently used validators and host-owned layout inspection rather
than treating every function called “measure” as part of this subsystem.

## 5. `PlanRequest.cache_context()` — strongest helper deletion

Location: core `planning/request.py:121`.

This method constructs a large structural cache dictionary with target, palette,
localization, chrome, navigation, and presentation details. There are no calls to
it anywhere in the repository. The request docstring still presents it as one of
two active cache-key mechanisms. Current composition constructs its partition key
elsewhere; exact replay uses `exact_key()`.

Candidate: delete the method, its now-unused imports, and the stale explanation.
Do not turn it into another cache-key object.

## 6. Planner telemetry that nobody produces — strong

Location: core `planning/result.py:29`, plus Discord rendering, message-root, and
developer-tool instrumentation.

The current planner never assigns `PlanMetrics.states_explored` or
`search_fallback`, never populates `PlanReport.logical_fingerprint`, and never
emits `PlanReuse.STRUCTURAL` or `INCREMENTAL`. Consumers still display or count the
empty/default values. `PlanEvent.before`/`after` also deserve removal after the old
adaptation writers disappear.

Candidate: remove obsolete fields and enum members, migrating displays to actual
metrics such as `work_used`, `target_evaluations`, and `generated_states`. Do not
invent new producers just to justify old instrumentation. Check exported report
consumers when changing the schema.

## 7. A second degradation model attached to `PlanResult` — medium, dependent

Locations: core `planning/degradation.py`, `planning/result.py:111`, and
`planning/composition_planner.py` in `_output`.

Shared composition already has typed fidelity effects. `_output` projects every
selected effect into a `DegradationEffect` with priority zero and
`semantic_steps=1`, losing the original distinctions. `DegradationProfile` still
supports ranking, merging, multiple loss axes, and accumulation. Those richer
operations belong to the old measurement/frontier code; current result consumers
found in the repository are tests of losslessness. The live solver does not rank
candidates using this projected result profile.

Candidate: retire the duplicate profile from shared planning. Retain the useful
losslessness query and any needed diagnostic effects through one authoritative
representation. Do not remove the live solver's fidelity comparisons or strict-mode
checks. Removing the whole module depends on the decision about candidate 4.

## 8. Cache scaffolding that outlived its original contents — strong simplification

Location: core `planning/cache.py`.

`PlanCache[BodyT]` stores only occurrence-to-partition mappings. `BodyT` appears in
no stored value or method result, yet it propagates through planner and adapter
signatures. `_LruStore` has exactly one instantiation: that same partition cache.
Its generic default-return API and capacity-on-every-write interface serve no
second store. `PlanMemo.get`/`put` are only called by its own `replay`/`store` methods.

Candidate: remove the phantom body parameter; let `PlanCache` directly own its
ordered mapping and eviction. Consolidate the memo's raw and convenience APIs into
one pair of meaningful operations. Preserve LRU behavior, immutable stored hints,
exact identity checks, and post-delivery revision promotion.

Do not combine `PlanCache` with `PlanMemo`: callback-free partition hints and an
owner's live callback-bearing result have materially different lifetimes.

## 9. `composer()` → `rules()` → constructor — strong helper simplification

Locations: Discord classic/V2 dialects, HTML `target.py`, Slack `protocol.py`.

All six `composer` implementations call `RuleComposition(self.rules(...))`.
The six `rules` methods construct the concrete rule object; there are no other
in-repository `.rules(...)` calls. This extra factory layer was retained in the
latest refactor without an actual repository consumer.

Candidate: construct the concrete rules directly inside `composer` and remove the
redundant factory methods. Keep `RuleComposition` and `TargetComposition`: that
boundary preserves the payload/resource/preparation type relationship, so deleting
it would undo useful typing rather than remove unnecessary machinery.

## 10. `GeneratedHandler` as a separate concept — medium

Locations: core `planning/generated.py:12`, `interactions/handlers.py:66`, and
concrete planning/pagination handler classes.

`GeneratedHandler` adds an abstract typed `__call__` to `_HandlerComposition`.
No code tests for `GeneratedHandler`; runtime binding dispatches on
`_HandlerComposition`. Every concrete handler already defines its call behavior
and explicit `_bind_handlers`. Its module describes structural cache rebinding,
which is no longer how the callback-free partition cache operates.

Candidate: remove this intermediate base and let concrete handlers implement the
binding contract directly, or consolidate the callable contract with the existing
binding abstraction. Verify static callable checking before deletion. Keep explicit
callback-edge binding: it prevents accidental traversal of application dataclasses.

## 11. Dynamic target factories whose only effect is type erasure — strong simplification

Location: Discord `planning/discord.py:38` and `:45`.

`dynamic_components_v2_target` and `dynamic_classic_target` simply call the ordinary
generic factories and widen the adapter type to `Any`. The V2 helper has one caller
in widget tests; the classic helper has no callers beyond export references.
There is no distinct runtime behavior.

Candidate: migrate that test to the normal factory and delete the parallel API.
Where a runtime-selected adapter really needs gradual typing, handle it at that
specific boundary rather than maintaining duplicate target-construction functions.

## Suggested order and exclusions

Start with 2, 5, and 9: they are small, concrete deletions. Then remove 1 and its
strategy/helper dependencies in 3. Decide the fate of the measured solver before
removing its diagnostics and degradation machinery. Clean telemetry and cache
scaffolding as independent pieces.

Do not mass-inline small functions. `resolved.text` enforces a real invariant;
`_same_context` implements custom context-key equality; `_inside` changes route
semantics during splicing. Chrome's small default functions are injected callbacks,
and the query-index owner keeps derived data out of serialization. Their short
length or small number of callers is not evidence that they are useless.

The original audit changed no runtime code. Removal work must verify public planning,
page turns, strict-mode behavior, callbacks, and durable restoration at the affected
boundaries, instead of relying solely on tests of the machinery being deleted.

## Implementation progress

- Removed the unused variant frontier, request cache context, redundant rule
  factories, and dynamic target constructors in `ef63a650b` (2, 5, 9, 11).
- Simplified partition-cache ownership and exact memo operations in
  `46a6a9e70` (8). A partition cache can now be shared across body types;
  callback-bearing memos remain typed and owner-specific.
- Removed the obsolete Discord adaptation package (1). Regression coverage now
  exercises the public planner for nested tables, deferred text, translated
  page footers, and page budgets.
- Removed strategy axes, remembered strategy choices, and their traversal helpers
  (3). Durable restoration ignores the retired field in older snapshots.
- Removed the generated-handler base in `6c9010243` (10). Concrete callable
  signatures and the explicit callback-binding contract remain.
- Removed unproduced telemetry in `4cc75b625` (6). Developer tools and profiling
  now display actual composition work and evaluations.
- Replaced the projected result degradation profile with typed fidelity effects
  and a derived `lossless` property in `17e7f6e69` (7).
- Removed the uncalled measured-layout dialect API and converters in
  `af4420ce0` (part of 4).
- Retired standalone `measure`, its intermediate nodes, allocation machinery,
  diagnostics, and degradation model (4 and the remainder of 7). Independent
  host inspection remains.
- Remaining: broad UI validation, package-build checks, and final scope audit.

## API changes for callers

Use the ordinary Discord target factories; the dynamic variants are removed.
Dialect implementations construct their rules directly in `composer`.
`PlanCache` no longer takes a body type argument. `PlanMemo` retains its body
type and exposes `replay`/`store` rather than raw `get`/`put` operations.

Plan results expose typed `effects` and `lossless` instead of a synthetic
`degradation` profile. Reports no longer have logical fingerprints or before/after
event deltas. Metrics expose live composition work rather than the retired
`states_explored` and `search_fallback` fields.

Strategy-state APIs are removed. Durable readers accept older snapshots by
ignoring their obsolete strategy field. New snapshots omit it.

### Measurement retirement and coverage

Use public `plan` and `PlanResult.for_page` instead of `planning.measure` and
mutable `MeasuredLayout` instances. The removed `SolveNote` diagnostics and
`LayoutOverflowError` belong to the old solver; public planning reports typed
search diagnostics and fidelity effects.

Resource axes, bare controls, entity selectors, entry priorities, text alternatives,
and count-pagination properties now run through public planning. Existing composition
tests cover native overflow, strictness, budgets, text breaking, page navigation,
allocation reuse, and independently validated output. Renderer properties still
check host protocol limits. Durable restoration and callback-binding tests remain.

Retired tests that specified the old mutable-layout implementation: internal text
pools, scalar degradation-axis ranking, proportional allocation, mutable repaging,
and constant navigation shape. Shared composition certifies whole page families
and has its own fidelity ordering. It does not promise the old allocator's fixed
reservation for `Condense`. Shortening-after-replacements tests use three oversized
entries and an explicit search allowance to isolate that behavior from exhaustive
combinations and default-budget exhaustion.
