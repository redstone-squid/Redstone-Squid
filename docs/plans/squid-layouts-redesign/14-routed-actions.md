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

## Design

**Node.** `RoutedAction(route: str, label: TextLike, payload: str = "", tone/emphasis)`
as a semantic node (plus a primitives-level `RoutedButton` for exact placement, e.g. as
a `Section` accessory). Also `RoutedChoices` if migration finds a select-shaped consumer
— audit first, do not build speculatively.

**Custom id scheme.** `rt:{route}:{payload}` with the 100-char budget validated at plan
time (a too-long payload is a planning error, not a Discord 50035). `route` is a stable
registered name; `payload` is an opaque string the handler parses (build ids, etc.).

**Scene.** A `SceneRoutedButton` (or `SceneButton` with a `route:`-namespaced action id
and no binding requirement — pick during implementation; distinct node is cleaner for
the codec). The renderer draws it without `wire`, so `render_static` documents may
carry them; `conform` measures them like any button. Scenes become fully serializable
— routes are strings, unlike `RawItem` closures — and the HTML renderer emits the route
id the same way it already emits action identifiers.

**Dispatch.** One framework-owned `discord.ui.DynamicItem` subclass with template
`rt:(?P<route>[^:]*):(?P<payload>.*)`, registered once by the host
(`sl.discord.Router.register(bot)`). A `Router` maps route names to handlers:

    router = sl.discord.Router()

    @router.route("poll:close")
    async def close_poll(interaction: discord.Interaction, payload: str) -> None: ...

Handlers receive the raw `discord.Interaction` — tier 3 is Discord-native by nature,
consistent with plan 02's philosophy (typed escape hatch over fake portability). No
author lock, no generations, no transactions: routed handlers own their concurrency,
as they do today.

**Mount interplay.** A mounted document may also contain routed actions (a session view
embedding a permanent "edit build" button). Their custom_ids stay stable across
generations, dispatch bypasses the mount's funnel entirely, and `_disable_all` already
handles `DynamicItem` wrappers on finish (`discord/mount.py:449`). Document this
split-brain explicitly: mount policies (lock_to, EXCLUSIVE) do not apply to routed
controls in the same message.

**Migration.** Convert the five DynamicItem call sites: keep their handler bodies,
delete the class boilerplate and casts, replace the `RawItem` splices with
`RoutedAction`/`RoutedButton` nodes. `voting/rendering.py`, `starboard/render.py`, and
`consent_banner.py` then become candidates for full semantic authoring (coordinate with
plan 04 — if 14 lands first, those files migrate once). `RawItem` stays for genuinely
native escape hatches, but loses its "only way to have a button on a static post" role.

## Verification

- Package tests: plan-time custom_id budget enforcement; renderer draws routed
  controls without wire while still rejecting *bound* controls; codec round-trips a
  routed scene; HTML renderer emits route ids; conform counts routed buttons.
- Router unit tests: template dispatch, unknown-route behavior (log + generic notice,
  never a crash — old messages may outlive routes).
- Host: migrated files' unit modules under `tests/unit/bot`, `--no-cov`; a manual
  restart test via the `run` skill — click a pre-restart vote card button after reboot.
