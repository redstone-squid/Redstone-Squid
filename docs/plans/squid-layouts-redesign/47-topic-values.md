# 47 — A topic becomes a value, and a watched read

## Problem

`TopicBus` + `Reactor` ([26](26-topic-bus.md), [45](45-topic-bridge.md)) is correct, but every
screen that wants live data pays for it three times: `reactor.follow(mount, topic)` before the
send, a `reload()` closure because `follow` only re-renders and never re-fetches, and a manual
`await reload(component)` for the initial load. The bot — the package's own worked example —
could not express this with the package API at all, so it grew
`squid/bot/topics.py::follow_resource`, a six-parameter helper wiring two subscriptions and two
finish hooks, used at `squid/bot/submission/search.py:305` and
`squid/bot/submission/ui/views.py:1279`.

The package already solves this for `sl.Shared` cells with no ceremony: a render's tracked reads
land in `ComponentTree.observations`, and `Mount._ensure_follows` / `_prune_follows` reconcile the
mount's subscriptions against them every render. [41](41-reactivity-cells.md) already made the
argument — tracked reads beat hand-declared dependencies. Named topics are the last mechanism
still asking the author to maintain the graph by hand.

That gap was awkward to close cleanly because `type Topic = Hashable`. Since a topic could be
*anything*, no API taking one could also take a callable (a function is hashable) or a collection
of them (a tuple topic is itself iterable), the wire form had to be supplied by the host as a
`TopicCodec`, and "don't mix `("build", 123)` and `("build", "123")`" had to be defended in README
prose. Tightening the type first is what makes the rest pleasant, and it makes the cross-process
story *smaller* rather than larger.

## Phase 1 — `Topic` is a value

`Topic(kind: str, key: str)` and `CellAddress(owner, name)`, both frozen, with
`type Address = Topic | CellAddress` as what the bus actually carries: `subscribe`, `publish`,
`Reactor.follow`, `mount.observed`, `mount.followed`, `ComponentTree.observations`. `Topic` is
what a host writes; a `CellAddress` is only ever received.

`CellAddress` compares its `owner` by identity (`eq=False` plus explicit `__eq__`/`__hash__`), so
a `Shared` subclass that defines its own `__eq__` cannot make two live namespaces collide.

What this deletes:

- **The host codec, for the common case.** Encoding is now *total* on `Topic`, so the package
  ships `KindKeyCodec` (`kind:key`) as `PostgresTopicBridge`'s default and `TopicCodec` demotes to
  an override for a host that must speak a wire format someone else defined. 45 §2's "the codec
  returns `None` for an identity-bearing address" stops being a runtime convention and becomes a
  type distinction: a `CellAddress` is not a `Topic`, so the bridge cannot try.
- **`squid/topics.py`'s `_RESOURCE_KINDS` and `ResourceTopicCodec`** — about 35 lines.
  `resource_topic` survives as the typed constructor, because it is what preserves the
  `ResourceKind` literal that a bare `Topic` cannot.
- **The README's exact-matching warning.** `Topic("build", 123)` is a type error, because `key` is
  `str`. No coercion at runtime: a host wanting `Topic("build", id)` writes its own one-line
  constructor.

Accepted wart: `TopicBus`, `TopicCodec`, `TopicSnapshot` and `TopicFollower` keep their names
while their parameters widen to `Address`. Renaming them to `Address*` is churn for a type whose
dominant case is still a topic.

Breaking change for any host that built tuple topics by hand, which in this repo is
`squid/topics.py` alone.

## Phase 2 — `sl.watch(*topics)`

A topic gets a `_Cell` with no value: an address and a version. Reading it registers with whatever
is consuming reads — a render's `Observation`, or a `Resource` mid-load. Publishing bumps its
version.

```python
class BuildInfo(sl.Component):
    @sl.resource(delivery=sl.ResourceDelivery.ATOMIC)
    async def build(self) -> Build:
        sl.watch(BUILD(self.build_id))
        return await self.queries.get(self.build_id)
```

Consequences of machinery that already exists:

- the render reads the resource, so the topic is in `tree.observations`, so `_ensure_follows`
  subscribes the mount at stage time and `_prune_follows` retires it when a delivered render stops
  reading it;
- a publish bumps the version, so `Resource._recheck` re-pends the value and the mount's existing
  settle loop re-fetches before drawing;
- the initial load is the resource's first settle, so no manual priming call;
- unfollow on finish and the weak-mount backstop are `Reactor.follow`'s, unchanged.

**It closes the race the current API documents rather than fixes.** [26](26-topic-bus.md) §B4 and
the `follow` docstring both say "subscribe before the first read/send", because a write landing
between the read and the subscription is lost. With a version, a publish during the loader's
`await` moves the source, `_recheck` sees it moved, and the mount re-settles — the same protection
resources already have against cell writes.

**It also closes 26 §B6.** A mount restored by `DurableSessionRuntime` re-renders, so it
re-acquires its follows; today "a rehydrated mount follows nothing until the host's recovery hook
calls `follow` again".

