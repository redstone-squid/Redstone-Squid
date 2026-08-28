# 90 — Deferred and rejected

Findings from the 2026-08-21 audit (full package + nine consumers + CascadeUI
comparison) that we consciously decided **not** to act on, with the reasoning — so they
are not re-derived or accidentally adopted later.

Grouped by current status, not by original date, so the entries you'd actually reopen
today aren't buried under ones already settled elsewhere. Each bullet carries a
`*(status)*` tag:

- **still rejected** — the decision stands, unqualified, as of today.
- **still deferred** — waiting on a real consumer/condition that hasn't shown up yet.
- **no longer live** — resolved, shipped, overturned, closed, or promoted to an active
  plan elsewhere. Kept for the reasoning trail, not because it's something to revisit.

## Still rejected

- **Redux-style global store** *(still rejected)* — (CascadeUI's cross-view model: dispatch → middleware →
  reducers → subscribers). The local `Component + state() + computed + transaction`
  model is simpler and fits the frontend-neutral tree. Cross-view updates already have a
  path: shared services + `MountScheduler.schedule`/`Mount.refresh`. If a real many-views-one-
  domain need appears, add a host-side event bus, not a store in the package.
  **Revisited 2026-08-22**: the bus is [26](../completed/squid-layouts-redesign/26-topic-bus.md), moved package-side by the
  productization decision; the store half of this rejection stands in full — the bus is
  payload-free precisely so it can never become one.
  **Revisited again 2026-08-22**: [40](../completed/squid-layouts-redesign/40-shared-state.md) covers one narrow case the bus
  provably cannot — view state that outlives a mount *and* still rolls back with the action
  that wrote it, which the bus cannot carry (payload-free) and a shared service cannot
  either (outside `transaction()`, so `sl.history()` needs a hand-written inverse for state
  the framework should own). It does not overturn this rejection: 40 has no store. A shared
  namespace is an object, shared by being passed, and what it adds is that its writes join
  the transaction and its changes reach the bus and `sl.history()` unaided. No dispatch,
  reducers, middleware or global singleton; addresses still travel the bus and subscribers
  still re-read; and `Controlled`/`Managed` still owns domain truth, with 40 §3 making a
  namespace an unsuitable home for anything durable.
  **Revisited 2026-08-23**: the CascadeUI comparison's "steal the scoping/keying ergonomics, but
  not the singleton store" finding lands as [59](../completed/squid-layouts-redesign/59-shared-pool.md)'s scope vocabulary and
  [63](../completed/squid-layouts-redesign/63-stores-package.md), and neither reopens this. 59 keys a *lifetime owner* the host
  constructs and holds — there is still no global, no lookup by type, and no way to reach a
  namespace you were not given; adopting `sessions.py`'s existing scope taxonomy as `ScopeT` makes
  the keying typed, not ambient. 63 is a store, but a store of application values in a package
  *below* the UI library with no edge pointing up, which is this entry's own prescription ("add a
  host-side event bus, not a store in the package") applied to durable data instead of events. The
  test that keeps both honest: neither *store* is reachable from a `squid_ui` import.
  **Amended 2026-08-24** alongside 59's rewrite, which sharpened that last sentence. `SharedPool`
  is re-exported as `sl.runtime.SharedPool`, so a keyed lifetime owner *is* reachable from a
  `squid_ui` import and always was going to be — what stays unreachable is a store, a global,
  and any way to obtain a namespace nobody handed you. 59 also dropped the `sl.discord.scopes`
  module this note's "scope vocabulary" referred to; the taxonomy it adopts is now reached through
  `screens.py`'s existing `Opener`/`Scope`, which strengthens rather than weakens the point, since
  no new surface was added at all.
