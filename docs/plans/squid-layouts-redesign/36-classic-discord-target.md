# 36 — Classic Discord target

## Why classic is not only a migration ramp

A Components V2 message has **no `content` field**. Everything that reads `content` therefore
sees nothing: reply previews, search results, push notifications, forwarded-message previews,
and any automation keyed on message content. A bot whose message must ping someone *and* be
readable in the notification cannot use V2 at all. Embeds also carry author, footer, timestamp,
and field structure with no V2 equivalent, and they are the only Discord surface that renders a
titled, coloured, field-structured card beside plain message text.

So a classic target is a permanent capability, not a transitional one. The migration payoff is
real but secondary:

1. Replace hand-built embed content with a Squid semantic document, keeping the classic message
   mode.
2. Move individual controls onto Squid actions while calling the same application functions.
3. Let a classic `Mount` own the screen's reactive lifecycle.
4. Select the V2 target for a new or replaced message without rewriting the semantic component.

The correct feature is a second exact Discord target, not `compose(..., into=old_view)`. Editing
a classic message into a `LayoutView` requires explicitly clearing content, embeds, and
attachments, and the message can never return to the classic representation, so a V2 fragment
cannot migrate one region of a V1 screen. Transferring items out of an existing `View` is not a
substitute either: view-level access, error handling, timeout state, decorator bindings, and
callback-store registration belong to the original view and do not travel with its child list.

> A classic target lets Squid produce and own a legal classic Discord message. It does not adopt
> the lifecycle of an arbitrary existing view.

## The cost, stated honestly

