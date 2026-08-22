# 26 — Topic bus and expiry chrome

## Problem

Cross-mount refresh has no primitive. Every `invalidate()` caller in the bot today is a
panel refreshing itself; when a build changes, the posted showcase messages, a live edit
panel, and a search panel showing it have no way to hear about it. 90 rejected the
Redux-style store and said "add a host-side event bus, not a store in the package" — the
productization decision moves the bus package-side, but the store half of that rejection
stands in full: the bus carries no state, because the database behind the services is the
only source of truth and `Controlled`/`Managed` exists to prevent a second one.

The bus also creates the situation 90's ephemeral-handoff deferral was waiting for, on
its own stated terms: "only worth building for a view that must update itself unattended,
which none does." A mount bound to a topic is exactly such a view, and its webhook token
dies at 15 minutes. Today the push lands silently in `Mount.pending` and the panel goes
quietly stale — the one dishonest behavior left in the delivery layer.

## Design

> Publish says only "this changed." Subscribers re-read the world; the bus never carries it.

1. **Portable core** in `squid_layouts/topics.py`: `TopicBus` with
   `subscribe(topic, callback) -> unsubscribe`, `publish(topic)`, and `run()`. Topics are
   hashable tuples — `("build", 123)` — because tuples cannot collide the way format
   strings with suffix conventions eventually do. Nothing Discord-shaped lives here.
2. **Payload-free, and that is the load-bearing choice.** With no payload, subscribers
   must re-read services (one truth), and coalescing is correct by construction: dropping
   a duplicate notification is only safe when notifications carry nothing. Cascade needs
   reducers/selectors/middleware because its store is the truth; ours is not, so the
   apparatus never has to exist.
3. **Publish is synchronous enqueue; a supervised drain executes.** Domain code never
   blocks on UI refresh. `bus.run()` runs under the host's task supervisor next to
   `reactor.run()`, per the anyio ownership rule. Callback failures are logged, never
   propagated to the publisher.
4. **Discord glue**: `sl.discord.bind(bus, mount, *topics)` — subscribes
   `mount.invalidate` + `Reactor.schedule`, unsubscribes via `Mount.on_finish`. Plain
   callbacks stay first-class: the routed tier's subscribers (re-render a showcase card,
   edit the channel message through its permanent handle) are functions, not mounts.
5. **Single-process by contract.** Subscriptions are local; `publish` is the only method
   a Redis-backed implementation must satisfy, so sharding later is an adapter, not a
   redesign.

## Expiry chrome

6. **Paused banner.** For a mount whose handle is not `permanent` and which holds live
   topic bindings, schedule one final flush at `handle.expires_at − margin` whose render
   carries a chrome line: "Live updates paused — press any control to resume." After
   expiry, publishes accumulate in `pending` exactly as today; the first click renews the
   handle (`_renew`, plan 07/23) and the flush delivers everything. No Cascade-style
   continue button: every control is already the continue button — the banner makes the
   pause visible instead of silent.
7. **Channel-refetch upgrade experiment** (plan 23's open item): a *public* interaction
   response may be re-fetchable through the channel as a plain `Message`, minting a
   permanent handle. If it works on real Discord, public panels escape expiry entirely
   and the banner becomes ephemeral-only. Verify at implementation; either outcome gets
   recorded in 23.

## Consumers

- Build updates: services publish `("build", id)`; a subscriber re-renders via
  `build_handler` and edits every posted showcase message (permanent handles — this is
  the consumer that motivated the round, and it needs no expiry machinery at all).
- Ephemeral edit/search panels bound to the same topic are the paused-banner consumers.