- **`compose(into=view)` / adopting existing discord.py views** *(still rejected — live-view adoption; the unsent-view case was accepted separately, see below)* — re-confirmed: renderer
  ownership is what keeps budget measurement sound. Incremental interop is CascadeUI's
  advantage by design choice, not an oversight here.
  **Revisited 2026-08-22**: this bundled two operations, and only one of them is unsafe.
  *Adoption* — Squid and a live view both claiming lifecycle or edit ownership of one
  message — stays rejected. *Fragment composition* — the host stays the sole owner while
  Squid measures it and contributes a sessionless, fully planned region to what is left —
  is [35](../completed/squid-layouts-redesign/35-discord-v2-fragments.md), and is the supported incremental boundary.
  `sl.discord.contribute(document, to=view)` is the shipped spelling; `into=` remains
  rejected because it names the wrong relationship.
  **Revisited again 2026-08-23**: [53](../completed/squid-layouts-redesign/53-view-adoption.md) splits the surviving half on one
  fact — whether the view has been *sent*. A live view owns a message and will edit it, and
  that case stays rejected exactly as written above. An unsent view owns nothing: it is items
  and callbacks that have not met Discord, so Squid can translate them into its own exact
  primitives, become the sole writer, and leave the legacy object as a model plus handlers.
  Renderer ownership, the property this entry protects, is preserved rather than traded away —
  Squid constructs every item it draws. `adopt()` raises on `view.is_dispatching()`, which is
  what makes this a narrowing and not a reversal — `View.message` is a convention bots follow by
  hand and discord.py never sets, so it is kept only as a secondary signal.
- **Class-body operational policy** *(still rejected)* — (CascadeUI's `owner_only`, `instance_limit`,
  `instance_scope`, `instance_policy`, `participant_limit` as class attributes) — rejected
  2026-08-23 by [43](../completed/squid-layouts-redesign/43-mount-defaults.md). Every one of those values is an actor, a scope,
  or a host decision the same component is opened with differently (`ConsentPrompt` opens as
  a root under `Reject()` and as an attached child two lines apart). A class attribute would
  couple portable components to Discord session vocabulary, and 34 already declines to copy
  class-variable policy. The ergonomics go into a `MountDefaults` value instead.
- **A separate application-layer package** *(still rejected as a package — a fourth layer above `sl.discord`; individual pieces landed elsewhere, including the reachability half in plan 70, see below)* — (`squid-ui`: a `UIRuntime` composition root, a
  `Screen` recipe, `Projection` objects for cross-screen reactivity, named policy presets
  like `private_panel`) — proposed externally 2026-08-23 and rejected as a *package*, though
  one of its three ideas survived as [51](../completed/squid-layouts-redesign/51-screens.md). Recorded because the proposal was
  written from the README and re-derived, under new names, three things this series had
  already settled:
  `UIRuntime` is [43](../completed/squid-layouts-redesign/43-mount-defaults.md)'s `MountDefaults` plus a host facade — 43 quotes
  the same motivating snippet;
  `Projection` is [47](../completed/squid-layouts-redesign/47-topic-values.md) phase 2's `sl.watch`, reaching the same conclusion
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
  **Revisited 2026-08-23**: [63](../completed/squid-layouts-redesign/63-stores-package.md) adds a package and this entry does not
  forbid it. What was rejected was a *fourth UI layer above* `sl.discord`, re-splitting what the
  productization round deliberately joined and re-deriving `MountDefaults`, `sl.watch` and the
  class-body policy surface under new names. 63 is the opposite direction: durable application
  data, below the UI library and never importing it, answering a question the series has
  consistently said is *not* the UI library's (`docs/squid-ui-architecture.md:268`). The
  distinction this entry turns on is which way the dependency points, so a package that points
  down is outside its reasoning rather than an exception to it. 63 is also mostly *extraction*
  rather than addition: 1,414 lines of it already exist inside `discord/durability/` and already
  import nothing from `squid_ui`, so the package boundary is being drawn where the dependency
  graph already put one.
  **Revisited 2026-08-25**: [70](../completed/squid-layouts-redesign/70-discord-py-interop.md) takes the *reachability* half of the
  `UIRuntime` idea and this entry does not cover it. The rejection's central claim was that
  `UIRuntime` is "[43](../completed/squid-layouts-redesign/43-mount-defaults.md)'s `MountDefaults`
  plus a host facade", which holds for construction and fails for lookup: a `MountDefaults` is a
  value, and a value cannot be found from a `discord.Interaction`. The bot proved the gap the
  expensive way — `squid/bot/ui.py` carries a process-global `install_mount_defaults` with a
  `global` statement, written because `create_mount`'s 21 call sites hold no bot and a challenge
  presenter needs one. A client-keyed lookup is what that global is a bad substitute for, and the
  package already has the pattern in `routing._INSTALLED`/`routers(client)`. What this entry
  actually protects is untouched: 70 adds no policy surface, no presets, no `Projection`, no
  package, and lands inside `sl.discord` rather than above it. The half it leaves alone is the half
  this entry and [65](../completed/squid-layouts-redesign/65-screen-entrypoints.md) both hold back —
  named audience policy, which stays in the host's `Visibility`/`Private` vocabulary.
  70 landed on 2026-08-25 as `sl.discord.install`/`LayoutHost`; the policy half is still deferred.
