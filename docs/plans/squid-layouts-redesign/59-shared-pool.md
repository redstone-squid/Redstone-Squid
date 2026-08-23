# 59 — Shared pools: explicit keyed lifetime for shared view state

## Problem

[`Shared`](40-shared-state.md) deliberately has no registry. A handle is the state, and an
ordinary owner decides its lifetime by retaining that handle. That remains the precise model,
but a host that wants one handle per application scope has to repeat the same cache around every
namespace:

```python
self._preferences: dict[int, Preferences] = {}

def preferences_for(self, user_id: int) -> Preferences:
    if user_id not in self._preferences:
        self._preferences[user_id] = Preferences(self.bus, user_id)
    return self._preferences[user_id]
```

This code is not application state or persistence. It is a lifetime owner for reactive view
state, but every host must choose its typing, factory, invalidation, and inspection behaviour
again. A heterogeneous `get(Preferences, user_id)` registry would remove the spelling while
introducing two hidden policies: unrelated namespace types would share one untyped cache, and a
per-call factory would make the first caller's dependencies silently win.

## Decision

Add a **strong, type-bound `SharedPool`**. One pool owns one `Shared` namespace type and retains
one canonical handle per hashable scope until that scope is dropped, the pool is cleared, or the
pool itself is released.

```python
preferences = sl.SharedPool(Preferences, bus)

mine = preferences.get(UserScope(user.id))
same = preferences.get(UserScope(user.id))
assert mine is same
```

The pool is an optional keyed lifetime owner; it does not change `Shared`. Constructing and
passing handles directly remains supported, scopes used outside a pool remain allowed to be
mutable or unhashable, and no global pool or singleton is added.

The owner still states the lifetime by where it puts the pool:

```text
bot       -> process lifetime
cog       -> extension lifetime
session   -> session lifetime
request   -> request lifetime
```

## Scope vocabulary

A pool keys on `ScopeT`, and today nothing says what a scope *is*. `Shared[ScopeT]`'s scope is a
diagnostic label — not hashable, not validated, nothing reads it but `__repr__` and `describe`
(`packages/squid-reactive/src/squid_reactive/shared.py`) — so hosts reach for a bare `int`, as
`squid/bot/layout_showcase.py:565` does with `Shared[int]` holding a user id. Nothing then
distinguishes a user id from a guild id, and the pool would inherit that.

The repo already has the vocabulary this wants, one layer over: `UserScope`, `GuildScope`,
`UserGuildScope`, `GlobalScope` and `CustomScope` in
`packages/squid-layouts/src/squid_layouts/discord/sessions.py:24-58`, frozen, hashable, and
already the taxonomy CascadeUI is credited for. It is used for *session* keying only. **Adopt it
as the conventional `ScopeT`.** One scope taxonomy, not two.

Nothing moves between packages to make this true. `SharedPool[ScopeT: Hashable, …]` is generic,
`SessionScope` members are already hashable, and `squid-reactive` stays dependency-free and
Discord-free — the Discord layer supplies a concrete scope, the portable core never learns what
it means.

The ergonomics come from the class statement, not from lookup methods:

```python
class Preferences(sl.Shared[UserGuildScope]):
    theme: str = sl.state("dark")

preferences = sl.SharedPool(Preferences, bus)     # ScopeT inferred from the base
prefs = preferences.get(sl.discord.scopes.user_guild(interaction))
```

`sl.discord.scopes` is the small new surface this plan adds beside the pool: free functions
`user`, `guild`, `user_guild` and `global_` reading a scope off a `discord.Interaction` or a
`commands.Context`, so hosts stop extracting ids by hand. `guild` and `user_guild` raise a named
error outside a guild rather than inventing a scope; the error names the function, because the
mistake it catches is a DM-reachable command that assumed a guild.

`sl.discord.scopes.of(key)` also derives the scope from an existing `SessionKey`, which is what a
panel already holding its session key wants — the point of sharing the taxonomy is that this needs
no conversion.

## Public API

`squid_layouts.runtime.shared` gains `SharedPool` and a public factory alias, both re-exported
from `squid_layouts.runtime` and the root authoring vocabulary:

```python
type SharedFactory[ScopeT, SharedT] = Callable[[TopicBus, ScopeT], SharedT]

class SharedPool[ScopeT: Hashable, SharedT: Shared[ScopeT]]:
    def __init__(
        self,
        namespace: type[SharedT],
        bus: TopicBus,
        *,
        factory: SharedFactory[ScopeT, SharedT] | None = None,
    ) -> None: ...

    def get(self, scope: ScopeT) -> SharedT: ...
    def get_existing(self, scope: ScopeT) -> SharedT | None: ...
    def drop(self, scope: ScopeT) -> SharedT | None: ...
    def clear(self) -> None: ...
    def active(self) -> Mapping[ScopeT, SharedT]: ...
```

The implementation may adjust the internal type-alias spelling to satisfy Pyrefly, but the
public relationship must remain: the namespace's `Shared[ScopeT]`, the lookup scope, factory
argument, and returned handle are one inferred pair. Typing fixtures pin that relationship.

