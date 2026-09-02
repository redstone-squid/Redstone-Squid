# 02 — Request-centric application layer

Agreed 2026-09-02. Redesigns the application-facing half of `squid_ui_discord` — the part a
cog author touches — around one object, the request. Rendering, sessions, message roots and
routing below the facade are untouched.

## Status

Prototyped on `cleanup/surface-audit` (commits `bd5ba7a8`..`cf697211`, 2026-09-02): the
package half and all three call-site clusters are ported, plus `give_redstoner.py` and the
`ext.Cog` imports. Numbers and findings are in [Verification](#verification). Still open:
the deletions list below (the `DiscordUI` shim, `ext`, `run_managed_result`'s public name,
`app_ui`) and the docs.

## What is wrong

The audit covered every application entry point (`DiscordUI`, `DiscordRequest`,
`DiscordAction`, `Screen`, `ResponseSpec`/`Response`, `ext.Cog/command/context_menu/autocomplete`,
`ext.testing`, `run_managed_result`) and every bot call site. Counts are from the branch at
`48742261`.

1. **The request does not travel, so the bot re-derives it.** `DiscordRequest` holds the
   localization, user, guild and acknowledgement ledger, but nothing memoizes it on the
   source, so helpers that receive a raw interaction resolve again or rebuild the pieces by
   hand: `consent.py:156-172` (`_is_context/_ui/_user_of`), `operations.py:25-37` (a four-way
   owner probe), `errors.py:327/356/442` (resolve for localization, then the command resolves
   again). The library itself duck-types the source in six places (`runtime._candidates`,
   `request.destination`, `session_specs.OpenContext.of`, `ext.commands._ui_for`, …).
2. **Three ways to respond, four ways out of an action.** `DiscordUI.respond`,
   `DiscordRequest.respond` and `DiscordAction.respond` carry the same overloads;
   `sd.responder(event)`, `sd.native(event)`, `ui.action(event)` and the portable
   `ActionEvent` methods all answer a click. `DiscordAction` has zero users.
3. **The acknowledgement ledger is hidden.** 16 of 48 `respond` sites pass
   `audience="personal"` after `defer("private")` because the caller cannot see that the
   request already knows; four sites drop to raw discord.py to get out.
4. **Six policy layers.** Runtime defaults → scope defaults → `Screen` class vars → `spec=` →
   keyword overrides → `root_options`, with `TypeError`s policing overlap between layers.
   Screens use `session` 19×, `audience` 3×, `access` 1×, `root_options` 1× (for `chrome`).
5. **The slow-command shape lives in the bot.** "Acknowledge, show a pending card, replace it
   with the result or a rendered error" is `operations.py` (210 lines, five casts), used
   twice. The facade offers `run_managed_result` below it, used once.
6. **Helpers that accept several source shapes cast their way through**; `consent.py:162-176`
   and `operations.py:25-29` re-probe interaction-vs-context on every call. (An earlier draft
   blamed `DiscordUI[OwnerT]` for these casts; it does not cause them, see below.)
7. **Entry-point churn.** `Invocation` (retired in `ff92dafa`), then `DiscordUI.resolve`, then
   `ext.command`; `@sdx.command/context_menu/autocomplete` have zero bot users; README and
   `docs/plans/ui-ergonomics/README.md` still describe `Invocation.reply` and `screen.show`.

## Decision

### One request

`sd.Request` is the only application object. It is created once per source and memoized on
it (`interaction.extras` for interactions; a weak table for `Context`, `Message` and
component events), so every layer that receives the source gets the same request:

```python
req = await sd.request(interaction)          # anywhere: resolves or returns the memoized one
req = sd.request(event)                      # component handlers: sync, the event carries it
```

`Request` owns everything the old three surfaces shared:

| Need | Was | Is |
|---|---|---|
| answer with content | `ui.respond(src, …)`, `request.respond`, `action.respond` | `req.respond(content, audience=…, …)` |
| acknowledge | `request.defer("private")` + `audience="personal"` later | `await req.defer("private")`; `respond` reads the ledger; `req.audience` is readable |
| open a form | `request.open_form` / `event.present_form` | `req.form(...)` |
| reach the click | `sd.responder(event).message_root`, `sd.native(event)` | `req.root`, `req.session`, `req.interaction` |
| the raw source | `request.source` + `hasattr` probes | `req.interaction` / `req.context` (typed, one is `None`) |
| who/where | re-derived from the interaction | `req.user`, `req.guild`, `req.localization`, `req.runtime` |

Policy collapses to two layers: install-time defaults (`sd.Config`) and the call. `Response`
becomes `Response(content, audience=…, access=…, timeout=…)` — no `spec` plus `overrides`.
`Screen` keeps its class-body policy (it is Discord-specific and lives in this package, so the
portable-component objection in `90-deferred.md` does not apply) minus `root_options`; `chrome`
becomes a `Config` value.

### Scope is a lifetime

`sd.Scope[OwnerT = Any]` tracks the roots a cog opened so `close()` can finish them. It has
no `respond`; `send`/`edit` stay for the no-source case (scheduled posts, starboard edits).
`DiscordUI` and `DiscordAction` go.

The owner type parameter stays, on `Scope`, `Request` and `Screen`, defaulted to `Any` (PEP
696) so the bare `sd.Screen` every bot screen uses today remains legal. It is precise where
the entry point knows the owner — `@sd.command` passes `args[0]`, which is `Command.binding`,
so the handler gets `Request[Cog]` and `Screen[Cog].opening.owner.db` type-checks — and `Any`
where it cannot: a click resolves its scope from the root at runtime. The casts in finding 6
come from helpers probing the source shape, which `sd.request()` does once; they were never
the generic's fault.

### `sd.command` absorbs `app_commands.command`

```python
@sd.command(name="submit", description="…", pending="Preparing…", defer="private")
async def submit(self, req: sd.Request, build_id: int) -> None: ...
```

- Builds the outward wrapper (request in the native slot, `__signature__` rewritten for
  discord.py's parameter scan), then delegates to `app_commands.command(**native)` and returns
  the real `Command`. Every discord.py decorator keeps working in either order: function
  stamps (`describe`, `rename`, `choices`) ride `functools.wraps`; command-targeting ones
  (`guild_only`, `default_permissions`, `checks`, `autocomplete`) act on the returned object.
  Cog scanning, `Group` class bodies, `tree.add_command` and `group.add_command` all see a
  native `Command`.
- Native kwargs are typed with an **open** `TypedDict` so future discord.py keywords pass
  through and still type-check:

  ```python
  class NativeCommandKwargs(TypedDict, total=False, extra_items=object):   # PEP 728
      name: str | locale_str
      description: str | locale_str
      nsfw: bool
      auto_locale_strings: bool
      extras: dict[Any, Any]

  def command(*, pending=…, defer=…, **native: Unpack[NativeCommandKwargs]) -> …
  ```

  Verified under the project's pyrefly config: `name=3` is rejected, `future_kwarg=1` is
  accepted, `reveal_type(native)` is the TypedDict. `extra_items` needs
  `typing_extensions.TypedDict` until 3.15.
- `sd.hybrid_command` mirrors `commands.hybrid_command`; prefix-only commands stay native and
  call `await sd.request(ctx)` themselves. `sd.context_menu` already absorbs the native
  constructor and keeps doing so.
- `pending=` and `defer=` absorb `operations.py`: acknowledge, post the pending card, replace
  it with the return value (`present_return` protocol) or the rendered error. `sd.Config`
  carries `errors=ErrorPolicy(render=…, observe=…)` as the one error home, replacing the
  `on_error` hook on `MessageRootDefaults` and the bot's `_component_error_hook`.

### Groups

`sd.Group` is an `app_commands.Group` whose registrar is the squid decorator, and which
carries policy defaults its members inherit:

```python
# Instance form — the bot's mixin-cog pattern, unchanged in shape.
build = sd.Group(name="build", description="…", defer="private")

@build.command(name="submit", pending="Preparing…")
async def submit(self, req: sd.Request, …): ...

# Class-body form.
class Build(sd.Group, name="build", description="…"):
    defer = "private"

    @sd.command(name="browse")
    async def browse(self, req: sd.Request, …): ...

# Hybrid mirror.
layout = sd.hybrid_group(name="layout")
```

- **No `parent=`.** Membership is what discord.py already records: `group.command()` sets
  `parent`; a class-body member is collected by `Group.__init_subclass__` from `cls.__dict__`
  *because* `@sd.command` returns a real `Command` (`app_commands/commands.py:1507`). A wrapper
  type would be invisible there — the second reason absorption must return the native object.
- **Policy resolves at invocation.** The wrapper walks `interaction.command.parent` and
  overlays each `sd.Group`'s defaults under the command's own. This is what lets the
  class-body form work (the decorator cannot see its group) and gives nested groups for free.
- **Scope comes from `Command.binding`.** discord.py binds instance-form members to the cog
  and class-body members to the group instance, keeping that through the cog copy
  (`commands.py:743`), so `binding` is a cog, a group, or `None`. `runtime.scope_for(binding)`:
  an `sd.Cog` → its scope; anything else → the app scope. This deletes `ext/commands._ui_for`
  and `operations._command_request`.
- A group is **not** a scope owner. Roots opened from a member belong to the binding cog or
  the app, so there is no third lifetime object.
- `sd.hybrid_group` returns `sd.HybridGroup(commands.HybridGroup)` with the same registrar
  producing `HybridCommand`s; the request resolves from either `Interaction` or `Context`.

### The `ext` namespace folds into `sd`

`Cog`, `Group`, `command`, `hybrid_command`, `hybrid_group`, `context_menu`, `autocomplete`
and the testing helpers are the application API, not an extension of it.

## Rejected

- **B. Interaction-native functions, no request object** (`sd.respond(interaction, …)`,
  `sd.defer(interaction)`). Cheap to read, but the ledger and localization then live in a side
  table keyed by interaction anyway — the request object with a worse name. Rejected.
- **C. Integration-first `sd.CommandTree`** that injects the request at the tree. Viable as an
  add-on for the double-resolve in `errors.py:419`; not the primary surface because prefix
  commands and component events never pass through the tree.
- **`parent=` passthrough on `sd.command`** for group members. Duplicates what discord.py
  records and cannot serve the class-body form.
- **A squid-typed command wrapper** returned by `sd.command`. Breaks cog scanning, `Group`
  class bodies and every command-targeting discord.py decorator.
- **Groups as scope owners.** Adds a lifetime object nobody closes.
- **Dropping the owner type parameter.** Proposed in the first draft; the casts it was meant
  to remove come from source-shape probing, not from `OwnerT`. A defaulted parameter costs
  nothing at the bare form and types `opening.owner` at the decorated one.
- **Keeping `DiscordUI.respond` alongside `req.respond`** "for the one-liner". The one-liner
  is `await (await sd.request(i)).respond(x)` only when the author refuses the decorator; with
  `@sd.command` the request is already in hand.

## Deletions

`action.py`, `DiscordUI.respond/resolve/action`, the `acknowledgement` enum and its `"form"` mode, `Response.spec`/`overrides`, `root_options`, `sd.responder`,
`sd.native`, the `ext` package path, `run_managed_result`'s public surface (it becomes the
implementation of `pending=`), the "place `@sdx.command` directly beneath the native command
decorator" `TypeError`, and in the bot: `operations.py`, the helpers at `consent.py:156-172`,
`_component_error_hook`, `app_ui`.

Small independent cuts that do not wait for the rest: re-export `AdmissionSpec`/`Reject`
from `sd`; make `RouteGroup.define` idempotent (removes `routes/_root.py:_feature_route`); fix
the stale package README and `docs/plans/ui-ergonomics/README.md`.

## Verification

Measured from `f328050e` (before) to `cf697211` (after). Lines are `wc -l`; casts exclude
the `should_remove_reaction_on_cast` false match.

| Cluster | Lines before/after | `cast()` before/after | duck-typed `getattr`/`hasattr` before/after |
|---|---|---|---|
| `submit.py` + `edit.py` (+ `groups.py`, `ui/opening.py`) | 617 → 559 | 0 → 1 | 0 → 0 |
| `consent.py` + `verify.py` | 623 → 576 | 2 → 0 | 1 → 0 |
| `operations.py` + `admin.py` + `voting/vote.py` | 819 → 550 | 6 → 1 | 3 → 0 |

The two casts that remain narrow `request.client` to `RedstoneSquid` (`opening.py:23`, a
shared helper typed `Request[Any]`) and a channel to `GuildMessageable` (`vote.py:214`);
neither is about the request shape. `audience="personal"` went 25 → 14: the eleven that
followed a private defer (nine in `submit.py`/`edit.py`, two in the vote-to-delete flow) are
gone, and each survivor precedes a reply with no defer, where the audience is a real choice.
The request table has a test per row in `packages/squid-ui-discord/tests/test_request.py` and
`test_commands.py`. Pyrefly reports zero errors for every ported file.

Found while porting:

- **Defer-then-re-resolve bug, in production.** `/build submit` deferred privately, then
  built a second `DiscordUI` request that did not know about the defer, so the workspace went
  out as a follow-up and the "thinking" placeholder never resolved. `edit.py:129` →
  `opening.py:73` had the same shape behind the Edit Build menu. Memoizing the request on
  `interaction.extras` fixes both; `tests/unit/bot/submission/test_submit_command.py` and the
  tightened `test_build_edit_command.py` pin it.
- **`BuildEditCommands.edit_build` is dead.** Unregistered since `26002d83`; kept and marked,
  not ported to a command.
- **`ErrorReportScreen.root_options` was never read**, so the error browser shipped with the
  default chrome. Now `Screen.chrome` (`1a85eab9`).
- **`SendDestination` rejected real channels.** discord.py's overloaded `send` does not satisfy
  the structural `Messageable`, which nothing noticed because `Scope.send` had no callers.
  Widened to the same union as `send_to`.
- **Pending-card behaviour narrowed.** `managed_result` carried a 900 s timeout and
  `dismiss_on_success`; `pending=` posts the card and replaces it with the result or the error
  policy's rendering, nothing else. `raise-error` is the only user and does not miss them.
- **Vote-to-delete simplified.** The reconciler used to adopt the pending card; now the menu
  sends a placeholder through `Scope.send`, hands the delivered message to
  `start_delete_log_vote`, and deletes it if opening the vote fails.
- **Middleware and error audience.** Route middleware re-homes onto the group (`OwnerGuildOnly`
  unchanged). The error handler answers `audience="personal"` only when nothing was deferred;
  after a defer it follows the ledger, because a public "thinking" placeholder can only be
  completed publicly (`errors.py:_error_responder`).
- `sd.testing.invoke_context_menu(owner, method, interaction, target)` runs a
  `@sd.context_menu` method through its registered dispatch; `sdx.testing` is retained until
  `ext` is deleted. `permissions.allows/enforce` accept a transitional `Caller` union of
  `Request` and the native sources until the last native caller is gone.
- Pre-existing, not from this work: `test_ui_ownership.py::test_localized_ui_literals_use_template_strings`
  fails on the branch, and `tests/architecture/test_naming.py` needs `squid_ui_slack`
  installed to collect.

## Order

1. `sd.Request` + `sd.request()` memoization, `sd.Scope`; `DiscordUI` kept as a shim.
2. `sd.command`/`hybrid_command`/`Group`/`hybrid_group` with open kwargs and `binding` scope
   resolution; `pending=`/`defer=` on top of `run_managed_result`.
3. `sd.Config(errors=…)`; fold `ext` into `sd`.
4. Port the bot, delete the shims and the bot helpers, update the docs.