### One primitive, called in the body

`sl.watch(*topics)` is the whole surface. A `watch=` argument on `sl.resource` was designed and
dropped: the topic is derived from `self`, so it could only be
`watch=lambda self: BUILD(self.build_id)`, which is *longer* than the statement it replaces,
repeats `self` twice, and wraps past 120 columns once `delivery=` is also present. The forms that
would remove the lambda all fail on this package's own terms — a string field reference is
stringly-typed, and referencing the descriptor from the class body (`watch=BUILD(build_id)`) is a
type-checker lie, because `sl.state()`'s overloads type that name as its value type, not as the
descriptor. Restricting `watch=` to static topics is possible and pointless:
`sl.watch(Topic("config", "global"))` in the body is the same single line.

In-body is also the only form that expresses a conditional watch, or a topic derived from what the
loader just fetched. `BuildEditComponent` is the conditional case already in the tree — its key is
`self.build.id`, `None` until a build exists.

Live data belongs in a `sl.resource`, never `on_load`: `on_load` runs once per instance, guarded
by `_loaded`, under no consumer, so a watch there is untracked and could never reload anyway.

### The gap this phase must close first

**A resource's addressed reads must reach the render that used its value.** `_ResourceDescriptor
.__get__` fills `ComponentTree.resources` only; the loader's tracked reads stay in
`Resource.sources` and never reach `tree.observations`. Without this a watched topic re-fetches but
is never followed, so nothing fires the refresh.

Fix it where `Observation.addresses()` already states the principle for `_Derived` ("a cached
computed is walked rather than re-run: it did not read its sources again, but a render that used
its value still depends on every one of them"): make `Observation` recurse into `Resource.sources`
the same way, and have `__get__` register `bound` with the current `_CONSUMER`.

`watch()` uses `cell.track(cell.settle())`, **not** `cell.read()`: `_Cell.read` calls
`transaction.observe(self)` for addressed cells, installing a lost-update precondition — right for
a shared cell an action wrote, wrong for a topic, which no action writes.

`TopicBus.publish` invalidates the topic cell **before** the `not state.subscriptions` skip, so a
mount with no reactor still re-fetches on its next click.

Name collisions, both accepted: `Reactor.watch(mount)`/`TopicFollower.watch` watch an expiry
deadline, and `Mount._watched` is already this concept — topics the current render reads — under
this exact word.

## Phase 3 — the bot

- Delete `squid/bot/topics.py` and its import at both call sites.
- `BuildInfoComponent` takes a `sl.resource` that watches its build topic and returns the build
  plus its rendered node; the `reload` closure, the `follow_resource` call and
  `await reload(component)` all go.
- `BuildEditComponent` is the harder one: it also *writes* `self.build` locally when the user
  edits. The resource owns the `(build, node)` pair, and a local edit calls `Resource.replace
  (value)`, which installs the value and re-baselines its sources so a later publish still
  reloads. Do this second, after `search.py` proves the pattern.
- `delivery=ATOMIC` on both: `VISIBLE` is the `sl.resource` default and would flash a pending paint
  on every external change.

## Considered, not done

- **A facade owning both run loops.** [26](26-topic-bus.md) §C3 rejected it: every task has a
  visible owner. Startup keeps `bus.run()` and `reactor.run()` under the host's supervisor.
- **Retiring `reactor.follow`.** It stays the imperative escape hatch for a dependency not
  expressible as a render-time read, and `bus.subscribe` stays first-class for subscribers that are
  not mounts.
- **`TopicKind` / `Vocabulary`.** Phase 1 removes the need: coercion is a type error instead, and
  the codec is total.
- **`watch=` on `sl.resource`.** Rejected above: longer than the statement it sugars, and less
  expressive.
- **A store, or durable subscriptions.** Untouched; [90](90-deferred.md) and
  [40](40-shared-state.md) §3 stand.
- **Renaming `TopicBus` and friends to `Address*`.** Churn; see the wart above.

## Status

Phases 1 and 2 implemented 2026-08-23. Phase 3 (the bot) designed, not started.

One deviation in phase 2, and it made the change smaller. The plan had
`_ResourceDescriptor.__get__` register the bound resource with the current `_CONSUMER`, which
would have put a `Resource` in a consumer's `sources` -- where `_recheck` calls `settle()` on
every key, and a `Resource` has no `settle`. `render_component_tree` already collects exactly
the resources one render touched, through `observe_resources()`, so it passes them to
`observation.addresses(resources)` instead. `resources.py` is untouched, and the hazard never
arises.

`watch()` and its cell live in `topics.py` rather than `runtime/reactivity.py`. Nothing in
reactivity needs to know a topic exists: `Observation.addresses()` already yields
`cell.address` for any addressed cell, and a `_TopicCell`'s address is its `Topic`. That also
keeps the import one-way -- `topics.py` imports `runtime.reactivity`, never the reverse.

`_invalidate` stayed private, so `sl.watch` really is the only callable this phase adds.
