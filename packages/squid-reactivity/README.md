# squid-reactivity

Reactive Python models with transactions, cached computations, and asynchronous values.
The package has no required dependencies and starts no background tasks. Its API is still
in alpha and may change before 1.0.

## Models and transactions

Declare writable fields with `state()` and derived values with `@computed`:

```python
from squid_reactivity import Reactive, computed, observe_reads, state, transaction


class Counter(Reactive):
    count: int = state(0)

    @computed
    def doubled(self) -> int:
        return self.count * 2


counter = Counter()
with transaction("Increment counter"):
    counter.count += 1

with observe_reads() as dependencies:
    assert counter.doubled == 2
```

A `Reactive` model needs no special constructor or rendering method. Share an instance by
passing it to consumers. `squid_ui.Component` extends `Reactive`; its renderer automatically
tracks fields read through models and computed values.

Outside a transaction, assignments publish immediately. Inside one, writes are staged until
commit. Failure before publication discards them. Nested transaction blocks share the outer
transaction and its result.

A publishing transaction checks the versions of strongly read cells before committing. A read
is strong when the transaction also writes that cell, when it occurs inside `strong_read()`, or
when its version is explicitly required with `require_version()`. Other reads do not block commit
by default. `relaxed_read()` opts out of read validation; `untracked()` separately disables
reactive dependency capture.

## Reading and subscribing

`observe_reads()` records dependencies and runs the block read-only. It rejects changes to
existing state but allows a new reactive object to initialize until its constructor returns.
Computed functions also run read-only. Use `capture_reads()` alone when writes should be allowed.

`dependencies.current()` checks whether the observed result is still valid.
`dependencies.addresses()` finds the fields to subscribe to through the current dependency graph.
These are separate checks: a computed value can switch dependencies without changing its result.

Outside the UI runtime, pass those addresses to `SubscriptionReconciler(None, callback)`.
Retain the reconciler and close it when finished. Model fields have identity-based addresses
and need no topic bus. Sources hold subscriptions weakly, so they do not keep abandoned
consumers alive. Keep models in application-owned attributes or dictionaries when they need
to outlive a consumer. For saving and restoration across restarts, use
`squid_storage.PersistentStatePool`.

## Results and diagnostics

`ActionContext` identifies why work ran and what caused it. A transaction's result describes
what it published or discarded: `TransactionCommit` or `TransactionRollback`, together named
`TransactionResult`. The yielded handle exposes its context immediately and its result after
the outer block finishes, even when the block raises.

```python
from squid_reactivity import EventLedger, TransactionCommit, observe_events


def record_commit(result: TransactionCommit) -> None:
    print(result.context.name, len(result.patches))


ledger = EventLedger(limit=100)
with observe_events(ledger):
    with transaction("Increment counter") as tx:
        tx.after_commit(record_commit)
        counter.count += 1

assert isinstance(tx.result, TransactionCommit)
```

`after_commit`, `after_rollback`, and `after_result` receive the completed result. Callback
failures cannot change it. A callback that writes must open a new transaction; use
`transaction("Follow up", caused_by=result.context)` to preserve the causal link.
`ActionRelation` identifies the earlier action targeted by undo, redo, or compensation.

`observe_events(sink, redaction=RedactionPolicy(...))` collects transaction results, operation
and resource transitions, and callback failures. Child tasks inherit the registration, but
collection stops when the scope exits, including in those children. Independent scopes remain
isolated. Metadata is omitted and exception messages are redacted by default.

`EventLedger` retains recent snapshots without retaining live models. It only collects while
registered with `observe_events`. For logging or persistence, implement `EventSink.accept(event)`;
it receives typed, redacted snapshots. Your application owns serialization, storage, and retention.
Transaction callers can still access their full results independently of these observers.

## Asynchronous work

Import `squid_reactivity.resources` for asynchronous values whose loaders track dependencies.
Loads run in the caller's task. The host controls cancellation: install a scope factory with
`abandon_superseded_loads` to stop superseded loads; `anyio.CancelScope` implements the required
`LoadScope` protocol. Without a factory, obsolete loads finish but their results are discarded.
The UI decides when to load and display pending values through `squid_ui.runtime.async_bindings`.

`squid_reactivity.operations` provides repeatable operation definitions. Each invocation creates
one execution with its own causal ID, progress, and terminal status. A retry creates a new execution.

## External changes and topics

Use a topic when a dependency lives outside a reactive model, such as a database record:

```python
from squid_reactivity import LocalTopicBus

bus = LocalTopicBus()
build_changes = bus.topic("build", "42")
build_changes.watch()    # Read inside a render, computed function, or resource loader.
build_changes.publish()  # Notify after a database change or incoming bridge message.
```

Looking up a handle does not record a read; `watch()` does. Handles carry their owning bus and
work with `SubscriptionReconciler(None, callback)`. Equal topic names on different buses are
independent. Bridges use portable `Topic` names: `bus.publish(Topic("build", "42"))` publishes
the same dependency as the handle above. The bus keeps handles weakly.

`LocalTopicBus` delivers synchronously in registration order. Subscriber failures go to
`on_subscriber_error` and do not stop other subscribers; the default handler logs them.
The host owns queues, networking, persistence, and scheduling through the `TopicBus` protocol.

## Code layout

`core` declares models; `tracking` records dependencies; `transactions` stages and publishes
changes. `transaction_results` defines outcomes and reversible changes, while `actions` owns
causal identity. `diagnostics` observes work, and `topics` connects external dependencies.
`resources` and `operations` provide asynchronous values and executions.
