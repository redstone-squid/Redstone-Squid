# 70 — discord.py interop: reachability, delivery gaps, and invocation shape

## Problem

`sl.discord` has no capability gaps worth naming: mount lifecycle, three delivery adapters,
routed controls with a middleware onion, modals, unsent-view adoption, fragment composition,
durable sessions. What is left is four places where a host has to reach *around* the package to
reach discord.py, plus one seam that splits prefix and slash commands into unrelated worlds.

**1. Nothing is reachable from a `discord.Client`.** `squid/bot/app.py:160-179` builds `Reactor`
→ `SessionRegistry(defaults=...)` → `ChallengeRunner` → `DialogPresenter(registry, runner)`, then
writes the presenter *back* into `registry.defaults` because the dependency is circular, then
calls `install_mount_defaults()` a second time for panels that never touch the registry. That
function (`squid/bot/ui.py:407-421`) is a process global with a `global` statement, and its
docstring says exactly why it exists: a challenge presenter "needs the session registry and the
background runner, and both belong to a bot instance", while `create_mount` — the path 21 call
sites use — holds no bot.

`MountDefaults` (plan 43) solved construction. It cannot solve this, because it is a *value*: a
value cannot be found from an `interaction`. The bot minted a global to stand in for a lookup the
package does not offer.

**2. No `Destination` for a message the bot already owns.** Producers are `reply_to`, `send_to`,
`respond_to`, and `testing.delivered_to`. `squid/bot/posts/reconciler.py:141-142` drops to
`handle_for(message).write(...)` — correct, but outside the `Destination` family and passing no
`mode`, so a classic-mode presentation gets a handle built without it.

**3. `send_to` cannot take a real discord.py channel.** `squid/bot/ui.py:295-309` casts to the
structural `Messageable` protocol and carries a docstring explaining that discord.py's overloaded
`send` does not match it. Every library user writes that cast.

**4. Rendering one node reaches into renderer internals.** `squid/bot/ui.py:340-355` builds a
presentation, takes `presentation.layout.children[0]`, and calls `remove_item`. That is the
ownership breach `contribute` exists to prevent — but `contribute` needs a host view, and this
caller has none yet.

**5. `Context` and `Interaction` are two worlds.** `Replyable` and `discord.Interaction` never
meet, and `Opener.of` (`screens.py:37-40`) accepts only an interaction. `squid/bot/consent.py`
pays for it with a `_registry_of` / `_destination` / `_send` triple-dispatch over
`ConsentTarget = Context | Interaction`.

## Why now, and why this is not the rejected facade

Plan 65 closed with "a Context-specific `Screen.reply`, policy presets, and **a runtime facade
remain deferred until real call sites establish one common policy**". The call sites arrived: 21
`create_mount`/`send_component` sites reading a module global, one triple-dispatch in
`consent.py`, and a constructor that has to write one of its own values back into another. This
plan takes the reachability half of that deferral and leaves the policy half deferred.

`90-deferred.md` separately rejects **a separate application-layer package** — `squid-ui` with a
`UIRuntime` composition root, `Projection` objects, and named policy presets. This plan is
outside that entry's reasoning on every ground the entry gives:

- **No policy surface.** `install()` takes a `MountDefaults` and hands back the objects a host
  already constructs. No presets, no class-body policy (rejected by 43), no `Projection`
  (answered by 47's `sl.watch`). Item 5 below explicitly declines to carry visibility policy.
- **No fourth layer, no package.** It lands *inside* `sl.discord`, where plans 24–28
  deliberately moved host helpers. The rejection was of re-splitting that join.
- **It adds what `MountDefaults` structurally cannot.** The entry's argument was that `UIRuntime`
  is "`MountDefaults` plus a host facade". True of construction, false of lookup — and lookup is
  the half the bot could not do without.
- **The pattern is already in the package.** `routing._INSTALLED` is a
  `WeakKeyDictionary[discord.Client, list[Router]]` with a `routers(client)` reader
  (`routing.py:70-78`). Client-keyed state is established practice here.

## Decision

### 1. `sl.discord.install(client)` and `LayoutHost`

New module `discord/host.py`, re-exported from `discord/__init__.py`.

```python
def install[ClientT: discord.Client](
    client: ClientT,
    *,
    defaults: MountDefaults = MountDefaults(),
    bus: TopicBus | None = None,
    profiler: Profiler | None = None,
) -> LayoutHost[ClientT]: ...
```

`install` performs the circular assembly once, internally: construct the `SessionRegistry`, the
`ChallengeRunner`, and the `DialogPresenter(registry, runner)`, then write the presenter into the
registry's defaults and its own. Construct a `Reactor` when a `bus` is given and set it as
`scheduler`. Record the result in a module-level `WeakKeyDictionary` keyed by `client`, refusing a
second installation the way `Router.register` refuses a colliding router (`routing.py:610-617`).

`LayoutHost` exposes `client`, `defaults`, `mounts`, `reactor`, `challenges`, and:

| Member | Contract |
|---|---|
| `mount(component, *, access, **overrides)` | `defaults.mount`; a panel holding a host needs no other object |
| `async run()` | Drives `reactor.run` and `challenges.run` under one task group, started by the host's supervisor |
| `async close()` | Ends it: `mounts.close_all()`, then drop the client entry |
| `classmethod of(source)` | `discord.Client \| discord.Interaction \| Replyable` → the installed host; raises when absent |

`of` is spelled to match `Opener.of`, and raises rather than returning `None` because the bot's
own comment already establishes the case as a programmer error: "a guard that challenges is a
programmer error without one".

`run()` is a convenience, not a requirement. The package still starts nothing on its own
(`README.md:693-698`), and a host wanting per-job health granularity keeps starting
`reactor.run` and `challenges.run` as separate supervised jobs — `squid/bot/app.py:227-228` does
exactly that today, and neither job is in `CRITICAL_BOT_JOBS`, so either shape is correct there.

**Naming.** `install`, not `attach`: `AttachedFragment.attach(view)` (`fragments.py:117`) already
means "attach a rendered region to a host view", and plan 67's rule is that nouns — and by the same
argument verbs on the public surface — owe one meaning per word. The package already says *install*
for putting its machinery on a client: `routing._INSTALLED`, and `Router.register`'s "install this
router's dispatch item on `client`". `test_naming.py` would not have caught this, since
`test_one_public_name_means_one_class` checks classes; it was caught by grep and is recorded so the
next round does not re-propose `attach`.

`close` and `run` are both in `test_naming.py`'s `TERMINATING_VERBS`; only `close` is
in `OBJECT_ENDING_VERBS`, so the pair is legal and the class must not also grow `finish`. Per
CLAUDE.md, `LayoutHost`'s first docstring paragraph names what ends it. `Attachment` was
considered and reads like a `discord.Attachment`; `LayoutRuntime` collides in spirit with
`DurableSessionRuntime`, which persists sessions and is a different thing. Plan 67 rejected a
closed suffix taxonomy, so `Host` argues for itself: it is the object a host holds, and the docs
already use "the host" for this role throughout.

**Dividends**, which are what justify the surface:

- `install_mount_defaults`, `MOUNT_DEFAULTS`, and the `global` statement in `squid/bot/ui.py` are
  deleted; `create_mount` reads `LayoutHost.of(...)`.
- `Screen.open`/`Screen.respond` gain forms omitting `sessions` when the interaction's client
  carries an installation. The explicit form stays for hosts keeping several registries. This
  amends plan 65's boundary rather than replacing it: 65 kept the registry explicit because there
  was no way to find it.
- `squid/bot/app.py:160-179` collapses to one `install()` call plus the topic-bridge wiring, and
  `close()` to one `host.close()`.

### 2. `sl.discord.edit_to(message)`

```python
def edit_to(
    message: discord.Message,
    *,
    files: Sequence[discord.File] = (),
    allowed_mentions: discord.AllowedMentions | None = None,
) -> Destination: ...
```

In `discord/delivery.py`, beside its three siblings and named for them. Writes the presentation to
a message the bot already owns and returns
`DeliveryReceipt(message, handle_for(message, mode=presentation.mode))`. Building the handle
inside the closure is the point: it reads the presentation's mode, which `reconciler.py:141`'s
bare `handle_for(message)` cannot.

`edit_to` and `Mount.adopt_handle` are not competing answers. `edit_to` is a `Destination` — the
way a mount *arrives* on an existing message, so `Mount.send` runs its usual stage → deliver →
commit. `adopt_handle` retains newly established authority for a mount that is already live, and
stays the answer when the handle comes from somewhere else. Consumers:
`squid/bot/posts/reconciler.py:141-142` becomes one line, and the sticky-message and
post-restart-recovery cases gain a supported mount path.

### 3. Typed `send_to` overloads

Add `@overload`s to `delivery.send_to` accepting `discord.abc.Messageable`, keeping the structural
`Messageable` protocol overload so test doubles still work. The single `cast` moves inside the
package, where the docstring explaining discord.py's overload shape belongs.
`squid/bot/ui.py:295-309` is deleted outright. `Member`/`User` need no special case — both are
already `abc.Messageable`.

**`discord.Webhook` is excluded, deliberately.** `Webhook.send` returns `None` unless
`wait=True`, so it cannot satisfy `Destination`'s contract of returning a receipt, and an
overload that silently pins `wait=True` would hide a round trip the caller did not ask for. A host
sending through a webhook passes the structural protocol overload with its own wrapper. Revisit
only if a consumer appears; the bot has none.

### 4. `sl.discord.render_item(node, ...)`

Move `squid/bot/ui.py:340-355` into `discord/composition.py`, beside `render_static` and named for
the family. Inside the package, constructing a presentation and detaching its single child is
legal — the renderer owns the object it built. In the host it is a reach-around.

The docstring keeps the host's existing steer: prefer `contribute`, which measures the host view
and places the region atomically; `render_item` is for a caller assembling the surrounding view
itself and knowing its own budget. Not named `detach` — that is a terminating verb in
`test_naming.py`, and this is a pure function.

### 5. Invocation shape: two narrow additions

- `Opener.of` accepts a `Replyable` as well as a `discord.Interaction`, reading `author.id` and
  `guild.id` duck-typed — the technique `reply_to` already uses when it peeks at
  `ctx.interaction`.
- `sl.discord.deliver_to(target)` dispatches to `respond_to` for an interaction and `reply_to`
  otherwise.

`deliver_to` carries **no visibility policy**. `ephemeral` stays a caller keyword.
`squid/bot/utils/visibility.py`'s `personal()` and `Private(reason)` are host audience policy,
and `90-deferred.md` rejected named policy presets for exactly this reason; this is also the half
of plan 65's deferral that stays deferred.

With `LayoutHost.of(target)`, these collapse `consent.py`'s triple-dispatch, which is the
evidence that the `Context`/`Interaction` split leaks into consumers.

## Rejected alternatives

- **A `discord.ext.commands.Cog` installer.** The idiomatic discord.py spelling, and wrong here:
  `delivery.py` types `Replyable` structurally precisely "so this package keeps out of the
  commands extension". A Cog would make the extension a hard dependency of wiring.
- **`install()` returning the registry instead of a new class.** Loses `reactor`, `challenges`,
  and `run()`, and gives `SessionRegistry` a second job. The registry owns admission; the host
  owns assembly and lifetime.
- **Making `LayoutHost.of` return `None` when nothing is installed.** Every caller would grow the same
  raise, and the failure is a wiring bug, not a runtime condition.
- **A `panel` decorator for `app_commands`.** Would bake actor and locale policy into a
  decorator — the class-body policy surface plan 43 rejected, in a new spelling.

## Verification

This plan is design only; the implementing plan owes:

- Package tests: a second `install` on one client is refused; `LayoutHost.of` resolves from a client, an
  interaction, and a `Replyable` double, and raises otherwise; `close()` finishes every session;
  `edit_to` returns a receipt whose handle carries the presentation's mode; `send_to` typechecks
  against a real `discord.TextChannel` with no cast; `render_item` returns a detached item and
  leaves no partially built view behind.
- Bot migration: `squid/bot/app.py:160-179` and `205`, `squid/bot/ui.py:295-309`, `340-355` and `407-421`,
  `squid/bot/posts/reconciler.py:141-142`, `squid/bot/consent.py`'s dispatch helpers.
- Docs: four new rows in `docs/squid-layouts-architecture.md`'s "Which entry point to use" table,
  and a `README.md` host-integration note that `install` starts nothing until `run()` is
  supervised.
- `just typecheck` compared against the recorded baseline, and `git diff --check`.

## What landed

Implemented 2026-08-25. Two departures from the design above, both about where the
registry is named:

- **`Screen.open` takes the component first and the registry by keyword**, widened to
  `SessionRegistry | HostSource`. A form literally *omitting* `sessions` is impossible for
  `open`: it receives a `Destination` and an `Opener`, and neither carries a client. Passing
  the target you already hold is what collapses `consent.py`'s dispatch, which is the
  dividend the plan was after. `Screen.respond` does default it, from the interaction.
- **`create_mount` grew a required `source`** rather than reading an ambient host. The plan
  said it "reads `LayoutHost.of(...)`" without saying what it looks up from; a module-level
  default would have been the deleted global under a new name. Every call site had a client,
  an interaction or a context in reach, and each panel's `mount()` threads it from its caller.

`deliver_to` also declines a `files` keyword, because `respond_to` has no host-files
parameter and an overload that silently dropped them would be worse than not offering them.
