# Spike: `ScopeT` inference for [59](../../../completed/squid-ui-redesign/59-shared-pool.md)'s `SharedPool`

Evidence, not a staging area — nothing here is meant to be promoted into the package.

```
uv run --locked pyrefly check docs/plans/squid-ui-redesign/spikes/59/inference.py
```

`docs/` is outside `[tool.pyrefly] project-includes` (`pyproject.toml:365`) and ruff excludes
`docs/plans/**/spikes/` (`:230`), so this file is only ever checked by hand. Measured against
**Pyrefly 1.2.0**, the same version [90](../../90-deferred.md) pins for its `Unpack` rejection.

## The question

`SharedPool(Preferences, bus)` must solve `ScopeT = UserGuildScope` from `Preferences`' base
`Shared[UserGuildScope]`. In the plan's original signature `ScopeT` occurs in **no parameter type** —
only inside the bound `SharedT: Shared[ScopeT]` — so recovering it needs bound-directed solving.

## What decided it

Two findings, one of them not the question that was asked.

**1. Pyrefly 1.2 rejects a bound that references a sibling type parameter, full stop.**

```
ERROR Type variable bounds and constraints must be concrete [invalid-annotation]
57 | class PoolA[ScopeT: Hashable, SharedT: Shared[ScopeT]]:
   |                                        ^^^^^^^^^^^^^^
```

It fires on *any* variant spelled that way, so `SharedT: Shared[ScopeT]` cannot ship regardless of
inference. **The already-shipped `PersistedPool` uses exactly this spelling** and therefore emits
this error today, plus three consequential ones — `SharedT` degrades to `object`, so
`created.bus`, `created.scope` and `created._add_commit_listener` all fail `missing-attribute`
(`pyrefly check packages/squid-storage/src/squid_storage/persisted.py` → 4 errors). Nobody noticed
because the tree is not at zero and `packages/squid-storage/tests` is not in `testpaths`.

**2. The plan's original signature lands on outcome 3 — the silent one.**

```
INFO revealed type: PoolA[Unknown, Preferences]
ERROR assert_type(PoolA[Unknown, Preferences], PoolA[UserGuildScope, Preferences]) failed
```

and, decisively, `variant_a_negative` — `pool.get(UserScope(1))` on a `UserGuildScope` pool —
produced **no diagnostic at all**. That is precisely the failure the plan designed against: typed
keying that type-checks a wrong scope and fails at runtime as a cache miss.

## Results

| Variant | Spelling | `PoolX(Preferences, bus)` | Wrong scope | Annotated factory | Bare lambda |
|---|---|---|---|---|---|
| A | `SharedT: Shared[ScopeT]`, `namespace: type[SharedT]` | `[Unknown, Preferences]` ✗ | **silent** ✗ | `[UserGuildScope, SearchState]` ✓ | `[Unknown, …]` ✗ |
| B | A + overloaded callable `namespace` | `[UserGuildScope, Preferences]` ✓ | errors ✓ | ✓ | `[Unknown, …]` ✗ |
| C | B with `SharedT: Shared[Any]` | `[UserGuildScope, Preferences]` ✓ | errors ✓ | ✓ | `[Unknown, …]` ✗ |

A and B both also emit the `invalid-annotation` above; **C does not**.

Additional measurements:

- **Variance probe** — `Shared[UserGuildScope]` is *not* assignable to `Shared[Hashable]`
  (`bad-assignment`), because `Shared.scope` is a plain mutable attribute and `ScopeT` is therefore
  invariant. It *is* assignable to `Shared[Any]`. This is why a bound fallback goes silent rather
  than loud, and why `Shared[Any]` is the only workable bound.
- **PEP 696 default participates** — `PoolC(Anonymous, bus)` where `class Anonymous(Shared)` reveals
  `PoolC[None, Anonymous]`, so an unscoped namespace types correctly rather than as `Unknown`.
- **`Shared[Any]` still discriminates** — a class that is not a namespace at all is rejected:
  `NotANamespace is not assignable to upper bound Shared[Any] of type variable SharedT`
  (`bad-specialization`). The weaker bound loses nothing that was actually being enforced.
- **`get_existing` and `active` follow** — `Preferences | None` and
  `Mapping[UserGuildScope, Preferences]` both assert clean under C.

## Decision

**Ship variant C.**

```python
class SharedPool[ScopeT: Hashable, SharedT: Shared[Any]]:
    @overload
    def __init__(self, namespace: Callable[[TopicBus, ScopeT], SharedT], bus: TopicBus,
                 *, factory: None = None) -> None: ...
    @overload
    def __init__(self, namespace: type[SharedT], bus: TopicBus,
                 *, factory: Callable[[TopicBus, ScopeT], SharedT]) -> None: ...
```

`ScopeT` is solved from a callback *parameter* position — ordinary contravariant inference — rather
than from a bound. The relationship the plan requires is carried by the overloads instead of by
`SharedT: Shared[ScopeT]`, which was decoration that Pyrefly will not accept anyway.

Two consequences the plan takes on:

- A **bare lambda factory does not infer** (`PoolC[Unknown, SearchState]`), because a lambda takes
  its parameter types from the expected type, which still contains the unsolved `ScopeT`. The
  documented spelling is an annotated `def`, which infers correctly. Explicit parameterization
  remains available as the escape hatch.
- **`PersistedPool` must lose its dependent bound too.** That is not scope creep; it fixes four
  live Pyrefly errors in shipped code, and it is the same edit.

The spike's second question — `pool.at(interaction)` through a generic classmethod-bearing protocol
— was **not run**. It was gated on the first question passing as written, and it did not: since
`ScopeT` now comes from a parameter rather than a bound, `at()` would need the same bound-directed
solving that finding 1 rules out entirely. It stays deferred in 59's "Not included", now on a
measured rather than an assumed basis.
