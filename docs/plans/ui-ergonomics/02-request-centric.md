# 02 — Request-centric application layer

Agreed 2026-09-02. Redesigns the application-facing half of `squid_ui_discord` — the part a
cog author touches — around one object, the request. Rendering, sessions, message roots and
routing below the facade are untouched.

## Status

Design agreed; prototype pending. The prototype ports `submit.py`/`edit.py`,
`consent.py`+`verify.py` and `operations.py`+`admin.py:235` in a worktree and records the
before/after numbers in [Verification](#verification) before anything lands on the branch.

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
6. **Owner generics erase to `Any` at every helper boundary**; `DiscordUI[OwnerT]` buys nothing
   the bot reads.
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

### Scope is a lifetime, not an owner

`sd.Scope` tracks the roots a cog opened so `close()` can finish them. It has no owner type
parameter and no `respond`; `send`/`edit` stay for the no-source case (scheduled posts,
starboard edits). `DiscordUI` and `DiscordAction` go.

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
- **Scope comes from `Command.binding`.** discord.py rebinds every child of a cog-owned group
  to the cog (`commands.py:1706`) and binds class-body members to the group instance, so
  `binding` is a cog, a group, or `None`. `runtime.scope_for(binding)`: an `sd.Cog` → its
  scope; anything else → the app scope. This deletes `ext/commands._ui_for` and
  `operations._command_request`.
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
- **Keeping `DiscordUI.respond` alongside `req.respond`** "for the one-liner". The one-liner
  is `await (await sd.request(i)).respond(x)` only when the author refuses the decorator; with
  `@sd.command` the request is already in hand.

## Deletions

`action.py`, `DiscordUI.respond/resolve/action`, the owner type parameter, the `acknowledgement`
enum and its `"form"` mode, `Response.spec`/`overrides`, `root_options`, `sd.responder`,
`sd.native`, the `ext` package path, `run_managed_result`'s public surface (it becomes the
implementation of `pending=`), the "place `@sdx.command` directly beneath the native command
decorator" `TypeError`, and in the bot: `operations.py`, the helpers at `consent.py:156-172`,
`_component_error_hook`, `app_ui`.

Small independent cuts that do not wait for the rest: re-export `AdmissionSpec`/`Reject`
from `sd`; make `RouteGroup.define` idempotent (removes `routes/_root.py:_feature_route`); fix
the stale package README and `docs/plans/ui-ergonomics/README.md`.

## Verification

Prototype in a worktree against the three call-site clusters, then record here:

| Cluster | Lines before/after | `cast()` before/after | duck-typed `getattr`/`hasattr` before/after |
|---|---|---|---|
| `submit.py` + `edit.py` (group members, forms, defer) | | | |
| `consent.py` + `verify.py` (child sessions from a click) | | | |
| `operations.py` + `admin.py:235` (pending card) | | | |

Plus: the redundant `audience="personal"` count (16 → 0 expected), and one test per row of
the request table above under `packages/squid-ui-discord/tests/`. Pyrefly must stay at zero
for `squid/`.

## Order

1. `sd.Request` + `sd.request()` memoization, `sd.Scope`; `DiscordUI` kept as a shim.
2. `sd.command`/`hybrid_command`/`Group`/`hybrid_group` with open kwargs and `binding` scope
   resolution; `pending=`/`defer=` on top of `run_managed_result`.
3. `sd.Config(errors=…)`; fold `ext` into `sd`.
4. Port the bot, delete the shims and the bot helpers, update the docs.