- **Context-manager render DSL** *(still rejected)* — (dominate-style) — fights `render()`-returns-a-value
  purity; the factory layer (plan 03) is the chosen ergonomics fix.
- **Python 3.10 backport / PyPI packaging** *(still rejected — the 3.10 backport; actual PyPI publication is a separate, still-unmade call, not a rejection)* — irrelevant to this repo (3.14 target).
  Publishing squid-ui is a product decision to make explicitly, not design debt.
  **2026-08-22**: the productization decision was made — plans [24](../completed/squid-layouts-redesign/24-session-registry-move.md)
  through [28](../completed/squid-layouts-redesign/28-history.md) build for the library user rather than waiting on bot
  consumers. Actual PyPI publication remains a separate, still-unmade call; the 3.10
  backport stays rejected.
- **A generic `action.status` (`Idle | Pending | Failed`) in the reactive layer** *(still rejected)* — proposed
  2026-08-24 by an external review, on the correct observation that `sl.Feedback` is fixed
  policy: `_BusyPaint.show` relabels the pressed control and disables the panel, and label text
  plus `restore_on_error` are the only knobs. The need for author-controlled pending UI is real;
  this is the wrong place for it, on two grounds. It has nowhere to live — an `Action` is a frozen
  semantic value rebuilt every render, so status would need mount-side dispatch state keyed by
  action key and plumbed into render, new machinery for a node that is conceptually a value. And
  the need signals the work is in the wrong primitive: `Progress.set` invalidates outside any
  transaction, which is exactly "presentation status, not transactional domain state", and Plan
  68's definition/execution split made an operation re-armable. So the routing rule is that slow
  or effectful work belongs in an operation the action arms, not in the action's transaction.
  The residual — an action that is genuinely transactional *and* slow, such as a large recompute
  with no external effect — cannot move into an operation, and there `Feedback`'s fixed paint is
  correct. Transactional state cannot be a loading indicator: `self.saving = True` stages and is
  invisible until commit, by which point it means nothing.
- **Serializable actions by default** *(still rejected — this is the live default: opt in with `strong_read()`)* — validating every shared cell an action read, not just the
  ones it also wrote, closes write skew and is what a textbook would do. Rejected as the *default*
  and shipped as `strong_read()` instead, after being tried both ways: Plan 68 made it the default
  and it was narrowed back on 2026-08-24. The reason is the shape of real handlers here — they read
  a namespace to decide whether a press is allowed and then write something unrelated, so full
  validation aborts actions that succeed harmlessly, and an abort is not free once the handler has
  done external work. Compare-and-set on a cell the action read *and* wrote stays automatic, because
  there the read is load-bearing by construction. `relaxed_read()` survives the narrowing with a
  smaller job: it opts a read back out from inside a `strong_read()`. Two notes for anyone revisiting.
  Version-over-equality lineage came in with the strict default and is orthogonal — keep it; A→B→A
  must conflict. And on the web, where concurrent actions are ordinary rather than exotic, the
  default probably does invert; that is a porting note, not a reason to invert it here.
