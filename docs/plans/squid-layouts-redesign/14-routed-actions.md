# 14 — Routed actions: first-class stateless controls

## Problem

The framework has three interactivity tiers and only represents two:

1. **Session mounts** (`Mount`) — in-process handlers, per-generation custom_ids.
2. **Stateful session recovery** (`durability.MountManager`) — snapshots + leases;
   currently unused.
3. **Stateless routed controls** — restart-surviving buttons on mass-posted cards,
   where the custom_id is the state pointer and the DB owns the state.

Tier 3 is entirely outside the framework today. Five hand-written
`discord.ui.DynamicItem` subclasses (`voting/controls.py` poll:close/poll:refresh,
`submission/ui/components.py` edit:build:(\d+), `consent_banner.py`,
`give_redstoner.py`), each carrying `template=` regex + `from_custom_id` boilerplate +
`# pyright: ignore` overrides, splice into measured documents via
`primitives.RawItem(factory)` with `cast(discord.ui.Item[LayoutView], ...)`
(the cast is documented as a known wart at voting/controls.py:33-42), and register via
`bot.add_dynamic_items` at startup.

Structural consequences:

- The renderer hard-fails on any interactive control in a sessionless scene
  (`discord/renderer.py:76-79`), so semantic `Action` — which requires an in-process
  `on_trigger` callable — cannot appear on reconciler-managed posts at all. The vote
  card, starboard card, and consent banner are permanently stuck on
  `primitives.Panel` + `RawItem`, capping plan 04's semantic-first goal.
- `RawItem` factories are process-local closures, so these scenes violate the scene
  protocol's own promise: they cannot be serialized by `sl.scene.Codec`, and the HTML
  renderer cannot represent the controls.
- Note this is **not** a durability problem: `MountManager`'s snapshot-per-session
  model is the wrong shape for thousands of cards with two stateless buttons and no
  per-message state. Do not unify these tiers; represent tier 3 natively.

## What the first draft got wrong

The first draft proposed a framework-owned namespace `rt:{route}:{payload}` with one
`DynamicItem` whose template was `rt:(?P<route>[^:]*):(?P<payload>.*)`. Re-reviewing it
against the actual consumers and against discord.py's dispatcher turned up three
defects, and the redesign below is built around avoiding them.

1. **It breaks every already-posted message.** Rewriting `edit:build:123` as
   `rt:edit:build:123` means every build card ever posted in the build log, every vote
   card, and the sticky consent banner get a dead button forever. Surviving a restart
   *is the entire purpose of tier 3*; a migration that discards the existing population
   defeats it. Route ids must stay byte-identical.
2. **The template cannot parse its own routes.** Every existing route name contains a
   colon (`poll:close`, `build_log:consent`, `remove:role:redstoner`), so
   `rt:(?P<route>[^:]*):(?P<payload>.*)` would parse `rt:poll:close` as route `poll`,
   payload `close`.
3. **It assumed the rendered component must be a `DynamicItem`.** It need not.
   `ViewStore.schedule_dynamic_item_call` rebuilds the view with
   `LayoutView.from_message(...)` and locates the base item *by custom_id*, then swaps
   in a freshly constructed dynamic item. Nothing requires the outgoing component to
   have been dynamic. So the renderer can emit a plain `discord.ui.Button` and the
   `cast(discord.ui.Item[LayoutView], ...)` wart disappears at the root rather than
   being relocated into the framework.

## Design

### Routes own their custom-id format

A `Route` is a format string, not a name in a framework namespace:

    EDIT_BUILD = sl.Route("edit:build:{build_id}")
    POLL_CLOSE = sl.Route("poll:close")

`Route` derives everything it needs from that one string:

- `route.id(build_id=5)` → `"edit:build:5"`, validated at build time: every parameter
  supplied exactly once, no `:` inside a value (that is the field separator), result
  within Discord's 100-char custom_id budget. A too-long id is a `LayoutInvariantError`
  at planning time, not a 50035 at send time.
- `route.pattern` → `re.compile(r"edit\:build\:(?P<build_id>[^:]+)")`, derived from the
  same parse, so id-building and id-matching cannot drift.
- Format validation at construction: `{name}` placeholders only, identifier names, no
  positional or format-spec fields.

All five existing routes express exactly their current custom ids, so no posted message
loses a button:

| today | route format |
|---|---|
| `poll:close` | `"poll:close"` |
| `poll:refresh` | `"poll:refresh"` |
| `build_log:consent` | `"build_log:consent"` |
| `remove:role:redstoner` | `"remove:role:redstoner"` |
| `edit:build:(\d+)` | `"edit:build:{build_id}"` |

### Nodes

- `primitives.RoutedButton(label, custom_id, style, emoji, disabled)` — exact placement,
  including as a `Section` accessory or inside a `Row`.
- `semantic.RoutedAction(key, label, custom_id, tone, emphasis, available)` — a sibling
  of `Action` and `Link` inside `Actions`/`ActionGroup`, so routed and session controls
  can be grouped and degrade together. `Link` is the precedent: the collection already
  holds a control with no in-process handler.

