# 26 — Topic bus and expiry chrome

## Problem

squid-layouts has no cross-mount refresh primitive. `invalidate()` is a panel saying "I
changed"; nothing says "the thing you are showing changed." Any application with two live
views of one entity has this problem — two moderators on one record, a list beside a detail
panel, a dashboard beside the form that edits it — and today every library user hand-rolls
it, badly, because doing it well means getting coalescing, task ownership and token expiry
right at once. A UI framework that ships a mount lifecycle, a scheduler and a durability
boundary and then leaves this to the reader has a hole in the middle of it.

90 rejected the Redux-style store and said "add a host-side event bus, not a store in the
package." The productization decision moves the bus package-side; the store half of that
rejection stands in full, because the application's own data layer is the only source of
truth and `Controlled`/`Managed` exists to prevent a second one.

The bus also creates the situation 90's ephemeral-handoff deferral was waiting for, on its
own terms: "only worth building for a view that must update itself unattended, which none
does." A mount bound to a topic is exactly such a view, and its interaction token dies at 15
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
    async def drain(self) -> None: ...
    def snapshot(self) -> BusSnapshot: ...
```

1. **Topics are `Hashable`; the host owns the vocabulary.** The same call plan 24 made for
   session keys, for the same reason: the bus hashes and compares, it never reads key
   internals. Tuples are the shipped convention — `("build", "123")` beats a format string
   because a suffix convention eventually collides — but a library user with an existing
   entity-id type brings it unchanged.
   Matching is *exact*: no prefix, no wildcard, no hierarchy. That has a sharp edge worth
   documenting rather than discovering — `("build", 123) != ("build", "123")` — so the
   guidance is one constructor per vocabulary, at the host, and every publisher goes through
   it. Prefix subscription stays out; if it ever clears the bar it arrives as an explicit
   `subscribe_prefix`, not as tuple structure quietly becoming semantic.
2. **The callback receives the topic that fired.** One subscriber usually serves several
   topics; a no-argument callback forces a closure per topic for no gain. Passing the topic
   is still payload-free: a topic is an address, not state.
3. **Delivery contract, stated once because everything else follows from it:**
   > For every subscriber live when `publish(t)` returns, at least one of its callbacks for
   > `t` *begins* after that return.

   No ordering between topics, no delivery count, no payload. This is what makes coalescing
   correct rather than merely convenient, and it is what a library user needs in one sentence
   to reason about their own subscribers.
4. **Coalescing state machine, per topic.** `idle → queued → in flight`, and a publish during
   flight sets `redeliver`, re-enqueued when that drain finishes. Two consequences worth
   naming in the docs:
   - **At most one drain per topic is ever in flight**, so a subscriber never runs
     concurrently with itself and a slower read cannot be overtaken by one that started later.
   - **The queue can never exceed the number of distinct live topics.** A write storm costs
     memory in topics, not in publishes — the property that lets `publish` be an unbounded
     `put_nowait` on an application's hot path.

   The failure mode this rules out is subtle enough to name: clearing the coalescing marker
   *after* the callbacks would drop a publish that landed mid-drain, and the view would show
   pre-write state forever. `Reactor.schedule` already gets this right by discarding at pop;
   the bus makes it a contract with a test.
5. **Publish is a synchronous enqueue; a supervised drain executes.** Application code never
   blocks on UI refresh and never sees a UI exception, so `publish` is safe to call from
   inside a transaction or a request handler. It must be called from the loop thread — from a
   worker thread, go through `loop.call_soon_threadsafe`. Publishing before `run()` starts is
   fine; the queue buffers, bounded as above.
6. **Bounded concurrency across topics, sequential within one.** Different topics drain
   concurrently up to `concurrency`; one topic's subscribers run in registration order. This
   is decided now rather than later because a subscriber can come to depend on serialization,
   and widening a published guarantee afterwards is a silent behaviour change in someone
   else's application.
7. **`run()` owns its drains.** It holds an `asyncio.TaskGroup` and cannot return with a drain
   outstanding; cancellation propagates, and in-flight publishes are dropped by contract (§9).
   *Deviation, deliberate*: `tests/test_public_api.py` asserts the core namespace imports with
   both `discord` **and** `anyio` blocked, and anyio ships only in the `discord` extra — a
   portable module cannot import it. `asyncio.TaskGroup` is structured and stdlib, so task
   ownership holds in substance; this is not a bare `create_task`. Under anyio's asyncio
   backend it nests inside a host's task group without ceremony. Everything Discord-shaped
   (§B–D) uses anyio normally.
8. **Failure isolation.** A raising subscriber is logged with its topic and label; siblings
   still run, the drain still completes, the publisher never hears about it. `except
   Exception`, never `BaseException` — cancellation is not an error.
9. **Not durable, and the docstring leads with it.** A process that dies with a queued topic
   loses it. The bus is a latency projection over a path the application must already have;
   anything that must not be lost belongs in that path. This is the sentence that stops a
   library user from making the bus load-bearing, and it is worth more than any feature here.
10. **Re-entrancy is legal**, since publish is a synchronous enqueue and never recurses. A
    topic that publishes itself is an infinite loop the bus cannot detect; it counts
    consecutive self-publishes per topic and logs once past a threshold. Diagnostics, not
    policy.
11. **Unsubscribe is exact and safe mid-drain.** A drain snapshots its subscriber list, then
    re-checks membership before each call, so a subscription cancelled between enqueue and
    delivery is never invoked. This is what keeps a finished mount from being refreshed.
12. **`drain()` is the test seam.** It processes everything queued, including publishes made
    during it, and returns when the bus is quiet — so a library user's test is `publish(t)`,
    `await bus.drain()`, assert, with no background task and no sleeps. Shipping this is the
    difference between a testable subscriber and one whose tests are flaky in someone else's
    suite. It does not terminate against a self-publishing loop, which is §10's warning
    arriving early and loudly.
13. **`snapshot()`** returns per-topic subscriber counts with labels, queued/in-flight state,
    and delivered/failed counters — the `Mount.snapshot()` precedent, and what plan 25's cog
    renders as `ui topics`.
14. **Single-process by contract, with a named seam.** Subscriptions are local. `publish` is
    safe to call from any reader task, so a distributed host bridges its own transport into
    it — Redis, NOTIFY, a queue consumer — in a handful of lines. The library ships no
    bridge and says so; claiming a distribution story it has not tested would be worse than
    having none.

## B. Following a mount

```python
unfollow = reactor.follow(mount, ("build", "123"), ("build_group", "7"))
```

1. **`follow` is a `Reactor` method, not a free function.** The draft's
   `sl.discord.bind(bus, mount, *topics)` needs a bus, a mount and (for the expiry sweep) a
   watcher, and it collides with the name plan 15 deleted, where `bind` meant "commit".
   Hanging it off the reactor the mount is already scheduled by removes both problems: the
   host constructs `Reactor(bus)` once and every later call is `reactor.follow(mount, …)`.
2. **The mount is held weakly.** The subscription drops itself when the referent is gone.
   The bus must never be the thing keeping a panel alive — `live.py` made this choice already
   and a strong closure over a mount would quietly undo it. Unsubscribe is also registered on
   `Mount.on_finish`, so the normal path is exact and the weakref is the backstop.
3. **The callback is `await mount.refresh()`** — one line, and the mount's scheduler decides
   whether that is immediate or coalesced. The two layers compose exactly right: the bus
   coalesces per topic, the reactor per mount, so a mount following three topics that all
   fire redraws once.
4. **Follow before send.** Subscribing after the first render loses a write that lands
   between the read and the subscription. The docstring says so and the example does it.
5. **Plain callbacks stay first-class.** Nothing about `follow` is privileged; a host
   function that re-reads its own services and does something else entirely is an ordinary
   `bus.subscribe`.
6. **Recovery (plan 27) restores mounts, not bindings.** A rehydrated mount follows nothing
   until the host's recovery hook calls `follow` again. Components that expect to survive a
   restart should expose their topics so that hook is mechanical; the framework does not
   persist subscriptions, because a subscription is a live process's opinion.

## C. Reactor becomes the live-update engine

The `Reactor` has **no call sites at all today** — nothing passes `scheduler=`. It is
therefore free to change shape, and this plan is what makes it load-bearing:

1. **`Reactor(bus=None)`**, gaining `follow`. Without a bus it is exactly today's coalescing
   refresh loop.
2. **Bounded concurrency with per-mount in-flight tracking.** Today the loop awaits one
   `refresh_now()` at a time; twenty panels following one hot topic would serialize twenty
   Discord edits. Same state machine as §A4, keyed by mount, bounded by a capacity limiter.
   Per-mount tracking is *required* by the concurrency rather than optional: `_stage` and
   `_commit` are not re-entrant.
3. **It carries the expiry sweep (§D)**, so the host supervises two coroutines total,
   `bus.run()` and `reactor.run()`, each with an obvious owner. A facade that starts tasks
   for the host was considered and rejected: this framework's concurrency doctrine is that
   every task has a visible owner, and hiding two of them behind a convenience object trades
   the doctrine for one line of quickstart.

## D. Expiry chrome

1. **A sweep, not a timer per mount.** `Reactor.run()` ticks (default 10s) over its weak
   watch set. Polling is the right shape here because `expires_at` *moves*: every click
   renews the handle, so a scheduled timer would need cancelling and re-arming from a `Mount`
   hook that does not exist. Re-reading `mount.handle.expires_at` each tick needs no new API
   and cannot go stale. The margin (default 60s) swamps the tick granularity.
2. **Trigger**: not finished, following at least one topic, holding a handle with
   `permanent=False` and a known `expires_at`, and `expires_at - now <= margin`. Fires once
   per handle; a renewal that pushes `expires_at` out re-arms it.
3. **The final flush** sets a mount-level status line and refreshes: "Live updates paused —
   press any control to resume." No Cascade-style continue button — every control is already
   the continue button, and the banner makes the pause visible instead of silent.
4. **`Mount.status` is the mechanism, and it is framework-drawn.** `_draw` builds
   `Document(tree.nodes, …)`, so the mount appends its status node there. The alternative — a
   context value the component reads — was rejected: a component that forgot to read it would
   show nothing, which is the silent staleness this plan exists to remove, and a library
   cannot require every user's component to opt into its own honesty. The text is
   `Chrome.updates_paused`, a `TextLike` resolved by the mount's `Localization` (plan 17), so
   it translates like every other chrome string, and it passes through the planner as part of
   the document, so it is budgeted rather than bolted on. A full document can degrade one
   step to make room; that is visible and correct.
5. **Clearing is automatic.** `_begin_dispatch` clears the status, so "press any control" is
   literally true: the click renews the handle, the mount is dirty, and the flush delivers
   the current world with no banner. `status` is a general slot, not a pause flag — a host
   with its own "reconnecting" or "read-only" line gets it for free.
6. **Two framework bugs this exposes, both in scope:**
   - `refresh_now()` returns early on `self._handle is None` but not on an *expired* handle,
     which is not `None`. Every publish after expiry therefore pays a full `on_load` + render
     + plan before `_deliver` discovers it cannot write. Add `or self._handle.expired()`. The
     draft's "publishes accumulate in `pending`" is loose in the same place: what accumulates
     is one dirty bit, and the resuming flush re-reads the world rather than replaying
     anything.
   - **Out-of-band refresh currently extends an idle mount's life.** `_commit` sets `_active`
     and hands discord.py a brand-new `MountedView(self, self.timeout)`, so a followed mount
     on a busy topic never times out — a library user who asks for `timeout=300` gets
     immortality and a slow leak instead. Fix at the commit point: build the view with the
     *remaining* idle budget, so the timeout counts from the last interaction. This also makes
     `MountSnapshot.idle` and `expires_in` true, which they are not today.
7. **What the banner promises, and for how long.** Once the token is dead no commit happens,
   `_active` freezes, and the mount finishes `timeout` later. A click after that gets
   `Chrome.session_ended` — a correct, different message, already implemented. The banner is
   true for exactly the resume window the host chose, and a late click is answered honestly
   rather than failing the interaction. The docstring names `timeout` as the knob.

## E. The channel-refetch experiment

Plan 23 §5 did not leave this open — it **rejected** it, on documentation grounds: fetching a
public interaction response through the channel proves location, not edit authority, and
inferring authority from an object is the exact category error 23 removed. What changed is
the payoff, not the argument: with a bus, a panel holding permanent credentials never pauses
at all, so the question is worth one empirical answer.

- Run it as a throwaway integration check against real Discord, not as production code: does
  `PATCH /channels/{id}/messages/{id}` succeed on a message created by the application's own
  interaction webhook?
- If it fails, record that in 23 §5 and the question closes for good.
- If it succeeds it is still undocumented behaviour, so it ships as an opt-in `upgrade=True`
  on `respond_to` that attempts the fetch and **falls back to interaction authority on any
  error**. Public panels then escape expiry and the banner becomes ephemeral-only.
- Either outcome is written back into 23, so the next reader finds a decision rather than a
  rumour.

## Wiring, and the two rules the docs lead with

```python
bus = sl.TopicBus()
reactor = sl.discord.Reactor(bus)
mount = sl.discord.Mount(panel, scheduler=reactor)
reactor.follow(mount, ("build", "123"))
await mount.send(sl.discord.respond_to(interaction))
# supervised by the host: bus.run(), reactor.run()
```

Publishing is the half a library cannot write for its user, so the guide states the two rules
that keep it from going wrong:

1. **Publish from wherever the application already funnels "this changed"** — a service-layer
   commit hook, a change-feed drain, an outbox consumer — never scattered through domain
   code. A host that publishes from the drain of a durable change feed gets cross-process
   refresh for free, because that drain already sees writes from every process.
2. **Do not subscribe anything a durable projection already owns.** The bus refreshes live
   views. A message the application keeps rendered by its own reconciliation loop must not
   acquire a second writer; that is a race, not a feature. If a surface survives restarts, it
   is not a bus consumer.

Both rules are worked examples in the package docs rather than assertions, using this bot's
own shape: a `refresh_posts()`-style latency nudge and a reconciliation drain as the two
publish sites, and its reconciler-owned posted messages as the thing deliberately *not*
subscribed.

## Consumers

The consumer is the library user; the bot's contribution is the worked example and an
integration test, as in plan 27. It also has real live-mount staleness to fix — two panels
open on one record never hear about each other — and wiring `/build view` and the edit panel
to `("build", str(id))` is the honest end-to-end test of the primitive. Its posted showcase
messages stay with the existing durable reconciler, which is rule 2 demonstrated rather than
merely stated.

## Landing order

Independently landable, in this order: **A** (core bus, `drain()`, tests, no consumers) →
**B**/**C** (reactor gains a bus, `follow`, concurrency) → **D** (expiry chrome, which needs
C's watch set) → docs and the bot example. **E** is a one-off experiment that can run at any
point and only writes back to 23.

## Verification

- **Contract**: a publish during a drain produces a second callback that begins after it; the
  queue length never exceeds the distinct-topic count under a burst; a subscription cancelled
  between enqueue and drain is never called; a raising subscriber does not stop its siblings;
  cancelling `run()` leaves no drain running; `drain()` quiesces without a background task.
- **Concurrency**: two topics drain concurrently, one topic's subscribers do not; a mount
  never refreshes concurrently with itself under reactor fan-out.
- **Following**: `follow` unsubscribes on `Mount.on_finish`; a collected mount's subscription
  disappears after `gc.collect()`; a mount following three topics that all fire redraws once.
- **Expiry**: with a fake handle expiring inside the margin, the sweep flushes once and the
  render carries the chrome line; a renewal that extends `expires_at` postpones it; a publish
  after expiry stages nothing (no `on_load` call); the first click clears the status and
  delivers current state; a late click gets `session_ended`.
- **Idle**: a mount refreshed out of band ten times still times out on its original budget.
- **Portability**: `test_public_api.py`'s no-discord/no-anyio import check extends to
  `squid_layouts.topics`; `sl.TopicBus` and `sl.discord.Reactor.follow` join the exported-API
  assertions.
- **Host example**: two panels open on one build, an edit through one refreshes the other,
  and the posted card is still written exactly once — rule 2 as a regression test.

## Rejected alternatives

- **Payloads on the bus.** They would make coalescing unsound — dropping a duplicate is only
  safe when it carries nothing — and hand every library user a second source of truth.
- **Per-mount `asyncio.Event` instead of callbacks.** Every mount would need a waiting task:
  a task per panel, owned by nobody. One drain plus a callback is the same thing with an owner.
- **Polling: every mount re-reads on a timer.** N reads for a mostly-idle N, and still slower
  than a click.
- **A `LiveUpdates` facade that owns both tasks.** One line shorter at the quickstart, at the
  cost of the one property this framework does not trade: a visible owner per task.
- **Prefix or wildcard topics.** Deferred, not designed around: exact matching is a dict
  lookup, and hierarchy arrives as an explicit method if a consumer ever needs it.
- **A store.** Still rejected, for 90's reasons, in full.