- **A closed suffix taxonomy that encodes lifetime in nouns** *(still rejected)* — (plan 67, 2026-08-24) — designed
  as a 17-row table (`Key`/`Address` never end, `Handle`/`Token` expire, `Registry`/`Pool`/
  `Runtime`/`Store` end explicitly, `Snapshot`/`Report`/`Result` end immediately, `Record`/
  `State` outlive the process) and rejected on measurement. The public surface across `sl`,
  `sl.discord`, its twenty sub-namespaces, `squid_reactivity` and `squid_storage` has **93
  distinct class-name suffixes, 60 of them used exactly once**; only 15 recur three times or
  more. A closed table would have to reject `Component`, `Mount`, `Screen`, `Destination`,
  `Composition`, `Target` and `Work`, or grow until it was not a table. Record the numbers,
  not just the conclusion, or this gets re-proposed.
- **Collapsing suffixes to one word per lifetime class** *(still rejected)* — the follow-on idea, worse.
  `TopicBus` → `TopicOwner` and `PersistedPool` → `PersistedOwner` destroy the information
  that a bus delivers and a pool canonicalises, to encode a fact a single method signature
  already carries. Lifetime belongs on verbs; nouns owe one-meaning-per-word instead.
- **`-er` agent-noun consistency** *(still rejected — taste, not a rule violation)* — `ChallengeRunner` and `ChallengeSupervisor` own tasks
  while `ErrorRenderer` and `ChallengePresenter` own nothing, so the suffix says nothing
  about ownership. Real, but it violates no rule and renaming would be taste.
- **A generation object replacing `Resource._request_token`** *(still rejected — the rename; the bug the review incidentally surfaced was fixed separately, see the 2026-08-24 note)* — proposed by an external review
  so a loader would hold permission to complete *its* generation rather than comparing a
  counter. The comparison is three lines at `resources.py:401-421`, correct and commented.
  Churn.
  **Revisited 2026-08-24**: the rename stays rejected and `_request_token` is unchanged, but the
  rejection was tested against a proposal that was purely a spelling, so it never asked what else
  a generation owns. One thing did belong to it: the tracked read set. `_CONSUMER` pointed at the
  `Resource`, and `Resource.sources` is a single dict, so a superseded loader — which does not
  stop, and resumes with its own `_CONSUMER` still set — went on recording into the live
  generation's dependency set. The live value ended up subscribed to state it never read, and
  `Observation.addresses()` could broadcast an address it did not depend on. Fixed by a private
  `_Load` per generation that the winner publishes onto the resource, with a regression test that
  fails without it. The lesson is narrower than "reopen rejections": a rejection is only as wide
  as the proposal it was argued against.
- **`_Candidate` typestate classes** *(still rejected)* — (`StagedCandidate.presented() -> PresentedCandidate`) —
  same review. The half worth having is one `settled` flag, shipped; the other half is already
  enforced a layer down, because `_draw` stages subscriptions and the reconciler refuses a
  second staged set. Three classes to restate what one guard states is emulating Rust syntax
  rather than its principle.
- **An `adopt()` capture/into-component wrapper** *(still rejected)* — the review proposed making adoption an
  explicit move. Plan 53 already enforces unsent-only with a second-writer-refusing proxy, and
  the review itself concedes `adopt()` is pleasantly simple.
- **A universal `Lifetime`/`Owner`/`Borrow`/`Lease` framework** *(still rejected)* — Python will not enforce it
  strongly enough to justify making every call unpleasant. Make invalid ownership transitions
  difficult and resource death explicit; do not emulate the syntax.

## Still deferred (waiting on a real consumer)

- **Extension nodes on the HTML target** *(still deferred, 2026-08-28)* — the HTML planner
  rejects every `Extension` (with a placed message since the exhaustive-dispatch work);
  Discord falls back to `Extension.fallback` when no adapter is registered. HTML cannot
  simply "honor the fallback": `Extension.fallback` is typed as the Discord-shaped
  primitive `Node` (`primitives/nodes.py`) despite the "portable fallback" docstring, so
  there is nothing portable to honor. The open choices: (a) require a portable fallback
  shape on `Extension`, (b) an `extensions` registry on `HtmlTarget` mirroring
  `planning/v2.py`'s adapter lookup, (c) status quo. Upstream of all three sits an
  unresolved design doubt whether a *mandatory* fallback is right at all — its product is
  consumed only on Discord, and only when no adapter is registered, which in practice is
  never; nothing shipped forecloses making it optional. Reopen when a real document needs
  an extension to plan on HTML.

