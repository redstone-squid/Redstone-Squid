# 90 — Deferred and rejected

Findings from the 2026-08-21 audit (full package + nine consumers + CascadeUI
comparison) that we consciously decided **not** to act on, with the reasoning — so they
are not re-derived or accidentally adopted later.

## Rejected

- **Redux-style global store** (CascadeUI's cross-view model: dispatch → middleware →
  reducers → subscribers). The local `Component + state() + computed + transaction`
  model is simpler and fits the frontend-neutral tree. Cross-view updates already have a
  path: shared services + `Reactor.schedule`/`Mount.refresh`. If a real many-views-one-
  domain need appears, add a host-side event bus, not a store in the package.
  **Revisited 2026-08-22**: the bus is [26](26-topic-bus.md), moved package-side by the
  productization decision; the store half of this rejection stands in full — the bus is
  payload-free precisely so it can never become one.
  **Revisited again 2026-08-22**: [40](40-shared-state.md) covers one narrow case the bus
  provably cannot — view state that outlives a mount *and* still rolls back with the action
  that wrote it, which the bus cannot carry (payload-free) and a shared service cannot
  either (outside `transaction()`, so `sl.history()` needs a hand-written inverse for state
  the framework should own). It does not overturn this rejection: 40 has no store. A shared
  namespace is an object, shared by being passed, and what it adds is that its writes join
  the transaction and its changes reach the bus and `sl.history()` unaided. No dispatch,
  reducers, middleware or global singleton; addresses still travel the bus and subscribers
  still re-read; and `Controlled`/`Managed` still owns domain truth, with 40 §3 making a
  namespace an unsuitable home for anything durable.
- **Persistence batteries** (SQLite/Postgres `SnapshotStore` implementations,
  reattachment, pruning). The durability layer has **zero production consumers** in
  `squid/` (verified by grep). Building storage backends for an unused subsystem is
  inventory. Revisit only when a view actually needs to survive restarts; the
  `LeaseSnapshotStore` boundary is ready when that day comes.
  **Revisited 2026-08-22**: superseded by the productization standard — the consumer is
  the library user. [27](27-snapshot-stores.md) fills the boundary without moving it;
  the bot itself still, correctly, has no consumer.
- **`compose(into=view)` / adopting existing discord.py views** — re-confirmed: renderer
  ownership is what keeps budget measurement sound. Incremental interop is CascadeUI's
  advantage by design choice, not an oversight here.
  **Revisited 2026-08-22**: this bundled two operations, and only one of them is unsafe.
  *Adoption* — Squid and a live view both claiming lifecycle or edit ownership of one
  message — stays rejected. *Fragment composition* — the host stays the sole owner while
  Squid measures it and contributes a sessionless, fully planned region to what is left —
  is [35](35-discord-v2-fragments.md), and is the supported incremental boundary.
  `sl.discord.contribute(document, to=view)` is the shipped spelling; `into=` remains
  rejected because it names the wrong relationship.
  **Revisited again 2026-08-23**: [53](53-view-adoption.md) splits the surviving half on one
  fact — whether the view has been *sent*. A live view owns a message and will edit it, and
  that case stays rejected exactly as written above. An unsent view owns nothing: it is items
  and callbacks that have not met Discord, so Squid can translate them into its own exact
  primitives, become the sole writer, and leave the legacy object as a model plus handlers.
  Renderer ownership, the property this entry protects, is preserved rather than traded away —
  Squid constructs every item it draws. `adopt()` raises on `view.is_dispatching()`, which is
  what makes this a narrowing and not a reversal — `View.message` is a convention bots follow by
  hand and discord.py never sets, so it is kept only as a secondary signal.
- **Class-body operational policy** (CascadeUI's `owner_only`, `instance_limit`,
  `instance_scope`, `instance_policy`, `participant_limit` as class attributes) — rejected
  2026-08-23 by [43](43-mount-defaults.md). Every one of those values is an actor, a scope,
  or a host decision the same component is opened with differently (`ConsentPrompt` opens as
  a root under `Reject()` and as an attached child two lines apart). A class attribute would
  couple portable components to Discord session vocabulary, and 34 already declines to copy
  class-variable policy. The ergonomics go into a `MountDefaults` value instead.
