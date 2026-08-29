# 59 — Shared pools: explicit keyed lifetime for shared view state

## Problem

[`Shared`](40-shared-state.md) deliberately has no registry. A
handle is the state, and an ordinary owner decides its lifetime by retaining that handle. That
remains the precise model, but a host that wants one handle per application scope has to repeat the
same cache around every namespace. `squid/bot/layout_showcase.py:728` declares one and `:759` reads
it:

```python
self._appearance: dict[int, Appearance] = {}
...
appearance = self._appearance.setdefault(ctx.author.id, Appearance(self.bot.topic_bus, ctx.author.id))
```

That is the real spelling, and it is worse than the sketch this plan used to quote at itself:
`setdefault` evaluates its default eagerly, so every invocation past the first constructs an
`Appearance`, wires its cells, and throws it away. The discarded handle publishes nothing and the
bug is harmless, but nobody wrote it on purpose — it is what happens when a lifetime policy is
open-coded at a call site.

This code is not application state or persistence. It is a lifetime owner for reactive view state,
but every host must choose its typing, factory, invalidation, and inspection behaviour again. A
heterogeneous `get(Preferences, user_id)` registry would remove the spelling while introducing two
hidden policies: unrelated namespace types would share one untyped cache, and a per-call factory
would make the first caller's dependencies silently win.

## Decision

Add a **strong, type-bound `SharedPool`**. One pool owns one `Shared` namespace type and retains
one canonical handle per hashable scope until that scope is dropped, the pool is cleared, or the
pool itself is released.

```python
preferences = sl.runtime.SharedPool(Preferences, bus)

mine = preferences.get(UserScope(user.id))
same = preferences.get(UserScope(user.id))
assert mine is same
```

The pool is an optional keyed lifetime owner; it does not change `Shared`. Constructing and passing
handles directly remains supported, scopes used outside a pool remain allowed to be mutable or
unhashable, and no global pool or singleton is added.

The owner still states the lifetime by where it puts the pool:

```text
bot       -> process lifetime
cog       -> extension lifetime
session   -> session lifetime
request   -> request lifetime
```