- **Portable permission facts on `ActionEvent`** *(still deferred)* — plan 02 gives the typed Discord
  escape hatch instead. If a second frontend ever dispatches events, design the portable
  capability surface against its actual requirements.
  **Revisited 2026-08-22**: partially superseded by [31](../completed/squid-layouts-redesign/31-action-ergonomics.md) — the
  portable admission surface is `Guard`/`GuardDecision`; frontend facts still enter through
  plan 02's native access (`requires_role` lives in `sl.discord.guards`).
- **Multi-message rendering — the *spanning* half** *(still deferred; the branching half already shipped, see below)* —
  one logical UI spanning several messages, deferred until a consumer exists (the audit
  found none; search and leaderboards fit one message with plan 06). When it comes, the
  shape is decided: Discord's message sequence is append-only, so content cannot reflow
  between slots — growth in slot 1 means rewriting every later slot with no batch edit, no
  cross-message atomicity, and controls migrating between messages. Build *fixed
  author-declared partitions*, each independently budgeted, as a coordinator over
  per-message mounts (sharing services/session, routing invalidation) — never a
  multi-handle `Mount`, which would smear message identity through planner, generations,
  dispatch, and durability. `EditHandle`/`Destination` being per-message is what makes the
  coordinator cheap; keep `on_load`, context, and session policy free of any
  root-component-equals-session assumption so it stays that way.
  (*Branching* — a click spawns an additional message — was bundled with this originally
  and is not deferred: it shipped as the consent pattern, `account_view.py` mounting
  `prompt_for_consent` as its own ephemeral message, with `open(..., parent=)` from plan 12
  as the lifecycle piece and `sl.discord.responder(event).mount` already handing a handler
  its own mount for `parent=` to use.)
- **Statically checking a route handler's parameters against its route** *(still deferred — blocked on a pyrefly limitation, not on a missing consumer)* — (plan 16 stage 2)
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
- **`ActionKey`/`WireId`/`RouteId` newtypes** *(still deferred)* — a logical action key, a per-generation control
  id and a durable route id are three lifetimes flowing through `str`. Real distinction, no
  in-tree defect motivating it, and the routing module docstring already states it in prose.
  Revisit if a mix-up ever ships.

## No longer live (resolved, shipped, overturned, closed, or promoted elsewhere)

- **Abandoning a superseded resource load** *(resolved — shipped as an optional seam in `34d56b52`)* — proposed 2026-08-24 alongside the generation fix,
  so a load whose token has been bumped is cancelled rather than run to completion. No orphan
  motivated it: every `_load` is awaited by whoever started it, and the mount's settle pass runs
  its task group to completion before the next pass, so a superseded load is wasted work rather
  than a leak — and since the resource contract makes a loader safe to run zero, one or many
  times, wasted work is all it can be. Cancellation also has to live in `squid-ui`, because
  `squid-reactivity` is `dependencies = []` and anyio is where CLAUDE.md puts cancellation. The
  removal condition was "a loader expensive enough that the waste shows up, or a port that makes
  concurrent supersession ordinary".
  **Resolved 2026-08-24**, the same day, and by splitting the entry rather than meeting its
  condition. `squid-reactivity` supplies the seam — a `LoadScope` protocol and
  `abandon_superseded_loads`, read when a load *starts* rather than when the resource is
  constructed, so one installation covers a whole settle group — and `_new_generation` cancels
  through it; `sl.discord` installs the `anyio.CancelScope` that makes it real. Uninstalled it is
  inert and a loader still runs to completion, so the contract this entry protects is unchanged
  and no loader is required to be cancel-safe. The dependency argument was right and is what
  chose the shape. [91](91-prior-art.md) §2 records why Dioxus's stronger rule — computations may
  be dropped at *any* `await` — stays rejected as a contract even now that the mechanism exists.