The node carries a finished `custom_id` string rather than a route plus a parameter
mapping. Callers write `sl.routed_action("Edit", EDIT_BUILD.id(build_id=build.id),
key="edit")` — explicit, typed, and it keeps `**kwargs` parameter smuggling (which would
collide with `key`/`tone`/`label`) out of the factory signature.

`RoutedAction` deliberately does **not** exist as a semantic node with a `route:` name
resolved late: the whole point is that the id is opaque, stable, and computable without
a registry, so a `render_static` document can be planned in a process that has no
router at all.

### Scene

`SceneRoutedButton(label, custom_id, style, emoji, disabled)` joins `SceneNode`,
`SceneRow.items` and `SceneSection.accessory`. It has no `action` field and needs no
entry in `PlanResult.bindings` — that absence is what lets the renderer draw it with
`wire=None`, so `render_static` documents may carry controls. `conform` measures it like
any button. Scenes become fully serializable (a custom_id is a string, unlike a `RawItem`
closure), the codec round-trips it under `kind: "routed_button"`, and the HTML renderer
emits the custom_id the way it already emits action identifiers.

### Dispatch

One framework-owned `discord.ui.DynamicItem` subclass, generated by the router at
`register()` time, whose template is the alternation of every registered route's
pattern with its groups made non-capturing:

    router = sl.discord.Router()

    @router.route(POLL_CLOSE)
    async def close_poll(interaction: discord.Interaction, params: Mapping[str, str]) -> None: ...

    router.register(bot)   # once, at startup

Why one class over one per route: `ViewStore.dispatch_dynamic_items` iterates *every*
registered pattern and schedules *every* `fullmatch`, so two overlapping templates
double-fire the same click. A single class makes overlap structurally impossible, and
the router resolves which route matched by re-matching each route's own pattern in
registration order (so parameter group names cannot collide between routes either).
`Router.register` rejects a second route whose pattern already matches an earlier
route's own sample id.

Handlers receive the raw `discord.Interaction` plus the parsed parameters. This is a
deliberate asymmetry with `Action`, not an oversight: the *node* is portable (a label
and an opaque id are representable in any frontend) while *dispatch* is inherently
per-frontend, which is why `Router` lives in `sl.discord` rather than beside `Mount`.
Wrapping the interaction in a portable `PressEvent` was considered and rejected — all
five consumers need `interaction.message.id` (the poll a click refers to *is* the
message the button sits on, encoded nowhere), `interaction.guild`, or
`interaction.client.services`, so the portable surface would be a fig leaf every handler
unwraps on its first line. Plan 02's philosophy applies: a typed escape hatch beats fake
portability.

No author lock, no generations, no transactions: routed handlers own their concurrency,
as they do today.

Retired routes: an id whose route is no longer registered matches nothing and Discord
shows "This interaction failed" — the same as deleting a `DynamicItem` class does today.
A catch-all pattern is not worth its cost, because discord.py's dispatch-every-match
behaviour means a catch-all would double-fire on every live route. The documented
migration path is to keep the route registered with a handler that explains the control
is gone.

### Mount interplay

A mounted document may also contain routed actions (a session view embedding a permanent
"edit build" button — `submission/ui/views.py:967` does exactly this today). Their
custom_ids stay stable across generations and dispatch bypasses the mount's funnel
entirely. Verified benign rather than merely asserted: `dispatch_view` calls
`dispatch_dynamic_items` *and* the stored in-memory view, but the stored view finds a
plain `discord.ui.Button` whose `callback` is `Item`'s no-op, and `MountedView` defines
no `interaction_check`, so the second dispatch cannot double-respond. Document the split
explicitly: mount policies (`lock_to`, EXCLUSIVE, generation checks) do not apply to
routed controls in the same message. `_disable_all` (`discord/mount.py:449`) needs no
change — it already handles both plain and wrapped items.

### Migration

Convert the five DynamicItem call sites: keep their handler bodies, delete the class
boilerplate, the `from_custom_id` overrides, the `# pyright: ignore` comments and the
casts; replace the `RawItem` splices with `RoutedAction`/`RoutedButton` nodes. One
happy side effect at `edit:build`: loading the `Build` inside `from_custom_id` (a DB hit
before any authorization, guarded by `assert build is not None`, which crashes on a
deleted build) moves into the handler where it can fail gracefully.

`voting/rendering.py`, `starboard/render.py` and `consent_banner.py` then become
candidates for full semantic authoring (coordinate with plan 04 — this plan lands
between 04's phase A and phase B so those files migrate once). `RawItem` stays for
genuinely native escape hatches, but loses its "only way to have a button on a static
post" role.

`EphemeralBuildEditButton` stays as it is: a session-scoped plain button is tier 1, not
tier 3.

## Verification

- Package tests: `Route` format parsing/validation, id round-trip through `pattern`,
  100-char budget rejection, `:`-in-value rejection; renderer draws routed controls
  without `wire` while still rejecting *bound* controls; codec round-trips a routed
  scene; HTML renderer emits custom ids; `conform` counts routed buttons.
- Router unit tests: alternation template construction, route resolution order, overlap
  rejection at registration, handler receives parsed params.
- Host: migrated files' unit modules under `tests/unit/bot`, `--no-cov`; a manual
  restart test via the `run` skill — click a pre-restart vote card button after reboot.