The showcase command this plan migrates is itself the argument that the pool is optional, because it
needs one and its neighbour does not. Two lines apart, `Appearance` is retained in a cog dict while
`Session` is built fresh per invocation under a comment saying why (`layout_showcase.py:760-762` —
*"only the two panels hold it, so it is collected when the second of them finishes. Nothing was
looking at it, and that is the correct lifetime."*). The migration pools `Appearance` and leaves
`Session` exactly as it is.

## Where it lives

`SharedPool` lands in a new `../../../../packages/squid-reactive/src/squid_reactive/pool.py`, is exported from
`squid_reactive`, and is re-exported through `squid_layouts.runtime.shared` and
`squid_layouts.runtime`.

Two facts force this, and the first is why the placement changed:

- **`squid-stores` cannot import `squid_layouts`.** Its dependencies are `["squid-reactive",
  "anyio"]`, and `PersistedPool` — which already keeps a keyed dict of `Shared` handles — lives
  there. A pool implemented in `squid_layouts` could never be the one `PersistedPool` uses, so the
  duplication below would be permanent by construction rather than by oversight.
- **The shim is load-bearing.** `../../../../packages/squid-layouts/src/squid_layouts/runtime/shared.py` is five
  lines of re-export, and `packages/squid-layouts/tests/test_public_api.py:247-269` imports it in a
  subprocess with `discord` **and** `anyio` blocked at the meta-path. `squid_reactive` satisfies
  that today. Anything in `squid-stores` never could.

A new module rather than `shared.py` itself: that file's docstring is an argument that *"there is no
store, no registry and no keyed lookup"*, and putting the keyed lookup in it would undercut the
file. A sibling module keeps the claim true — `Shared` is unchanged, and the pool is something you
have to import.

The spelling throughout this document is `sl.runtime.SharedPool`, matching `sl.runtime.Shared`,
which is what `layout_showcase.py:596` writes. Root `squid_layouts` exports no `Shared` today
(`test_public_api.py:242` pins it to `sl.runtime.__all__`), and promoting the pool to the root
authoring vocabulary while its own base class stays a level down would be incoherent. Promotion is
[58](58-public-api-narrowing.md)'s rule to apply to both names
at once, later, if qualifying them becomes demonstrable noise.

## Relation to `PersistedPool`

[63](63-stores-package.md) closed with *"Unit 2's
`PersistedPool` needs 59's `SharedPool` to exist first"*, then shipped without it.
`packages/squid-stores/src/squid_stores/persisted.py:15-92` is therefore already most of this plan:
the same `[ScopeT, SharedT: Shared[ScopeT]]` spelling, the same `namespace`/`bus`/`factory`
constructor, the same `_handles: dict[ScopeT, SharedT]`, and a `_make` whose three validations are
verbatim the ones specified below. This section is what 59 owes that fact.

**`PersistedPool` composes a `SharedPool`; it does not inherit one.** Inheritance would hand it a
synchronous `get()` that constructs, validates, and publishes a handle with the store never
consulted — so a concurrent `load()` for that scope would return an un-hydrated handle and hydrate
nothing. Overriding `get()` to raise is a Liskov violation wearing a subclass. The lifecycles differ
too (`flush`, `close`, a supervised drain), and so does retirement.

The seam that makes composition work is `get()` split into its two halves, so an owner can put an
`await` between them:

```python
def _create(self, scope: ScopeT) -> SharedT:
    """Build and validate a handle for `scope` without retaining it.

    Runs the factory under the same-scope reentrancy guard and applies the three validations.
    The pool is unchanged whether this returns or raises.
    """

def _adopt(self, scope: ScopeT, handle: SharedT) -> SharedT:
    """Retain `handle` as canonical for `scope`, or return the incumbent if one already exists.

    Never calls the factory. Returns the canonical handle; the caller compares identity to
    learn whether it won.
    """
```

`get()` is then `get_existing(scope)`, else `_adopt(scope, _create(scope))`.

`_adopt` returning the **incumbent** rather than overwriting or raising is the whole design of the
seam. It converts "I lost a race across the await" from a corruption into a value the caller can
branch on — which `PersistedPool` needs, because the loser must not register a second commit
listener writing to the same slot. `load` becomes: `get_existing`, `_create`, `await store.get`,
`restore_state`, `_adopt`, and register the listener **only if** the adopted handle is the one it
created. `_make` is deleted and its three `TypeError`s move into `_create`, reworded from "persisted
namespace factory" to "namespace factory"; no shipped test asserts those strings.

Rejected: a single inverted-control `async def _acquire(scope, prepare)`. It is harder to misuse —
you cannot forget to adopt, and the reentrancy guard spans the await naturally — but it puts an
`async def` into a module whose entire claim is that it performs no I/O, and it turns
`PersistedPool`'s control flow into a callback.

Two things fall out that are worth having:

- `PersistedPool` gains `drop` and `clear`. Today `_handles` grows without bound, because nothing
  ever removes an entry.
- **`Shared` is untouched.** This plan adds no lifecycle hook, which it refuses to do below; a
  commit listener is owned by whoever registered it, and `_remove_commit_listener` already exists at
  `shared.py:153`.

`ScopeT` gains a `Hashable` bound in **both** pools. `PersistedPool`'s is currently unbounded yet
dict-keys `_handles` and `_pending` *and* is passed to `Slot[ScopeT: Hashable, ValueT]`
(`squid_stores/scoped.py:45`) and to `ScopedStore.get`/`put`/`drop`. That is latent unsoundness
rather than deliberate looseness, and closing it breaks nothing: the shipped tests key on `str`.
`Shared[ScopeT = None]` itself stays unbounded — hashability is a property of the dict, so it is
declared where the dict is, and `test_shared_state.py:79-81` pins that a direct
`Shared(bus, unhashable_scope)` keeps working.

## Scope vocabulary

A pool keys on `ScopeT`, and `Shared[ScopeT]`'s scope is a diagnostic label — not hashable, not
validated, nothing reads it but `__repr__` and `describe`
(`packages/squid-reactive/src/squid_reactive/shared.py:48-51`) — so hosts reach for a bare `int`, as
`layout_showcase.py:596` and `:608` do with `Shared[int]` holding a user id. Nothing then
distinguishes a user id from a guild id, and the pool would inherit that.

The repo already has the vocabulary this wants, one layer over, and it has it **twice**:

- `packages/squid-layouts/src/squid_layouts/discord/sessions.py:20-54` — `UserScope`, `GuildScope`,
  `UserGuildScope`, `GlobalScope` and `CustomScope`, frozen and hashable, unioned as `SessionScope`.
  This is the taxonomy CascadeUI is credited for; [90](../../squid-layouts-redesign/90-deferred.md)'s Redux entry is the only
  in-repo rendering of that finding.
- `packages/squid-layouts/src/squid_layouts/discord/screens.py:38-44` — `Scope`, a `StrEnum` of the
  same four kinds, used by [51](51-screens.md)'s `Screen`.

So the earlier draft of this section, which proposed a new `sl.discord.scopes` module of free
functions, was wrong twice over: "one scope taxonomy, not two" was already false, and a third
spelling would not have fixed it. `screens.py:25-35` already has `Opener` with
`Opener.of(interaction)` doing the id extraction, and `Screen._require_guild` (`screens.py:122-127`)
already raises the DM error that module was going to reinvent.

**Adopt `SessionScope` as the conventional `ScopeT`, and reach it through the vocabulary that
exists.** `Opener` gains one constructor per kind -- `user()`, `guild()`, `user_guild()`,
`global_()` -- each returning its exact scope value and sharing one `_require_guild`. `Scope` gains
`of(opener)`, which dispatches to them for a kind chosen at runtime, so `Screen.key`
(`screens.py:65-75`) collapses to `SessionKey(self.name, self.scope.of(opener))`. A host then
writes:

```python
class Preferences(sl.runtime.Shared[UserGuildScope]):
    theme: str = sl.state("dark")

preferences = sl.runtime.SharedPool(Preferences, bus)
prefs = preferences.get(Opener.of(interaction).user_guild())
```

The split is not decoration: `Scope.of` must return the `SessionScope` union, because `Screen`
resolves a declared member, and a union is not assignable to a `Shared[UserGuildScope]` pool. Per
member overloads on `self: Literal[Scope.USER]` would have recovered the exact type and were tried;
Pyrefly 1.2 does not apply them from inside the enum's own body. Putting the constructors on
`Opener` gets the precise types with no overloads at all, and `Opener` was already the extraction
point. So a statically known kind asks the opener, a runtime-chosen one asks the scope, and both
build the same values.

This removes a spelling instead of adding one, reuses the tested guild check, and leaves
`SessionKey`'s five classmethod constructors working. The proposed `scopes.of(SessionKey)` helper
collapses to `key.scope`, which already *is* the scope — which is the actual proof of the claim that
sharing the taxonomy needs no conversion.

The ergonomics still come from the class statement rather than from lookup methods: once the
namespace declares `Shared[UserGuildScope]`, the scope kind is fixed, and `pool.user(id)` would be a
second spelling for it.

This does not reopen [90](../../squid-layouts-redesign/90-deferred.md)'s rejection of class-body operational policy, which killed
Cascade's `instance_scope`/`instance_policy` because *"a class attribute would couple portable
components to Discord session vocabulary"*. The coupled class here is a host's own namespace
subclass, not a portable component, and what it declares is a type parameter the pool keys on, not a
policy knob the framework reads. `Screen` keeps that rule for the thing the rule was about: a screen
is still a value a host may build twice.

Nothing moves between packages to make any of this true. `SharedPool[ScopeT: Hashable, …]` is
generic, `SessionScope` members are already hashable, and `squid-reactive` stays dependency-free and
Discord-free — the Discord layer supplies a concrete scope, the portable core never learns what it
means.

**No new exports.** `test_public_api.py:243-244` asserts that `Opener` and `Scope` are absent from
`sl.discord.__all__`, which is
[58](58-public-api-narrowing.md)'s deliberate narrowing; their
public spelling is the submodule path `sl.discord.screens.Scope`, pinned at `:226-227`. The five
scope values stay reachable as `sl.discord.sessions.UserScope` and friends. Promoting any of them
is 58's rule to apply, not this plan's.

**No `commands.Context` overload.** The earlier draft had the helpers read a scope off an
`Interaction` *or* a `commands.Context`. `Interaction` exposes `.user` and `.guild_id`; `Context`
exposes `.author` and `.guild` and has no `guild_id` at all, so one structural protocol does not
cover both, and importing `discord.ext.commands` here would break the convention
`delivery.py:367-382` states outright — `Replyable` is typed by shape *"so this package keeps out of
the commands extension"*. [65](65-screen-entrypoints.md) already deferred a Context-specific
`Screen.reply` on these grounds. The showcase migration writes its opener explicitly:
`Opener(ctx.author.id, ctx.guild and ctx.guild.id)`.

## Public API

`squid_reactive.pool` defines `SharedPool` and a public factory alias, re-exported from
`squid_reactive`, `squid_layouts.runtime.shared`, and `squid_layouts.runtime`:

```python
type SharedFactory[ScopeT, SharedT] = Callable[[TopicBus, ScopeT], SharedT]

class SharedPool[ScopeT: Hashable, SharedT: Shared[Any]]:
    @overload
    def __init__(self, namespace: Callable[[TopicBus, ScopeT], SharedT], bus: TopicBus,
                 *, factory: None = None) -> None: ...
    @overload
    def __init__(self, namespace: type[SharedT], bus: TopicBus,
                 *, factory: SharedFactory[ScopeT, SharedT]) -> None: ...

    def get(self, scope: ScopeT) -> SharedT: ...
    def get_existing(self, scope: ScopeT) -> SharedT | None: ...
    def drop(self, scope: ScopeT) -> SharedT | None: ...
    def clear(self) -> None: ...
    def active(self) -> Mapping[ScopeT, SharedT]: ...
```

The two overloads and the `Shared[Any]` bound are what the spike chose; the section below records
why the obvious spelling -- `SharedT: Shared[ScopeT]`, `namespace: type[SharedT]` -- cannot be used.
The relationship they carry is the one that matters: the namespace's `Shared[ScopeT]`, the lookup
scope, the factory argument and the returned handle are one inferred pair, pinned by
`../../../../packages/squid-reactive/tests/typing_pool.py`.

Only a namespace *class* is accepted at runtime. The callable spelling exists so a checker can read
the scope off the constructor; a bare function that happens to match the signature is refused at
construction, because the identity check the pool runs on what its factory returns needs a class.

The default factory is equivalent to `lambda bus, scope: namespace(bus, scope)`. A namespace with
extra collaborators fixes them once, when constructing its pool:

```python
def make_search_state(bus: TopicBus, scope: UserGuildScope) -> SearchState:
    return SearchState(bus, scope, index=index)

searches = sl.runtime.SharedPool(SearchState, bus, factory=make_search_state)
```

The factory is spelled as an annotated function rather than a lambda on purpose; see the inference
section, where a bare lambda is the one case that may not infer.

Factories are synchronous. Creating reactive view state does not perform I/O; an async factory would
turn every lookup into an awaitable and duplicate the job of `sl.resource`. Hydration *is* I/O,
which is why `PersistedPool.load` is async and lives in another package rather than widening this
one.

## Lifetime and invalidation

`get(scope)` performs one synchronous lookup. On a miss it calls the configured factory, validates
the result, stores it, and returns it. A factory exception leaves no cache entry. Recursive
construction of the same scope raises `RuntimeError` naming the namespace and scope rather than
recursing or publishing two canonical handles; a factory may construct another scope or use a
different pool.

The constructed value must:

- be an instance of the pool's declared namespace type;
- hold the exact `TopicBus` passed to the pool; and
- have a scope equal to the requested key.

A mismatch raises `TypeError` before the value is retained. Equality, not identity, is used for the
scope because dictionary key equality is already what defines canonical lookup.

`drop(scope)` removes and returns the current canonical handle, or returns `None` when the scope is
absent. It does not invalidate or mutate that handle. Existing components may keep using it, but a
later `get(scope)` creates a new canonical **generation**. This split is intentional and must be
stated in the method's docstring; code that cannot tolerate overlapping generations must coordinate
its consumers before dropping.

`clear()` applies the same retirement to every scope and returns nothing. It does not run cleanup
hooks, because `Shared` has no lifecycle hook and adding one here would make direct and pooled
handles behave differently.

**Retirement is only listener-free for the plain pool.** `PersistedPool` registers a per-handle
commit listener writing to `(slot, scope)` (`persisted.py:60`), so a retired generation that kept
its listener would go on overwriting the new generation's row, and retired state would resurrect on
the next `load`. That is a bug, not a preserved affordance. So `PersistedPool.drop` additionally
detaches the handle's persistence listener: the handle stays fully usable, reactive and readable; it
simply stops writing to a slot it no longer owns. Mechanically that needs the listener retained per
scope, because it is built inline today and `_commit_listeners` is an identity-keyed `set`. It also
makes `PersistedPool.drop` and `clear` **async**, ending in a `flush()` — otherwise a drop followed
by a load can re-read the store before the pending write lands and hydrate the new generation from
pre-drop state. The store is the medium, so "enqueued" is not enough. That asymmetry with the plain
pool's synchronous versions is the same one that already justifies an async `load`. The same sweep
should detach listeners in `close()`, which today leaves them attached, so a commit after close
stages a snapshot onto a dead worker that `flush()` then reports as idle.

`active()` returns a read-only snapshot mapping scopes to handles, **copied on call**:
`MappingProxyType(dict(self._handles))`. A proxy over the live dict would be a view, and the two
properties this plan wants — that a caller may iterate the snapshot while retiring the scopes it
names, and that a snapshot taken before `clear()` still describes what was there — both require the
copy. Wrapping the copy rather than returning a bare `dict` keeps it read-only at runtime as well as
statically. The snapshot holds strong references, which matters given that no weak mode exists.
`PersistedPool.active()` delegates unchanged and stays synchronous: it is inspection and does no
I/O.

`get_existing()` and `active()` never invoke the factory. No `__getitem__`, iteration, or mapping
inheritance is added in v1: lookup remains an explicit lifetime operation rather than making the
pool look like application data.

A namespace that declares no scope is `Shared[None]`, so `SharedPool(Anonymous, bus)` is a legal
one-entry pool keyed on `None`. It stays legal — policing it would contradict "nothing is required
of scope" — and the docstring says a `Shared[None]` does not need a pool.

`SharedPool` has the same concurrency boundary as `Shared` and `TopicBus`: it is a synchronous,
host-owned event-loop object, not a thread-safe cache. Because `get()` contains no await, two
asyncio tasks cannot interleave its miss and insertion path. `_create`/`_adopt` exist precisely
because an owner that *does* need an await between them has to handle the race, and `_adopt` returns
the incumbent so it can.

## Type inference, and what the spike measured

This was the plan's real risk, and the earlier draft had it backwards: it gated a convenience method
behind a spike while treating the headline ergonomics as settled.

In `SharedPool(Preferences, bus)`, the solver gets one informative argument -- `Preferences` against
`namespace: type[SharedT]` -- yielding `SharedT = Preferences`. **`ScopeT` occurs in no parameter
type at all**, only inside the bound `SharedT: Shared[ScopeT]`, so recovering it would need the
checker to solve *from* a bound rather than verify it afterwards.

`../../squid-layouts-redesign/spikes/59` ran it against Pyrefly 1.2.0. Two findings, and the
first was not the question asked:

1. **Pyrefly rejects a bound that references a sibling type parameter, full stop** --
   `Type variable bounds and constraints must be concrete`. So `SharedT: Shared[ScopeT]` could never
   ship regardless of inference. The already-shipped `PersistedPool` was spelled exactly that way
   and emitted this error plus three consequential ones (`SharedT` degraded to `object`, so
   `created.bus`, `created.scope` and `created._add_commit_listener` all failed
   `missing-attribute`). Nobody had noticed, because the tree is not at zero and its tests were
   not being collected.
2. **The original signature landed on the silent outcome.** It inferred `SharedPool[Unknown,
   Preferences]`, and `pool.get(UserScope(1))` on a `UserGuildScope` pool produced **no diagnostic
   at all** -- precisely the failure this plan set out to prevent, where typed keying degrades into
   a runtime cache miss.

What ships instead types `namespace` as the constructor it already is. `type[Preferences]` is
assignable to `Callable[[TopicBus, UserGuildScope], Preferences]`, so `ScopeT` is solved from a
callback *parameter* position -- ordinary contravariant inference -- and the bound weakens to
`Shared[Any]`, which still refuses a class that is not a namespace at all. Measured: the pool infers
whole (`SharedPool[UserGuildScope, Preferences]`), the wrong scope is a `bad-argument-type` error,
`Shared`'s PEP 696 `ScopeT = None` default participates (`SharedPool[None, Anonymous]`), and
`get_existing`/`drop`/`active` all follow.

One cost, documented rather than discovered: **a bare lambda factory does not infer.** A lambda
takes its parameter types from the expected type, which still holds the unsolved scope, so it lands
on `Unknown`. The spelling is an annotated function, which is why the example above names one.
Explicit parameterisation remains the escape hatch.

`../../../../packages/squid-reactive/tests/typing_pool.py` pins all of it, including the wrong-scope negative --
if that suppression ever goes unused, inference has regressed to `Any` and the pin has stopped
meaning anything.

## Not included

- No mixed pool keyed by `(namespace type, scope)`.
- No weak mode, TTL, LRU, size limit, or background eviction.
- No async factories or resource loading in `SharedPool`. Hydration is `PersistedPool`'s job.
- No persistence, serialization, reducers, or application-database semantics.
- No lifecycle or dispose hook on `Shared`. The pool needs none: a commit listener belongs to
  whoever registered it, and `_remove_commit_listener` already exists.
- No `user()`, `guild()`, or `user_guild()` convenience methods **on the pool**. A pool is
  single-scope-typed, so once the namespace declares `Shared[UserGuildScope]` the scope kind is
  already fixed by the class statement, and `pool.user(id)` would be a second spelling for it — one
  that also silently type-checks against the wrong pool.
- No `sl.discord.scopes` module. Superseded within this plan: `Scope.of(Opener.of(interaction))` is
  the spelling, and adding free functions beside it would have made three taxonomies where the
  complaint was that there were two.
- No new names in `sl.discord.__all__` or the root authoring vocabulary. Both are
  [58](58-public-api-narrowing.md)'s to decide.
- No `pool.at(interaction)`, resolving the scope from the declared `ScopeT` through a `classmethod
  of(source)` protocol. It was the nicest spelling on offer and was gated on the spike's first
  question passing as written. It did not: `ScopeT` now comes from a constructor parameter rather
  than from `SharedT`'s bound, and `at()` would need exactly the bound-directed solving that
  finding 1 rules out. Deferred on a measured basis rather than an assumed one, which is the same
  footing as [90](../../squid-layouts-redesign/90-deferred.md)'s neighbouring `Unpack`-on-a-TypeVar rejection.
- No automatic pool on a bot, registry, mount, or session.
- No guarantee that separately constructed handles with equal diagnostic scopes converge.

These are cache-framework or application-service concerns. A future addition needs repeated
production demand rather than being inferred from the existence of keyed lookup.

## Verification

The spike lands first and its README records the measurement; the signature it selects is what the
rest of this list assumes.

- Two `get()` calls for an equal scope return the identical handle; unequal scopes do not.
- The default factory receives the pool bus and requested scope, and is not invoked on a hit —
  including for the `setdefault` shape it replaces, which constructed one per call.
- A custom factory is called once per canonical generation and receives its captured dependencies.
- Factory exceptions are not cached, and same-key recursive construction fails clearly.
- Wrong namespace type, bus, and scope results are refused without becoming active.
- `get_existing()` distinguishes a miss without constructing anything.
- `drop()` returns the retired handle; a later `get()` creates a distinct generation while the
  retired handle remains usable.
- `clear()` empties the pool, and a previously returned `active()` snapshot is unchanged by it.
- `active()` is read-only, cannot mutate the pool, and can be iterated while the scopes it names are
  dropped.
- A hashable mutable-by-convention scope works; an actually unhashable pool key raises the normal
  clear `TypeError`, while direct `Shared(bus, unhashable_scope)` still works.
- Typing fixtures infer concrete handle and scope types, reject the wrong scope type, and type a
  custom factory without `Any` leakage. The pool type is asserted whole, so an inferred
  `SharedPool[Any, Preferences]` fails the fixture rather than passing it.
- `PersistedPool` uses `SharedPool` rather than its own dict: `load` still hydrates before the
  handle becomes canonical, a losing racer registers no second listener, and `drop` leaves the
  retired handle usable but no longer writing to its slot. Its `_make` is gone.
- `squid_layouts.runtime.shared` still imports with `discord` and `anyio` blocked.
- `Scope.of(opener)` returns each scope value; `Screen.key` is unchanged in behaviour and a
  guild-scoped opener without a guild still raises; `SessionKey.scope` needs no conversion to reach
  a pool.
- `layout_showcase.py` migrates off its `setdefault` cache and its `Shared[int]` declarations, and
  leaves `Session` unpooled, which is the worked example the docs carry.

Then the focused shared-pool, shared-state, persisted-pool, screens and public API tests, plus
`just typecheck` and `git diff --check`, introducing no findings beyond the recorded baseline.

**Recorded baselines**, so a later reader can tell what was already broken. Pyrefly: 287 errors
before and after, with none in any file this touched -- and four fewer in `persisted.py`, which the
dependent bound had been failing. `../../../../packages/squid-layouts/tests`: seven failures before and after,
unchanged. `../../../../tests/unit/bot/test_layout_showcase.py`: one, unchanged.

**Two pre-existing defects found on the way, one fixed and one not.** Fixed:
`../../../../packages/squid-stores/tests` was absent from `[tool.pytest.ini_options] testpaths`
(`pyproject.toml:380-385`) and `squid_stores` from `--cov`, so every `PersistedPool` assertion
above would have been vacuous; that entry lands first, in its own commit. Not fixed: a bare
`pytest` run cannot collect at all, because `../../../../packages/squid-reactive/tests` and
`../../../../packages/squid-layouts/tests` each define `test_operations.py`,
`test_resources.py` and `test_topics.py` and neither is a package. Both were already in `testpaths`,
so this predates the work here. `--import-mode=importlib` resolves the collision but fails eight
otherwise-passing tests, so it is left alone and recorded rather than half-fixed. The suites are run
one directory at a time until someone takes it on.

## Status

**Shipped 2026-08-24.** `SharedPool` lives in `squid_reactive.pool`, `PersistedPool` composes one
across the `_create`/`_adopt` seam and gained `drop`/`clear`/`get_existing`/`active`, `Opener`
gained the four scope constructors, `Screen.key` collapsed to one line, and the showcase pools
`Appearance` while leaving `Session` alone. `../../squid-layouts-redesign/spikes/59` chose the signature and is kept as the
record of why the obvious one is unavailable.

Two things the implementation changed from this design, both recorded above rather than quietly:
the class-statement spelling (`SharedT: Shared[ScopeT]` is rejected by Pyrefly, so the constructor
overloads carry the relationship instead), and where the precise scope constructors live (`Opener`,
not `Scope`, because per-member overloads on an enum do not resolve). Closing `PersistedPool`'s
listener leak on `close()` was not in the design and was fixed because the drop work exposed it.

Amended 2026-08-23 with a scope vocabulary, folding in the CascadeUI comparison's "steal the
scoping/keying ergonomics" finding — a pool without a scope vocabulary and a scope vocabulary
without a pool being two halves of one unbuilt decision. That comparison is not a document in this
repo; [90](../../squid-layouts-redesign/90-deferred.md)'s Redux entry is the only in-repo rendering of the finding.

Rewritten 2026-08-24, because the plan had gone stale in a way that would have misled an
implementer. [63](63-stores-package.md) shipped `PersistedPool`
without waiting for this, so most of the machinery designed here already exists one package over and
this plan is now partly a consolidation. That moved `SharedPool` from `squid_layouts` to
`squid_reactive` — `squid-stores` cannot import `squid_layouts`, so the old placement made the
duplication permanent — and it added the section describing the split. The scope half was rewritten
in the other direction: the proposed `sl.discord.scopes` module was deleted in favour of extracting
`Scope.of` from `Screen.key`, because `Opener`, `Scope` and the guild check it was going to add
already existed in `screens.py`. The spike gate moved from `pool.at()` to the inference the plan
depends on. No longer independent: 63 is a real dependency, and 61 and 62 have shipped.

It reopens nothing in [90](../../squid-layouts-redesign/90-deferred.md) — there is still no store, no keyed global, and no
singleton; what is keyed is a lifetime owner the host constructs and holds, and the class-body
rejection stands for the thing it was about.