**There is no in-tree consumer.** `squid/` contains zero `discord.Embed` and zero
`discord.ui.View`; the bot is entirely on V2. Nothing here can be dogfooded, no migration is
performed by landing it, and its only end-to-end evidence is synthetic tests plus the live
experiment in [Verification](#verification).

That makes this a product bet on library users, in the sense
[24](24-session-registry-move.md) opened. It is also the largest single plan in the series: a
second target reaches adapters, limits, the solver, primitives, the scene, the codec, the
renderer, mounts, and durability. §A and the rename in step 2 are worth landing on their own
merits; **everything from §B onward should wait for someone to ask for it.**

## Boundaries and invariants

- One message still has one lifecycle and edit owner. Plan 35's rule applies unchanged.
- Both targets use the semantic tree, adapter registry, bounded search, exact resource
  accounting, action bindings, presentation state, and degradation report.
- Classic layout decisions happen in planning. The renderer mechanically constructs content,
  embeds, and rows from an exact classic scene; it does not infer field grouping or overflow.
- The classic target may choose lossy semantic strategies when the author permits them. It does
  not pretend every V2 exact primitive has a lossless classic representation.
- Application and database state remain outside the UI package. This plan adds no shared store.
- Arbitrary callback-bearing `View` adoption remains rejected. Legacy application functions are
  reusable; legacy view ownership is not.

## Dependencies

- [38](38-discord-presentation.md) for `DiscordPresentation` and the mode transition matrix. A
  classic renderer produces content and embeds, so there is nothing for it to return until 38
  exists.
- [35](35-discord-v2-fragments.md) units 1 and 2 for `sl.discord.measure`/`audit` and for
  `TargetProfile.resources` + `reserve()`, which §A generalizes rather than replaces.

## A. Message-wide budgets become named axes

Plan 35 unit 2 introduced `TargetProfile.resources`, mapping a resource name to the message-wide
limit attribute a reservation withholds from. Today it exists only for `reserve()`. Promote it
into *the* message-wide budget vocabulary, so the planner reads caps through the same names it
already reserves against.

Four changes, all verifiable against the **V2** target before any classic code exists:

- Measured usage becomes a `ResourceCost` per axis instead of the `measured.components` scalar.
  `planner.py`'s `measured.components > limits.total_components` check becomes "any axis over its
  cap", and the root-pagination remedy message names the offending axis.
- `measure._measure_once`'s `range(limits.total_components + 1)` is a fixpoint-iteration guard,
  not a semantic bound. Name it as one and give it its own constant.
- **Multi-axis text is the one genuinely new solver capability.** Today `_allocate_budgeted`
  distributes a single integer over every text unit. Tag each unit with the axis it draws from
  and run the allocator once per axis over the units tagged to it. Classic puts one `Content`
  node on `content_text` and everything else on `embed_text`, so this is a partition of
  `builder.units`, not a new allocator.
- **Per-value caps stay local shape, not budget.** `embed_title`, `embed_description`,
  `field_name`, `field_value`, `embed_footer`, and `embed_author` are checked by `_validate` and
  clamped by `measure._Clamper`, exactly as `button_label` and `option_label` are today. This is
  the rule plan 35 §A.2 already states for `row_buttons` and `select_options`: local caps
  describe the target's shape, not the remaining room.

One imprecision to record rather than solve. The allocator can grant a description unit more than
its 4096 cap, after which the clamp discards the excess and the budget is wasted. The classic
adapter mitigates by wrapping a description region in `Budget(preferred=4096)`. It cannot do the
same per field, because nested `Budget` regions currently flatten to the outermost owner —
`_allocate_budgeted` claims `reversed(builder.budgets)` outer-first by design, so an inner region
gets no units. v1 relies on clamping for fields and says so.

## B. Classic target profile

Add `sl.discord.CLASSIC_TARGET` beside an explicit `V2_TARGET` (today `DEFAULT_TARGET`). Every
value below is a starting point to be re-verified against current Discord API documentation
during implementation, and the documentation consulted is pinned in the test.

| Message-wide axis | Value | Local cap | Value |
|---|---|---|---|
| `content_text` | 2000 | `embed_title` | 256 |
| `embed_text` | 6000 | `embed_description` | 4096 |
| `embeds` | 10 | `embed_fields` | 25 |
| `rows` | 5 | `field_name` | 256 |
| `controls` | 25 | `field_value` | 1024 |
| `attachments` | 10 | `embed_footer` | 2048 |
| | | `embed_author` | 256 |

Row width, button label, select option caps, custom-ID length, and modal limits are Discord-wide
and stay in one place. No classic strategy may borrow the V2 totals of 40 components or 4,000
display characters.

Say which side enforces what, because almost nothing here is caught locally:

- discord.py enforces `len(embeds) > 10` in `http.handle_message_parameters` and a 25-child cap
  on `discord.ui.View`. That is all.
- The 6000-character aggregate and every per-value cap are server-only, so they need a strict
  payload audit. It walks `Embed.to_dict()` output beside the component payload, mirroring the
  existing wire-payload walk in `discord/testing.py`, and uses `Embed.__len__` for the aggregate.
  `Embed.__len__` already computes exactly Discord's definition — title, description, field names
  and values, footer text, author name — so the audit must not re-derive it.

Cross-check tests follow `tests/test_limits_crosscheck.py`.

## C. Divergence happens at semantic lowering

This is the layer the design turns on. `Fields` and `Field` are destroyed during lowering —
`adaptation._field_entry` returns `str | Alt` inside a `Lines` node — so by the time a renderer
sees anything, field-ness is gone. A classic target that decides embed structure downstream of
lowering has nothing left to decide with.

`lower_semantics` is already the right seam: it takes `capabilities` from the target and already
drops `Variant` rungs whose `requires` the target lacks. The classic profile declares
`{message.content, layout.embed, layout.embed_fields, actions.buttons, actions.select,
forms.modal}` and **not** `{layout.container, layout.section, layout.gallery}`.

### 1. Classic primitives join the shared union

Add classic-shaped nodes to `primitives.nodes.Node` rather than building a parallel IR, so one
`Variants` ladder can offer a V2 rung and a classic rung for the same region, and
`_lower_children`, `resolve_variants`, and `measure_nodes` stay single implementations:

- `Content(text)` — message content.
- `Card(title, children, fields, footer, author, accent, image, thumbnail, timestamp)` — one
  embed.
- `CardField(name, value, inline)`.

`_validate` gains one rule: a node whose required capability the target lacks is a planning
error. That protects V2 from classic nodes as much as the reverse.

**Rename `primitives.Embed` to `Boundary`** in the same work. It means "keyed component
boundary" and nothing else, and leaving a node called `Embed` in the same union as actual embed
rendering is a trap for every future reader. It is 24 references and two construction sites
(`measure.py`, `runtime/component.py`), and the series has already taken deliberate breaks
(plan 34).

### 2. Adapter ladders

Each is an ordered `Variants`, so the existing global fit search picks the rung, not the adapter:

- `Article`/`Section` → one `Card`, then several `Card`s, then paginated `Card`s.
- `Fields` → `CardField`s while they fit, then description text, then pagination.
- `Heading` → card title at the top level, bold description line otherwise.
- `Media` → embed `image`, then `thumbnail`, then a link row.
- `Table` → aligned code block, then `CardField`s, then pagination.
- `Actions`/`Choices` → rows and string selects under the five-row and five-per-row constraints,
  reusing the existing option-pagination state unchanged.
- Accent → embed colour. `Footer` → embed footer text.
- Document pagination owns both embed windows and control rows, so one page is one exact payload.

`strict=True` rejects any rung whose cost tier represents author-visible loss. Sections with
accessories, galleries, and downloads have no lossless classic form: they either take a
documented ladder or require author-supplied `Variants`. `NativeItem` stays gated to the V2
target and lowers to its required portable fallback under classic planning. Exact V2 primitives
are never silently reinterpreted when their shape has no classic contract.

## D. Classic scene and renderer

Add exact scene values:

```python
SceneClassicMessage(content, embeds, rows, assets)
SceneEmbed(title, description, fields, footer, author, colour, image, thumbnail, timestamp)
SceneEmbedField(name, value, inline)
SceneClassicRow(controls)
```

Target id `discord.components-v1`, version 1. Embed grouping, field placement, row allocation,
and pagination are all resolved before drawing. The scene carries action references and routed
IDs but no callbacks and no discord.py objects, and gets canonical codec and schema coverage like
the V2 scene. V2 and classic scene nodes may share portable leaf types where that keeps the codec
honest, but the renderer must never branch on semantic intent that planning failed to encode.

Three renderer facts that are not obvious and must survive into the code comments:

- The renderer builds a real `discord.ui.View`. It cannot reuse `LayoutView` with ActionRows:
  `ActionRow._is_v2()` returns `True` by deliberate upstream design, so an ActionRow-only
  `LayoutView` still sets the `components_v2` flag even though its payload is identical to
  classic.
- `discord.ui.View` rejects `ActionRow` items outright. Rows become explicit `row=` indices on
  bare `Button` and `Select` items, and the planner has already satisfied `_ViewWeights`.
- `View` caps at 25 children. Any `add_item` failure after planning is a `DrawInvariantError` in
  tests; production may retain the existing ugly-but-delivered `conform` fallback only for
  repairable upstream drift.

The public entry point is target-explicit and returns a complete payload:

```python
composition = sl.discord.classic.compose(document, localization=localization)
await destination(composition.presentation)
```

`sl.discord.classic.render_static` returns a `DiscordPresentation`, never a bare view, because
the embeds are inseparable from the rendered result. Sessionless documents may contain links and
routed controls; component-local actions remain an error without a mount.

## E. Classic mounts

`MountedView` is about thirty lines — `on_timeout`, `is_dispatchable`, `on_error`, and a mount
back-reference. Extract those into a mixin over discord.py's `BaseView`, which both `View` and
`LayoutView` derive from, then declare `MountedView(mixin, LayoutView)` and
`ClassicMountedView(mixin, View)`. `_WiredButton` and `_WiredSelect` generalize their view type
parameter. Nothing else moves.

```python
mount = sl.discord.Mount(
    component,
    target=sl.discord.CLASSIC_TARGET,
    access=sl.discord.Owner(user_id),
)
```

Mount ID, generation-qualified custom IDs, access policy, stale-event handling, action
serialization, the transaction funnel, timeout, error hook, forms, navigation, and
stage → deliver → commit are all unchanged. Buttons and selects are a different mechanical
drawing of the same `ActionBinding`s.

Do not fork the mount lifecycle into a `ClassicMount`. The target-specific pieces are candidate
planning, the renderer and view factory, the payload audit, and delivery serialization. If a
lifecycle branch appears anywhere else, extract the shared operation before adding a mode
conditional.

A mount has one target for its lifetime; changing target means opening a replacement mount, not
mutating a live mount's renderer under its action bindings. History, resources, topic refresh,
sessions, and the plan-34 durable runtime work unchanged. Durable snapshots record target id and
version, and recovery refuses an unavailable or mismatched target. Discord reconnection redraws
and edits the complete classic presentation before registering the recovered callback view.

## F. Host-owned classic messages

Squid does not need a fragment object here. A `DiscordPresentation` is already a complete
payload value, so contributing to a host-owned classic message is a measured reservation plus
value composition:

```python
reservation = sl.discord.classic.measure(
    content=content, embeds=existing_embeds, view=existing_view, attachments=2
)
composition = sl.discord.classic.compose(document, reservation=reservation)
await ctx.send(**host.merged_with(composition.presentation).to_kwargs())
```

`measure` returns a `ResourceCost` over the classic axes plus the host's custom IDs, computed
with `Embed.__len__` and a `View` walk. It never mutates the host and raises on an already
invalid one — the same contract as plan 35's `sl.discord.measure`, and ideally the same public
function dispatching on view type.

Merging is pure: embeds concatenate, rows append after host rows, content is used only when the
host left that axis free, and a merge that would exceed any axis raises before send. Neither
input is mutated.

The boundary the fragment design discovered is kept: **never splice fields into a host-authored
embed**, because Squid cannot own its internal layout or overflow policy. Squid replaces whole
embeds and whole control regions, nothing finer. That is still enough to migrate one card or one
control region at a time while the host keeps its lifecycle.

Contributed controls stay limited to links and routed actions. The host view's callbacks remain
under its owner, arbitrary native callback items cannot enter, and a contributed region is never
reactive independently of the host — all carried over from plan 35 §B.4 unchanged, including the
warning that routed controls do not reset the host view's timeout.

## G. Migration guide and non-goals

Ship a worked classic-to-V2 example built on one semantic component:

1. Hand-built embed plus decorated `View`.
2. Host-owned classic message with a measured, statically composed Squid region.
3. Classic Squid `Mount` whose action handlers call the unchanged service functions.
4. A newly opened V2 mount selected by changing only the target and any target-specific
   `Variants`.

The guide must distinguish reusable callback logic from callback ownership. A legacy function can
be called from a portable action with `sl.discord.native(event)` when it needs the interaction; a
decorated item or a whole live view is not transferred.

Explicit non-goals:

- preserving arbitrary `View.interaction_check`, `on_error`, timeout, or navigation stacks;
- editing a Components V2 message back into classic mode;
- pixel-identical embed reproduction from semantic input;
- making exact V2-native extensions work without a classic or portable fallback;
- two independently mounted regions in one classic message;
- a fragment object for classic, with its own attachment state machine;
- choosing a message mode automatically — the author picks a target;
- treating embeds as an application state store.

## Implementation sequence

1. `planning: budget message-wide resources by name` — axis vocabulary, multi-axis text
   allocation, axis-aware overflow diagnostics. Verifiable against V2 alone.
2. `primitives: rename the component boundary node` — `Embed` → `Boundary`.
3. `planning: add classic Discord limits` — limit table, capability set, official and discord.py
   cross-checks.
4. `primitives: describe classic message structure` — `Content`, `Card`, `CardField`, and
   capability validation.
5. `planning: lower semantics for the classic target` — adapter ladders and degradation tests.
6. `scene: describe classic Discord messages` — exact scene values, canonical codec, schema.
7. `discord: render static classic messages` — renderer, strict payload audit,
   `classic.compose`.
8. `discord: mount classic component views` — `BaseView` mixin, `ClassicMountedView`, and
   target-aware durability metadata.
9. `discord: measure host-owned classic messages` — `classic.measure`, presentation merging, and
   the adoption cookbook.

Steps 1 and 2 are worth landing whatever happens to the rest.

## Verification

- Property tests generate semantic documents at every classic resource boundary and prove a
  successful plan satisfies per-value, aggregate embed, row, control, and attachment limits.
- Property tests prove *every* named axis is honoured, not a components scalar — including that
  `content_text` and `embed_text` are separate pools, and that exhausting one does not degrade
  content allocated from the other.
- An ActionRow-only `LayoutView` still reports `has_components_v2()`. This pins the reason the
  classic renderer builds a `View` and is the upstream assumption most likely to change.
- The payload audit catches a 6000-character aggregate overrun that discord.py accepts locally,
  and every per-value cap the API enforces alone.
- Limit cross-checks exercise discord.py construction and serialization, and pin every
  server-only constant to the official documentation consulted during implementation.
- Scene round trips are canonical and contain no callbacks or discord.py objects.
- The renderer is mechanical: a malformed or over-budget classic scene fails strict audit rather
  than invoking a hidden degradation path.
- Classic mount tests reuse the access, stale-generation, exclusive/rebase, transaction, timeout,
  navigation, form, resource, history, and failed-delivery contracts currently run against V2.
  Target-agnostic cases become parametrized rather than copied.
- Recovery preserves target identity and reinstalls classic callback registrations before a
  session becomes live.
- `classic.measure` reserves host content, embeds, and rows exactly; merging is pure and leaves
  both inputs unmutated; a merge exceeding any axis raises before send.
- **Gate:** a live V1 → V1 edit and a live V1 → V2 replacement against a real message. With no
  in-tree consumer, this is the only end-to-end evidence the target works, so it is required
  rather than nice to have.
- Run the focused planning, scene, Discord, delivery, and durability suites with `--no-cov`, then
  `just typecheck`, changed-file formatting and linting, architecture tests, and
  `git diff --check`. The mount and delivery blast radius warrants the full package suite
  locally; the full application suite stays CI-owned unless failures show wider coupling.

## Status

Proposed 2026-08-22. Redesigned 2026-08-22 after verifying every claim against the tree: there is
no in-tree consumer, the limits work is a solver change rather than a plumbing extraction, embed
fields cannot be chosen downstream of semantic lowering, `ActionRow` forces a real
`discord.ui.View`, and the fragment API was cut in favour of a measured reservation over
[38](38-discord-presentation.md)'s presentation value.

**Shipped 2026-08-22** in twelve commits, `primitives: rename the component boundary node`
through `docs: document classic target adoption`. User guide:
[`packages/squid-layouts/docs/classic-messages.md`](../../../packages/squid-layouts/docs/classic-messages.md).

Six things came out differently from the sketch, each because the tree said so:

- **Rung distance stayed in the degradation profile** rather than moving to the cost vector.
  `Variants.priority` groups that profile, and priority has to keep steering which ladder gives
  way first even when every rung on offer is exact. It sits below every real loss axis instead.
- **Strictness became severity-driven.** A new `SolveNoteSeverity.ADAPTATION` is what makes an
  exact later rung strict-safe; the note still appears in the report, it just is not loss.
- **Card folding lives in semantic lowering, not the dialect.** After lowering, an authored
  region and loose prose are both just cards, and only the semantic layer can tell them apart.
  Merging two authored `Article`s into one embed would regroup the document rather than express
  it.
- **The limits object owns its own axis names.** `TargetProfile.resources` became an override
  rather than a second declaration of the same facts, because two declarations drift.
- **A registry override is allowed.** `Target.classic(limits=...)` keeps the built-in id — it is
  still a classic message — so refusing to replace a built-in would have made customized limits
  unusable with durability. The recorded fingerprint does the real work at resolve time.
- **A mounted classic renderer always builds its view**, even with no controls: the view owns
  the mount's timeout, so a screen of pure prose would otherwise never time out.

The live gate is **not** met. A classic-to-classic edit and a classic-to-V2 replacement against
a real message have not been run, and remain the only end-to-end evidence this target lacks.