- **A separate application-layer package** (`squid-ui`: a `UIRuntime` composition root, a
  `Screen` recipe, `Projection` objects for cross-screen reactivity, named policy presets
  like `private_panel`) — proposed externally 2026-08-23 and rejected as a *package*, though
  one of its three ideas survived as [51](51-screens.md). Recorded because the proposal was
  written from the README and re-derived, under new names, three things this series had
  already settled:
  `UIRuntime` is [43](43-mount-defaults.md)'s `MountDefaults` plus a host facade — 43 quotes
  the same motivating snippet;
  `Projection` is [47](47-topic-values.md) phase 2's `sl.watch`, reaching the same conclusion
  ("give the engine a reactive address, do not cache domain state") by a worse route, since
  tracked reads mean the dependency graph is not maintained by hand at all — and it
  contradicted itself, forbidding a store and then proposing a keyed loader-plus-cache with
  wrapped mutations;
  the policy presets are the class-body surface 43 rejected, for the reason 43 gives (the same
  component opens under different policies two lines apart, `squid/bot/consent.py:528-539`).
  The package boundary itself runs against the productization decision: plans 24–28 moved
  host-side helpers *into* `sl.discord` rather than out of it, and a fourth layer would
  re-split what that round deliberately joined. What was worth keeping — that per-open session
  policy is spread across call sites — is 51, landing in `sl.discord` as a value.
- **Context-manager render DSL** (dominate-style) — fights `render()`-returns-a-value
  purity; the factory layer (plan 03) is the chosen ergonomics fix.
- **Python 3.10 backport / PyPI packaging** — irrelevant to this repo (3.14 target).
  Publishing squid-layouts is a product decision to make explicitly, not design debt.
  **2026-08-22**: the productization decision was made — plans [24](24-session-registry-move.md)
  through [28](28-history.md) build for the library user rather than waiting on bot
  consumers. Actual PyPI publication remains a separate, still-unmade call; the 3.10
  backport stays rejected.

## Deferred until a real consumer exists

- **Portable permission facts on `ActionEvent`** — plan 02 gives the typed Discord
  escape hatch instead. If a second frontend ever dispatches events, design the portable
  capability surface against its actual requirements.
  **Revisited 2026-08-22**: partially superseded by [31](31-action-ergonomics.md) — the
  portable admission surface is `Guard`/`GuardVerdict`; frontend facts still enter through
  plan 02's native access (`requires_role` lives in `sl.discord.guards`).
- **Ephemeral session handoff** (Cascade-style: arm a refresh control before token
  expiry, rebuild the session from the fresh interaction). Mostly retired: plan 07's
  `EditHandle` renews on every click, so an ephemeral panel in use stays writable
  indefinitely. What remains is an ephemeral view that needs a *background* refresh after
  more than 15 minutes with nobody touching it — the render simply waits in `Mount.pending`
  until someone does. Only worth building for a view that must update itself unattended,
  which none does.
  **Resolved 2026-08-22**: [26](26-topic-bus.md)'s bus creates exactly those views, so
  this entry's condition is met — and the answer is the paused-chrome banner plus
  click-to-resume, not a handoff control: every control already renews on click, so
  arming a special one adds nothing. The handoff *mechanism* stays rejected.
  **Reopened 2026-08-22**: [39](39-ephemeral-handoff.md) identifies the missing UX contract:
  existing controls mutate application state, while a dedicated renewal action does not.
  The accepted design keeps Cascade's protected pre-expiry screen but renews Squid's same
  mount and message in place instead of reconstructing a view and spawning a successor.
- **Participant tracking / shared sessions** — plan 12 shipped instance policies and
  widened `lock_to` to accept a set of ids; participant *lifecycle* (join/leave, per-actor
  state) waits for a feature that needs it. No consumer needs even the set form today: the
  one multi-actor site, `BuildEditComponent._may_event`, needs an async permission check with
  its own wording, which a static set cannot express.
  **Revisited 2026-08-22**: [31](31-action-ergonomics.md)'s `guards.permission` serves the
  `_may_event` case named here; per-actor state arrives as [32](32-demand-driven.md)'s
  `Agreement` component state. The participant *lifecycle* model is now
  [34](34-safe-session-runtime.md) §B's scope, whose worked lobby/game example is this
  entry's remaining removal condition.