- **Persistence batteries** *(resolved — built by plan 27)* — (SQLite/Postgres `SnapshotStore` implementations,
  reattachment, pruning). The durability layer has **zero production consumers** in
  `squid/` (verified by grep). Building storage backends for an unused subsystem is
  inventory. Revisit only when a view actually needs to survive restarts; the
  `LeaseSnapshotStore` boundary is ready when that day comes.
  **Revisited 2026-08-22**: superseded by the productization standard — the consumer is
  the library user. [27](../completed/squid-layouts-redesign/27-snapshot-stores.md) fills the boundary without moving it;
  the bot itself still, correctly, has no consumer. 27 shipped both `SQLiteSnapshotStore`
  and an optional-extra `PostgresSnapshotStore`, plus the reachability sweep in
  `MountManager.recover` — the storage backends this entry withheld now exist; only a bot
  consumer is still absent, by design.
- **Blind history restore, revisited and reversed** *(overturned — see Plan 68 / the migration guide)* — Plan 28 chose deliberately: a restore is a
  write with no prior read, so it carries no precondition and cannot conflict. The tests said why
  — "a sibling panel setting the same filter is the motivating case, not an error case", and "the
  local half is not held hostage by the shared one" — resting on `Shared` holding view state, so a
  sibling losing its filter to an undo is not data loss. A version check was proposed and reverted
  on 2026-08-24 for exactly that reason, then adopted days later by Plan 68, which had the piece
  the first attempt lacked: weak versus strong targets, so an undo conflicts on what the author
  said matters and stays blind on what it does not. `StateDelta` is gone; see
  [the Plan 68 migration guide](../../plan68-migration.md).
- **Ephemeral session handoff** *(resolved — shipped as plan 39)* — (Cascade-style: arm a refresh control before token
  expiry, rebuild the session from the fresh interaction). Mostly retired: plan 07's
  `EditHandle` renews on every click, so an ephemeral panel in use stays writable
  indefinitely. What remains is an ephemeral view that needs a *background* refresh after
  more than 15 minutes with nobody touching it — the render simply waits in `Mount.pending`
  until someone does. Only worth building for a view that must update itself unattended,
  which none does.
  **Resolved 2026-08-22**: [26](../completed/squid-layouts-redesign/26-topic-bus.md)'s bus creates exactly those views, so
  this entry's condition is met — and the answer is the paused-chrome banner plus
  click-to-resume, not a handoff control: every control already renews on click, so
  arming a special one adds nothing. The handoff *mechanism* stays rejected.
  **Reopened 2026-08-22, then closed**: [39](../completed/squid-layouts-redesign/39-ephemeral-handoff.md) identifies the missing UX contract:
  existing controls mutate application state, while a dedicated renewal action does not.
  The accepted design keeps Cascade's protected pre-expiry screen but renews Squid's same
  mount and message in place instead of reconstructing a view and spawning a successor — shipped.
- **Participant tracking / shared sessions** *(closed — shipped as plan 60)* — plan 12 shipped instance policies and
  widened `lock_to` to accept a set of ids; participant *lifecycle* (join/leave, per-actor
  state) waits for a feature that needs it. No consumer needs even the set form today: the
  one multi-actor site, `BuildEditComponent._may_event`, needs an async permission check with
  its own wording, which a static set cannot express.
  **Revisited 2026-08-22**: [31](../completed/squid-layouts-redesign/31-action-ergonomics.md)'s `guards.permission` serves the
  `_may_event` case named here; per-actor state arrives as [32](32-demand-driven.md)'s
  `Agreement` component state. The participant *lifecycle* model is now
  [34](../completed/squid-layouts-redesign/34-safe-session-runtime.md) §B's scope, whose worked lobby/game example is this
  entry's remaining removal condition.
  **Closed 2026-08-24**: [60](../completed/squid-layouts-redesign/60-session-membership.md) shipped `join`/`leave`, per-session
  capacity, durable membership and the cross-session quota 34 §B.4 asked for, with
  `/layout lobby` as the worked example this entry required. The quota was very nearly
  deferred a third time on "no consumer needs it" — the same reasoning this entry had already
  been reopened twice under. [59](../completed/squid-layouts-redesign/59-shared-pool.md) is the counter-precedent: it shipped a
  pool that added no capability, purely so hosts stopped re-deriving one. Batteries a library
  user expects are part of the product, and this series' bar for *speculation* is not a bar
  for *completeness*.