The default factory is equivalent to `lambda bus, scope: namespace(bus, scope)`. A namespace
with extra collaborators fixes them once, when constructing its pool:

```python
searches = sl.SharedPool(
    SearchState,
    bus,
    factory=lambda bus, scope: SearchState(bus, scope, index=index),
)
```

Factories are synchronous. Creating reactive view state does not perform I/O; an async factory
would turn every lookup into an awaitable and duplicate the job of `sl.resource`.

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

A mismatch raises `TypeError` before the value is retained. Equality, not identity, is used for
the scope because dictionary key equality is already what defines canonical lookup.

`drop(scope)` removes and returns the current canonical handle, or returns `None` when the scope
is absent. It does not invalidate or mutate that handle. Existing components may keep using it,
but a later `get(scope)` creates a new canonical **generation**. This split is intentional and
must be stated in the method's docstring; code that cannot tolerate overlapping generations must
coordinate its consumers before dropping.

`clear()` applies the same retirement to every scope and returns nothing. It does not run cleanup
hooks because `Shared` has no lifecycle hook and adding one here would make direct and pooled
handles behave differently.

`active()` returns a read-only snapshot mapping scopes to handles. The snapshot cannot mutate the
pool and does not change while a caller iterates it. `get_existing()` and `active()` never invoke
the factory. No `__getitem__`, iteration, or mapping inheritance is added in v1: lookup remains an
explicit lifetime operation rather than making the pool look like application data.

`SharedPool` has the same concurrency boundary as `Shared` and `TopicBus`: it is a synchronous,
host-owned event-loop object, not a thread-safe cache. Because `get()` contains no await, two
asyncio tasks cannot interleave its miss and insertion path.

## Not included

- No mixed pool keyed by `(namespace type, scope)`.
- No weak mode, TTL, LRU, size limit, or background eviction.
- No async factories or resource loading.
- No persistence, serialization, reducers, or application-database semantics.
- No `user()`, `guild()`, or `user_guild()` convenience methods **on the pool**. A pool is
  single-scope-typed, so once the namespace declares `Shared[UserGuildScope]` the scope kind is
  already fixed by the class statement and `pool.user(id)` would be a second spelling for it —
  one that also silently type-checks against the wrong pool. The scope constructors live in
  `sl.discord.scopes` instead, where they are the only spelling.
- No `pool.at(interaction)`, resolving the scope from the declared `ScopeT` through a
  `classmethod of(source)` protocol. It is the nicest spelling on offer and is **not rejected,
  only gated**: it needs `spikes/59/` to show Pyrefly infers `ScopeT` through a generic
  classmethod-bearing protocol, and the series already has one Pyrefly-driven rejection of a
  construction of exactly this shape ([90](90-deferred.md)'s `Unpack`-on-a-TypeVar entry).
  Ship the free functions; promote `at()` if the spike passes.
- No automatic pool on a bot, registry, mount, or session.
- No guarantee that separately constructed handles with equal diagnostic scopes converge.

These are cache-framework or application-service concerns. A future addition needs repeated
production demand rather than being inferred from the existence of keyed lookup.

## Verification

- Two `get()` calls for an equal scope return the identical handle; unequal scopes do not.
- The default factory receives the pool bus and requested scope.
- A custom factory is called once per canonical generation and receives its captured dependencies.
- Factory exceptions are not cached, and same-key recursive construction fails clearly.
- Wrong namespace type, bus, and scope results are refused without becoming active.
- `get_existing()` distinguishes a miss without constructing anything.
- `drop()` returns the retired handle; a later `get()` creates a distinct generation while the
  retired handle remains usable.
- `clear()` empties the pool, and a previously returned snapshot remains unchanged.
- `active()` is read-only and cannot mutate the pool.
- A hashable mutable-by-convention scope works; an actually unhashable pool key raises the normal
  clear `TypeError`, while direct `Shared(bus, unhashable_scope)` still works.
- Typing fixtures infer concrete handle and scope types, reject the wrong scope type, and type a
  custom factory without `Any` leakage.
- `Shared[UserGuildScope]` infers `SharedPool[UserGuildScope, Preferences]`, and a `UserScope`
  passed to that pool is a type error rather than a runtime miss.
- `sl.discord.scopes` builds each scope from an interaction and from a `commands.Context`;
  `guild`/`user_guild` raise the named error in a DM; `of(SessionKey)` returns the key's own scope.
- `squid/bot/layout_showcase.py:565` migrates off its hand-rolled `setdefault` cache and its
  `Shared[int]` declaration, which is the worked example the docs carry.
- Run the focused shared-pool/shared-state tests, public API tests, `just typecheck`, and
  `git diff --check`.

## Status

Designed, not implemented. Independent of plans 60–62.

Amended 2026-08-23 with the scope vocabulary above, folding in the CascadeUI comparison's
"steal the scoping/keying ergonomics" finding. It arrived as a proposed sibling plan and was
merged here instead: a pool without a scope vocabulary and a scope vocabulary without a pool
are each half of one unbuilt decision, and 59 had not shipped, so there was nothing to amend
*around*. It reopens nothing in [90](90-deferred.md) — there is still no store, no keyed global,
and no singleton; what is keyed is a lifetime owner the host constructs and holds.