- **`squid_layouts.patterns` library** (Form, Wizard, richer table/list browser à la
  CascadeUI's pattern modules). Likely valuable — the poll wizard and submission form
  are hand-rolled wizards today — but premature before plans 03/04 settle the authoring
  surface they would be built on. Revisit after the presets migration lands. **Revisited 2026-08-21**: 03/04 landed;
  plans [18](18-forms.md)/[19](19-patterns.md) now cover Form, Wizard and
  MultiChoicePanel; Tabs/Menu/RankedList were also migrated under 19's two-shell rule.
  **Revisited 2026-08-22**: continued by the survey batches
  [29](29-control-vocabulary.md)–[32](32-demand-driven.md), including the richer
  table/list browser this entry originally named (30's `Browser`).
- **Grid / matrix interaction** (added 2026-08-21) — content grids are a `Table`
  display strategy (`MATRIX`), not a new node; interactive grids start as an
  `sl.button_grid` factory desugaring to `Row`s, whose exact-structure contract makes
  non-degradability free. The degradation ladder (button grid → text grid +
  coordinate select → paged select) is the semantic-node promotion, and it waits for
  a real consumer.
  **Revisited 2026-08-22**: promoted by [32](32-demand-driven.md); the recorded three-tier
  shape is adopted unchanged.
- **`sl.resource` descriptor** — resolved 2026-08-22 by [33](33-resources.md). Explicit
  `depends=(kind,)` state descriptors provide the missing dependency model; render-observed
  resources stay lazy; monotonic tokens reject stale completions; and `replace()` supplies
  the optimistic set the motivating `SettingsPanel` case required. Visible and awaited
  loading share one `Pending | Ready | Failed` state machine and differ only in whether the
  mount commits the pending discovery render before settling it.
- **Portable form protocol** (replacing the Discord-native modal boundary) — long-noted
  in the architecture doc's gaps; superseded by plan [18](18-forms.md) (2026-08-21).
- **Multi-message rendering** (one logical UI spanning several messages). Two features
  hiding in one thought, with opposite verdicts. *Branching* — a click spawns an
  additional message — is not deferred: it ships today as the consent pattern
  (`account_view.py` mounts `prompt_for_consent` as its own ephemeral message), and its
  missing piece was lifecycle, which plan 12's registry shipped as `open(..., parent=)`. The
  spawn-child-from-`ActionEvent` helper it also proposed resolves to none needed:
  `sl.discord.responder(event).mount` already hands a handler its own mount, which is all
  `parent=` takes. *Spanning* — one root component rendered
  across N messages — is deferred until a consumer exists (the audit found none; search
  and leaderboards fit one message with plan 06). When it comes, the shape is decided:
  Discord's message sequence is append-only, so content cannot reflow between slots —
  growth in slot 1 means rewriting every later slot with no batch edit, no cross-message
  atomicity, and controls migrating between messages. Build *fixed author-declared
  partitions*, each independently budgeted, as a coordinator over per-message mounts
  (sharing services/session, routing invalidation) — never a multi-handle `Mount`, which
  would smear message identity through planner, generations, dispatch, and durability.
  `EditHandle`/`Destination` being per-message is what makes the coordinator cheap; keep
  `on_load`, context, and session policy free of any root-component-equals-session
  assumption so it stays that way.
- **Cross-page multi-select** — resolved 2026-08-21: the grouping/commit model the
  rejection demanded turned out to be Form's submission model, and plan
  [19](19-patterns.md)'s `MultiChoicePanel` supplies it (staged vs committed sets,
  per-window merge, gated Apply). The rejection of engine-side `Managed` merging
  stands.
  **Revisited 2026-08-22**: [30](30-structures.md)'s immediate commit changes when the
  pattern commits, not who merges; the `Managed`-merging rejection stands.
- **Statically checking a route handler's parameters against its route** (plan 16 stage 2)
  — unavailable, and the spike is done, so do not repeat it. `Router.route` uses
  `ParamSpec`, which preserves the decorated signature but cannot constrain it: `P` is
  inferred from whatever was written, so `biuld_id: int` typechecks fine. The only
  construction that would check it is a `Route[ParamsTypedDict]` plus PEP 692
  `**params: Unpack[TD]` in a `Protocol.__call__`. **Pyrefly 1.2 rejects `Unpack` on a
  TypeVar** — "`Unpack` in \*\*kwargs annotation must be used only with a `TypedDict`" —
  including when the TypeVar is bound to a TypedDict base, so the protocol cannot even be
  spelled. The concrete-TypedDict form (`squid/settings/application/ports.py`) is the
  supported case and is not what this needs. It would also reintroduce the drift `Route`
  exists to eliminate, with parameter names and types living in two places, and three of
  five routes carry no parameters at all. Registration-time `inspect.signature` checking
  is the substitute, and it is stricter than Flask's, which waits for the first request.
  Revisit only if pyrefly gains generic `Unpack` support.
