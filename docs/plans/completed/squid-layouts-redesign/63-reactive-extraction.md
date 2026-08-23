# 63 — Extract the reactive-transactional core

> **Status: implemented.** Core extraction landed in `25f73d65`; shared state, topics, and
> subscription reconciliation in `2666d9fb`; optional resources in `930c7f9f`.

## Decision

The reactive engine is useful without a layout planner or Discord host, so it is now the
zero-dependency `squid-reactive` workspace package, imported as `squid_reactive`.

The package boundary follows the dependency direction already present in the design:

1. `core` — cells, computed values, transactions, OCC, action participants, tracked reads,
   and the reusable `Reactive` owner;
2. `shared` — `Shared` plus exact `CellAddress` publication;
3. `topics` — `Topic`, `Address`, tracked `watch()`, a two-method synchronous bus protocol,
   the small in-process `LocalTopicBus`, and render subscription reconciliation;
4. `resources` — an optional module for tracked async values whose loads remain owned by the
   caller's task.

`squid-layouts` keeps compatibility exports and makes `Component` a `Reactive` subclass. It
owns rendering, Discord delivery, Reactor, AnyIO run loops, profiling, expiry, durability, and
`PostgresTopicBridge`.

## Subscription invariant

A projection has exactly two read sets: the committed set visible to the reader and at most one
staged successor. Staging subscribes to the union before delivery, closing the read-to-subscribe
race. Commit promotes the staged set and retires anything the new visible projection no longer
reads; discard retires only what the failed candidate introduced. There is no collection of
parallel candidates.

This is the third role of the read set, after computed invalidation and OCC: it makes following
an exact consequence of what the projection read. Authors never pair reads with hand-written
subscribe/unsubscribe calls, so conditional reads cannot leave stale subscriptions or silently
miss new ones.

## Topic publication

`TopicBus` is the host seam: `subscribe(address, callback)` and `publish(*addresses)`. Publication
must advance a watched topic's version before notifying subscribers, including when none exist;
a publish that lands while a resource load is in flight therefore makes the completed result
immediately pending again.

`LocalTopicBus` delivers synchronously in registration order. Each subscriber is isolated: a
raise is reported through `on_subscriber_error`, the remaining subscribers still run, and a
broken reporting hook is itself logged and isolated. The bus has no task or lifecycle to die.

## Explicitly outside

- Reactor and refresh coalescing;
- AnyIO run loops and task ownership;
- PostgreSQL bridging and durability;
- expiry policy and presentation lifecycle;
- profiling policy.

Those are hosting opinions. The host supplies them around the protocol and owns every loop.