- **`squid_ui.patterns` library** *(closed — delivered across 18, 19, and survey batches 29–32)* — (Form, Wizard, richer table/list browser à la
  CascadeUI's pattern modules). Likely valuable — the poll wizard and submission form
  are hand-rolled wizards today — but premature before plans 03/04 settle the authoring
  surface they would be built on. Revisit after the presets migration lands. **Revisited 2026-08-21**: 03/04 landed;
  plans [18](../completed/squid-layouts-redesign/18-forms.md)/[19](../completed/squid-layouts-redesign/19-patterns.md) now cover Form, Wizard and
  MultiChoicePanel; Tabs/Menu/RankedList were also migrated under 19's two-shell rule.
  **Revisited 2026-08-22**: continued by the survey batches
  [29](../completed/squid-layouts-redesign/29-control-vocabulary.md)–[32](32-demand-driven.md), including the richer
  table/list browser this entry originally named (30's `Browser`). Batch D (roster, tally,
  grid, `Agreement`) shipped under [32](32-demand-driven.md) on 2026-08-24; this entry is closed.
- **Grid / matrix interaction** *(closed — shipped as batch D of plan 32)* — (added 2026-08-21) — content grids are a `Table`
  display strategy (`MATRIX`), not a new node; interactive grids start as an
  `sl.discord.button_grid` factory desugaring to `Row`s, whose exact-structure contract makes
  non-degradability free. The degradation ladder (button grid → text grid +
  coordinate select → paged select) is the semantic-node promotion, and it waits for
  a real consumer.
  **Revisited 2026-08-22**: promoted by [32](32-demand-driven.md); the recorded three-tier
  shape was adopted and shipped on 2026-08-24 with a variadic cell API; this entry is closed.
- **`sl.resource` descriptor** *(resolved — shipped as plan 33)* — resolved 2026-08-22 by [33](../completed/squid-layouts-redesign/33-resources.md). Explicit
  `depends=(kind,)` state descriptors provide the missing dependency model; render-observed
  resources stay lazy; monotonic tokens reject stale completions; and `replace()` supplies
  the optimistic set the motivating `SettingsPanel` case required. Visible and awaited
  loading share one `Pending | Ready | Failed` state machine and differ only in whether the
  mount commits the pending discovery render before settling it.
- **Portable form protocol** *(resolved — superseded by plan 18)* — (replacing the Discord-native modal boundary) — long-noted
  in the architecture doc's gaps; superseded by plan [18](../completed/squid-layouts-redesign/18-forms.md) (2026-08-21).
- **Cross-page multi-select** *(resolved — shipped as plan 19's `MultiChoicePanel`; one narrow sub-rejection still stands)* — resolved 2026-08-21: the grouping/commit model the
  rejection demanded turned out to be Form's submission model, and plan
  [19](../completed/squid-layouts-redesign/19-patterns.md)'s `MultiChoicePanel` supplies it (staged vs committed sets,
  per-window merge, gated Apply). The rejection of engine-side `Managed` merging
  stands.
  **Revisited 2026-08-22**: [30](../completed/squid-layouts-redesign/30-structures.md)'s immediate commit changes when the
  pattern commits, not who merges; the `Managed`-merging rejection stands.
- **A `CompensableEffect` saga interface** for external side effects *(overturned — shipped by Plan 68)* — plan 28's History
  already separates a transactional `StateDelta` from an author-supplied external inverse, and
  gives the tiers. Nothing to add until a consumer needs compensation ordering.
  **Overturned 2026-08-24** by Plan 68, both halves, for reasons this entry did not weigh. The
  History argument covers undo of a *committed* action; `record()` fires through
  `on_action_commit`, which never runs on a rolled-back one. The uncovered case is the mirror —
  an action whose external work already happened and whose commit then failed — and the author
  cannot close it by hand, because `ReactiveConflictError` and a failing participant `prepare()`
  both fire after the handler returns. Strict read-set validation raised how often that happens.
  Plan 68 shipped `on_action_rollback` for the notification and, having found the consumer this
  entry was waiting for, the ordering half too: `CompensationOutbox` with idempotency keys,
  restart recovery and reconciliation. See [the Plan 68 completion audit](../68-completion-audit.md).
