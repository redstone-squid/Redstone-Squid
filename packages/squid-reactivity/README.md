# squid-reactivity

Transactional reactive state for synchronous projections, with no hard dependencies and no
background tasks.

This is an alpha release. The Python API may change before 1.0.

```python
from squid_reactivity import Reactive, computed, observe_reads, state, transaction


class Counter(Reactive):
    count: int = state(0)

    @computed
    def doubled(self) -> int:
        return self.count * 2


counter = Counter()
with transaction():
    counter.count += 1

with observe_reads() as read_set:
    assert counter.doubled == 2
```

`Reactive` is a reusable model, with no required constructor or rendering method. Share an instance
by passing it to consumers. `squid_ui.Component` extends this class and its runtime automatically
observes model fields read during rendering, including reads through nested models and computed
values. Explicit `observe_reads()` users can follow the resulting addresses with
`SubscriptionReconciler(None, callback)`; retain the reconciler and close it when finished.
Sources hold weak subscription handles and cannot keep abandoned consumers alive.

`observe_reads()` combines `capture_reads()` with `read_only()`. Capture records direct sources
and their versions; it does not restrict writes. A read-only scope rejects changes to existing
state but permits initializing a new reactive object until its constructor returns. Computed
functions also run read-only. `Dependencies.current()` checks result validity, while
`Dependencies.addresses()` follows the current upstream graph for subscriptions: a computed
can change its dependencies without changing its value.
Retain models in application-owned attributes or dictionaries when they should outlive a consumer.
For restoration and saving across restarts, use `squid_storage.PersistentStatePool`.

The package is layered:

- `squid_reactivity.actions` provides sortable action IDs, causal contexts, immutable terminal
  outcomes, and the current causal context.
- `squid_reactivity.diagnostics` provides scoped diagnostic observers, redaction, event
  ledgers, and portable event encoding.
- `squid_reactivity.core` provides state cells, computed values, full strong-read OCC,
  version-conditional patches, staged transaction participants, and the reusable `Reactive` owner.
- Every `Reactive` field has an identity address. Consumers subscribe directly without a host-supplied bus.
- `squid_reactivity.topics` provides portable `Topic` names, bus-owned `TopicHandle` dependencies, and the small
  synchronous `TopicBus` protocol, `LocalTopicBus`, and committed/staged subscription
  reconciliation.
- `squid_reactivity.resources` is an optional import for tracked asynchronous values. It uses
  only the standard library and runs loads in the caller's task. Cancellation is the host's:
  `abandon_superseded_loads` installs a `LoadScope` factory -- `anyio.CancelScope` satisfies the
  protocol as it stands -- and a superseded generation is then stopped rather than run to
  completion. Uninstalled, it runs on and only its result is discarded.
- `squid_reactivity.operations` separates repeatable definitions from causally identified one-shot
  executions; every retry receives a fresh execution ID and terminal status.

A publishing transaction validates every strongly read addressed cell immediately before its
prepare/apply commit point. A read is strong when the action also writes that cell, when it was
taken inside `strong_read()`, or when it was pinned with `require_version()`; a read-only read is
not validated by default. `relaxed_read()` opts a read out of that validation;
`untracked()` independently opts out of dependency capture. Commit and rollback aftermath hooks are
failure-isolated and cannot mutate through a completed transaction. Recovery starts a new causal action.

An action is an identified transaction. The yielded handle exposes its context immediately
and its `result` after the outer block finishes, including when the block raises. Nested
transactions share the outer handle.

```python
from squid_reactivity import ActionCommit, EventLedger, observe_events


def record_commit(result: ActionCommit) -> None:
    print(result.context.name, len(result.patches))


ledger = EventLedger(limit=100)
with observe_events(ledger):
    with transaction("Increment counter") as tx:
        tx.after_commit(record_commit)
        counter.count += 1

assert isinstance(tx.result, ActionCommit)
```

`after_commit`, `after_rollback`, and `after_result` receive one completed result. A callback
that needs to write opens `transaction("Follow up", caused_by=result.context)`; it cannot
write directly into a completed transaction. `ActionRelation` separately describes which
historical action an undo, redo, or compensation targets.

`observe_events(sink, redaction=RedactionPolicy(...))` observes actions, operations, resource
loads, and callback failures in its execution scope. Child tasks inherit the registration;
leaving the scope disables it in those tasks too. Independent scopes do not see each other's
work. The observer applies its policy before delivery, omitting metadata and redacting exception messages
by default. Rich transaction results remain available to their caller independently of observers.

`EventLedger` stores recent events without registering itself. `EncodedEventSink(EventCodec(), append)`
encodes every event variant and calls an application-owned writer; the application controls storage
and retention. Every event uses the same envelope; an action carries its history relation
as one optional `relation` object.

`LocalTopicBus.publish()` delivers synchronously in registration order. If a subscriber raises,
the bus reports it through `on_subscriber_error`, continues delivering to the remaining
subscribers, and never lets the subscriber kill the bus. The default hook logs the exception.

The bus deliberately has no queue, run loop, durability, expiry, or bridge policy. A real host
implements the `TopicBus` protocol and owns any loops needed to connect processes or
schedule asynchronous projection refreshes.

Obtain a named dependency from its owning bus and pass the handle to readers:

```python
from squid_reactivity import LocalTopicBus

bus = LocalTopicBus()
build_changes = bus.topic("build", "42")
# Inside a render, computed value, or resource loader:
build_changes.watch()
# After a database change or an incoming bridge notification:
build_changes.publish()
```

Looking up a handle does not record a read. Equal names on different buses refer to independent
dependencies. Observed handles carry their bus and can be followed with
`SubscriptionReconciler(None, callback)`. Bridges keep using portable `Topic` names;
`bus.publish(Topic("build", "42"))` reaches the same dependency as `build_changes.publish()`.
The bus holds handles weakly, so unused names do not accumulate reactive state.
