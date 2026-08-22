# 26 — Topic bus and expiry chrome

## Problem

Cross-mount refresh has no primitive. Every `invalidate()` caller in the bot today is a
panel refreshing itself. When someone edits a build, every *other* live panel showing it —
a second moderator's `/build view`, an open edit panel, a search result — keeps showing the
old world until its owner clicks something.

90 rejected the Redux-style store and said "add a host-side event bus, not a store in the
package". The productization decision moves the bus package-side; the store half of that
rejection stands in full, because the database behind the services is the only source of
truth and `Controlled`/`Managed` exists to prevent a second one.

**Correction to the draft's motivation.** Posted showcase messages are *not* the gap. The
bot already keeps them current durably: a database trigger enqueues `discord_sync_queue`
work on every build write, `ReconciliationCog` drains it every 15s (LISTEN-nudged), and
`PostReconciler` diffs desired posts against `discord_posts` rows. `refresh_posts()` is the
same loop run early for latency. A bus subscriber that also edited those messages would be
a second writer racing the diff loop — precisely the defect the reconciler's docstring says
it was built to remove ("it replaces the per-surface idempotency schemes that grew up
separately"). The bus's territory is **live in-process mounts**, which nothing covers, and
which no durable projection can cover because they are not persisted anywhere.

The bus also creates the situation 90's ephemeral-handoff deferral was waiting for, on its
own terms: "only worth building for a view that must update itself unattended, which none
does." A mount bound to a topic is exactly such a view, and its webhook token dies at 15
minutes. Today the push lands silently in `pending` and the panel goes quietly stale — the
one dishonest behaviour left in the delivery layer.

## A. Portable core — `squid_layouts/topics.py`

> Publish says only "this changed." Subscribers re-read the world; the bus never carries it.

```python
type Topic = Hashable
type Subscriber = Callable[[Topic], Awaitable[None]]

class TopicBus:
    def __init__(self, *, concurrency: int = 4) -> None: ...
    def subscribe(self, topic: Topic, callback: Subscriber, *, label: str = "") -> Callable[[], None]: ...
    def publish(self, *topics: Topic) -> None: ...
    async def run(self) -> None: ...
    def snapshot(self) -> BusSnapshot: ...
```

1. **Topics are `Hashable`, tuples are the shipped convention** — the same call plan 24 made
   for session keys, for the same reason: the bus hashes and compares, it never reads key
   internals. `("build", "123")` beats a format string because a suffix convention
   eventually collides.
   Matching is *exact*: no prefix, no wildcard, no hierarchy. That has a sharp edge the draft
   missed — `("build", 123) != ("build", "123")` — so a host defines **one** constructor for
   its topic vocabulary and every publisher goes through it. Prefix subscription stays out;
   if it ever clears the bar it arrives as a separate `subscribe_prefix`, not as tuple
   structure quietly becoming semantic.
2. **The callback receives the topic that fired.** One subscriber usually serves several
   topics; a no-argument callback forces a closure per topic for no gain. Passing the topic
   is still payload-free: a topic is an address, not state.
3. **Delivery contract, stated once because everything else follows from it:**
   > For every subscriber live when `publish(t)` returns, at least one of its callbacks for
   > `t` *begins* after that return.

   No ordering between topics, no delivery count, no payload. This is what makes coalescing
   correct rather than merely convenient, and it is directly testable.
4. **Coalescing state machine, per topic.** `idle → queued → in flight`, and a publish
   during flight sets `redeliver`, re-enqueued when that drain finishes. Two consequences
   worth naming:
   - **At most one drain per topic is ever in flight**, so a subscriber never runs
     concurrently with itself and a slow read cannot be overtaken by a faster one that
     started later.
   - **The queue can never exceed the number of distinct live topics.** A write storm costs
     memory in topics, not in publishes — the property that lets `publish` be an unbounded
     `put_nowait` on a hot path.

   The failure mode this replaces is subtle enough to name: clearing the coalescing marker
   *after* the callbacks would drop a publish that landed mid-drain, and the panel would
   show pre-write state forever. `Reactor.schedule` already gets this right by discarding at
   pop; the bus makes it a contract with a test.
5. **Publish is a synchronous enqueue; a supervised drain executes.** Domain code never
   blocks on UI refresh and never sees a UI exception. `publish` must be called from the
   loop thread — from a worker thread, go through `loop.call_soon_threadsafe`. Publishing
   before `run()` starts is fine; the queue buffers, bounded as above.
6. **Bounded concurrency across topics, sequential within one.** Different topics drain
   concurrently up to `concurrency`; one topic's subscribers run in registration order.
   This is decided now rather than later because a subscriber can come to depend on
   serialization, and widening the guarantee afterwards is a silent behaviour change.
7. **`run()` owns its drains.** It holds an `asyncio.TaskGroup` and cannot return with a
   drain outstanding; cancellation propagates, and in-flight publishes are dropped by
   contract (see §9). *Deviation, deliberate*: `packages/squid-layouts/tests/test_public_api.py`
   asserts the core namespace imports with both `discord` **and** `anyio` blocked, and anyio
   is only in the `discord` extra — so a portable module cannot import it. `asyncio.TaskGroup`
   is structured and stdlib, so the ownership rule is satisfied in substance; this is not a
   bare `create_task`. Under anyio's asyncio backend it nests inside the host's task group
   without ceremony. Everything Discord-shaped (§B–D) uses anyio normally.
8. **Failure isolation.** A raising subscriber is logged with its topic and label; siblings
   still run, the drain still completes, the publisher never hears about it. `except
   Exception`, never `BaseException` — cancellation is not an error.
9. **Not durable, and the docstring says so.** A process that dies with a queued topic
   loses it. The bus is a latency projection over a path that must already exist; anything
   that must not be lost belongs in the host's durable machinery. This sentence is the one
   that stops a library user from making the bus load-bearing.
10. **Re-entrancy is legal**, since publish is a synchronous enqueue and never recurses. A
    topic that publishes itself is an infinite loop the bus cannot detect; it counts
    consecutive self-publishes per topic and logs once past a threshold. Diagnostics, not
    policy.
11. **Unsubscribe is exact and safe mid-drain.** A drain snapshots its subscriber list, then
    re-checks membership before each call, so a subscription cancelled between enqueue and
    delivery is never invoked. This is what keeps a finished mount from being refreshed.
12. **`snapshot()`** returns per-topic subscriber counts with labels, queued/in-flight state,
    and delivered/failed counters — the `Mount.snapshot()` precedent, and what plan 25's cog
    renders as `ui topics`.
13. **Single-process by contract.** Subscriptions are local. A Redis or LISTEN adapter needs
    only to call `publish` from its reader task; local `subscribe` is untouched, so sharding
    is an adapter rather than a redesign.

## B. Mount glue — `sl.discord.follow`

```python
unfollow = sl.discord.follow(bus, mount, ("build", "123"), ("build_group", "7"))
```

1. **Named `follow`, not `bind`.** Plan 15 deleted `bind` because it meant "commit";
   reusing the name three plans later for a subscription would resurrect a retired meaning.
2. **The mount is held weakly.** The subscription drops itself when the referent is gone.
   The bus must never be the thing keeping a panel alive — `live.py` already made this
   choice for the same reason, and a strong closure over a mount would quietly undo it.
   Unsubscribe is also registered on `Mount.on_finish`, so the normal path is exact and the
   weakref is the backstop.
3. **The callback is `await mount.refresh()`** — one line, and the mount's own `scheduler`
   decides whether that is immediate or coalesced. Two layers of coalescing compose exactly
   right: the bus coalesces per topic, the `Reactor` per mount, so a mount following three
   topics that all fire redraws once.
4. **Follow before send.** Subscribing after the first render loses a write that lands
   between the read and the subscription. The docstring says so and the host examples do it.
5. **Plain callbacks stay first-class.** Nothing about `follow` is privileged; a host
   function that re-reads a service and does its own thing is an ordinary subscriber.
6. **Recovery (plan 27) restores mounts, not bindings.** A rehydrated mount follows nothing
   until the host's recovery hook calls `follow` again. Components that want to survive a
   restart should expose their topics so that hook is mechanical; the framework does not
   persist subscriptions.

## C. Reactor hardening

The `Reactor` has **zero call sites today** — nothing in the bot passes `scheduler=`. This
plan is what makes it load-bearing, so two gaps close with it:

1. **Bounded concurrency with per-mount in-flight tracking.** Today the loop awaits one
   `refresh_now()` at a time; twenty panels following one hot build would serialize twenty
   Discord edits. Same state machine as §A4, keyed by mount, bounded by a capacity limiter
   (the bot's own fan-out bound is 5). Per-mount in-flight tracking is *required* by the
   concurrency, not optional: `_stage`/`_commit` are not re-entrant.
2. **`Reactor.watch(mount)`** — the registration the expiry sweep in §D reads. Folding the
   sweep into the existing loop keeps the host's supervised task count at two (`bus.run()`,
   `reactor.run()`) instead of three.

## D. Expiry chrome

1. **The sweep, not a timer per mount.** `Reactor.run()` ticks (default 10s) over its weak
   watch set. Polling rather than scheduling is the right shape here because `expires_at`
   *moves*: every click renews the handle, so a scheduled timer would have to be cancelled
   and re-armed from a mount hook that does not exist. Re-reading `mount.handle.expires_at`
   each tick needs no new `Mount` API and cannot go stale. The margin (default 60s) swamps
   the tick granularity.
2. **Trigger**: not finished, has live bindings, holds a handle with `permanent=False` and a
   known `expires_at`, and `expires_at - now <= margin`. Fires once per handle; a renewal
   that pushes `expires_at` out re-arms it.
3. **The final flush** sets a mount-level status line and refreshes: "Live updates paused —
   press any control to resume." No Cascade-style continue button — every control is already
   the continue button, and the banner makes the pause visible instead of silent.
4. **`Mount.status` is the mechanism, and it is framework-drawn.** `_draw` builds
   `Document(tree.nodes, …)`, so the mount appends its status node there. The alternative —
   a context value the component reads — was rejected: a component that forgot to read it
   would show nothing, which is the silent staleness this plan exists to remove. The text is
   `Chrome.updates_paused`, a `TextLike` resolved by the mount's `Localization` (plan 17), so
   it translates like every other chrome string, and it passes through the planner as part of
   the document, so it is budgeted rather than bolted on. A full document can degrade one
   step to make room; that is visible and correct.
5. **Clearing is automatic.** `_begin_dispatch` clears the status, so "press any control"
   is literally true: the click renews the handle (`_renew`), the mount is dirty, and the
   flush delivers the current world with no banner.
6. **Two framework bugs this exposes, both in scope:**
   - `refresh_now()` returns early only on `self._handle is None`. An *expired* handle is
     not `None`, so every publish after expiry pays a full `on_load` + render + plan before
     `_deliver` discovers it cannot write. Add `or self._handle.expired()`. The draft's
     "publishes accumulate in `pending`" is loose in the same place: what accumulates is one
     dirty bit, and the resuming flush re-reads the world rather than replaying anything.
   - **Out-of-band refresh currently extends an idle mount's life.** `_commit` sets
     `_active` and hands discord.py a brand-new `MountedView(self, self.timeout)`, so a
     followed mount on a busy topic never times out — `/build view` asks for `timeout=300`
     and would get immortality instead. Fix at the commit point: construct the view with the
     *remaining* idle budget, so the timeout counts from the last interaction. This also
     makes `MountSnapshot.idle` and `expires_in` true, which they are not today.
7. **What the banner promises, and for how long.** Once the token is dead no commit happens,
   `_active` freezes, and the mount finishes `timeout` later. A click after that gets
   `Chrome.session_ended` — a correct, different message, already implemented. So the banner
   is true for exactly the resume window the host chose, and the framework answers a late
   click honestly rather than failing the interaction. Hosts should pick `timeout` on
   topic-followed mounts deliberately; the docstring says which knob that is.

## E. The channel-refetch experiment

Plan 23 §5 did not leave this open — it **rejected** it, on documentation grounds: fetching a
public interaction response through the channel proves location, not edit authority, and
inferring authority from an object is the exact category error 23 removed. What changed is
the payoff, not the argument: with a bus, a panel holding permanent credentials never pauses
at all, so the question is worth one empirical answer.

- Run it as a throwaway integration check against real Discord, not as production code:
  does `PATCH /channels/{id}/messages/{id}` succeed on a message created by the application's
  own interaction webhook?
- If it fails, record that in 23 §5 and the question is closed for good.
- If it succeeds, it is still undocumented behaviour, so it ships as an opt-in `upgrade=True`
  on `respond_to` that attempts the fetch and **falls back to interaction authority on any
  error**. Public panels then escape expiry and the banner becomes ephemeral-only.
- Either outcome gets written back into 23 so the next reader sees a decision, not a rumour.

## Bot wiring

The topic vocabulary is the vocabulary the bot already has: `(ResourceKind, resource_key)` —
`("build", "123")`, string key, matching `discord_posts` and `ReconciliationJob` exactly. One
helper (`topic_for(kind, key)`) so the str/int edge in §A1 cannot bite.

**Two publish sites, both existing chokepoints, zero changes to domain services:**

| Site | Covers | Latency |
|---|---|---|
| `RedstoneSquid.refresh_posts(kind, key)` | every interactive write in this process (12 call sites today) | immediate |
| `ReconciliationCog._process_job`, after a successful reconcile | writes from the worker or API process, and any retried write | ≤15s, LISTEN-nudged |

The second is what quietly buys most of the cross-process story: a build confirmed by the
worker reaches live panels in the bot without Redis, because the durable queue is already
drained here. `bus.publish` on the durable path is also idempotent by construction — a
redelivered job publishes a payload-free notification twice, which coalescing makes free.

**Consumers to wire first**: `/build view` (`squid/bot/submission/search.py`, a public
`timeout=300` panel) and the edit panel (`squid/bot/submission/ui/views.py`), both following
`("build", str(build_id))`. Posted showcase messages stay with `PostReconciler` and are
explicitly *not* bus consumers.

## Landing order

Independently landable, in this order: **A** (core bus + tests, no consumers) → **B** (glue)
→ **C** (reactor) → **D** (expiry chrome, which needs C's watch set) → bot wiring. **E** is a
one-off experiment that can run at any point and only writes back to 23.

## Verification

- **Contract**: a publish during a drain produces a second callback that begins after it; the
  queue length never exceeds the distinct-topic count under a burst; a subscription cancelled
  between enqueue and drain is never called; a raising subscriber does not stop its siblings;
  cancelling `run()` leaves no drain running.
- **Concurrency**: two topics drain concurrently, one topic's subscribers do not; a mount
  never refreshes concurrently with itself under `Reactor` fan-out.
- **Glue**: `follow` unsubscribes on `Mount.on_finish`; a collected mount's subscription
  disappears after `gc.collect()`; a mount following three topics that all fire redraws once
  through the reactor.
- **Expiry**: with a fake handle expiring inside the margin, the sweep flushes once and the
  render carries the chrome line; a `_renew` that extends `expires_at` postpones it; a publish
  after expiry stages nothing (no `on_load` call); the first click clears the status and
  delivers current state; a late click gets `session_ended`.
- **Idle**: a mount refreshed out of band ten times still times out on its original budget.
- **Portability**: `test_public_api.py`'s no-discord/no-anyio import check extends to
  `squid_layouts.topics`.
- **Host**: two panels open on one build; an edit through one refreshes the other. Plus a
  regression that `refresh_posts` still reconciles posts exactly once — the bus must not have
  become a second writer.

## Rejected alternatives

- **Payloads on the bus.** They would make coalescing unsound (dropping a duplicate is only
  safe when it carries nothing) and create a second source of truth. Subscribers re-read.
- **Per-mount `asyncio.Event` instead of callbacks.** Every mount would need a waiting task —
  a task per panel, owned by nobody. The callback plus one drain is the same thing with one
  owner.
- **Polling: every mount re-reads on a timer.** N×DB reads for a mostly-idle N, and still
  slower than a click.
- **Make `squid.events` the primitive.** Attractive for the bot — durable, cross-process —
  but the domain-event log only fires on *status transitions*, so an ordinary build edit
  produces no event at all; the general "this changed" signal is `discord_sync_queue`. And a
  library cannot ship a Postgres event log as its refresh primitive. Resolution: the bus is
  the in-process primitive and the bot feeds it from the durable path it already drains.
- **Prefix or wildcard topics.** Deferred, not designed around: exact matching is a dict
  lookup, and hierarchy arrives as an explicit method if a consumer ever needs it.
- **A store.** Still rejected, for 90's reasons, in full.
