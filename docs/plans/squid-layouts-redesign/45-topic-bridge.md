# 45 — Cross-process topics: a Postgres bridge

## Problem

`TopicBus` is process-local by design (`topics.py` docstring: "bridge an external change feed
into `publish` when multiple processes need to refresh one another"), and the bot is about to
have a second process: `squid/worker/rendering.py` finishes renders whose panels live in the
bot. Today its only route to a panel is a database row the panel re-reads on its next click.
CascadeUI answers this with a persisted Redux store coordinated over PostgreSQL
`LISTEN`/`NOTIFY`. The store half is rejected and stays rejected ([90](90-deferred.md),
[40](40-shared-state.md) §3); the coordination half is the gap.

## Decision

A **host-owned bridge**, not a relay on the bus. An automatic relay of every local `publish()`
would loop (A → NOTIFY → B → relay → NOTIFY → A) unless every publish carried an origin and
every bridge filtered, and it would silently drop the addresses it cannot encode. The bus
docstring's stance is right: the host bridges *its* feed, and publishes through the bridge.

Soundness against the bus contract:

- the bridge is one more caller of `TopicBus.publish`, so coalescing and "at least one
  callback begins after `publish` returns" compose — remote publish → local publish → the
  local contract holds;
- the NOTIFY payload is an *encoded topic*, never state, so payload-free holds and subscribers
  still re-read;
- `Shared` cell addresses are `(handle, descriptor)` identities and are not encodable; the
  codec returns `None` and the bridge publishes them locally only. That is the right outcome
  and leaves 40 §3 untouched.

## Design

### 1. `TopicCodec` (`topics.py`)

```python
class TopicCodec(Protocol):
    def encode(self, topic: Hashable) -> str | None: ...
    def decode(self, text: str) -> Hashable | None: ...
```

Nothing else in `topics.py` changes.

### 2. `PostgresTopicBridge` (`discord/durability/postgres.py`)

```python
bridge = PostgresTopicBridge(pool, bus, codec, channel="squid_topics")
bridge.publish(*topics)        # local bus.publish now; pg_notify for the encodable ones
await bridge.run()             # LISTEN on one dedicated pooled connection
```

- A per-process `origin` (uuid) travels in the payload; the listener drops its own.
- `publish` inside the caller's transaction is commit-ordered by Postgres, which is the
  ordering a host wants: a subscriber re-reads and finds the row.
- Listener reconnect fires an optional `on_resync()` so the host can publish its coarse
  topics; NOTIFY is not durable, which matches the bus contract exactly.
- The 8000-byte payload limit is documented; the codec is expected to produce short keys.
- No lease/fence reuse — NOTIFY has no ownership. Reuse the asyncpg pool and the
  integration-gated test pattern from `tests/test_durability.py`. Placement under
  `discord/durability/` is a wart (the bridge is portable) accepted because the asyncpg
  extra already lives there.

### 3. The bot

- `squid/bot/topics.py` supplies the `ResourceTopic` codec (`kind:id`).
- Publishers move onto the bridge: `squid/bot/app.py` and `squid/bot/sync/reconciler.py`.
- `squid/worker/rendering.py` publishes the build's resource topic when a render completes,
  so a panel showing that build repaints without a click.

## Considered, not done

- **Relay on `TopicBus`.** Rejected above.
- **Durable `Shared`.** Rejected by 40 §3; a namespace is the wrong home for anything the
  application wants with nobody looking.
- **Redis pub/sub.** The bot already runs Postgres for durability; one fewer moving part.
  The `TopicCodec` seam is transport-neutral, so a Redis bridge is a later file, not a
  redesign.

## Verification

- Two buses on one database each see the other's publish exactly once; own-origin payloads
  are dropped; an unencodable topic is published locally only; reconnect fires `on_resync`;
  `bus.drain()` still terminates with a bridge attached.
- Gated Postgres integration test alongside `test_durability.py`; `tests/test_topics.py` for
  the codec; README "Live updates across mounts" gains the two-process recipe.

## Status

Implemented 2026-08-23. Independent of 42–44.

Two deviations from §3, both about layering rather than design. The vocabulary and its codec
live in a new process-neutral `squid/topics.py`, because the render worker publishes into the
same channel and must not import the Discord layer to do it; `squid/bot/topics.py` keeps
`follow_resource`. And the bridge is opened only when `database.listener_url` is configured --
the same gate the domain-event and permission-epoch listeners already use, since `LISTEN`
needs a session-level connection -- so a deployment without one keeps the local bus and the
reconciler's poll.

`publish` stayed synchronous, matching `TopicBus.publish`, with the notification queued and
sent by `run()`. That puts the NOTIFY on the bridge's own connection rather than the caller's
transaction, so the commit-ordering property in §2 is available through a public `payload()`
the host sends itself with `pg_notify` when it wants it.
