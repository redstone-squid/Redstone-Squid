# squid-reactivity

State that knows who is reading it. You declare fields on a model, the package records which
fields a piece of code read, and it tells that code when one of them changes. A transaction
groups related writes so that all of them publish or none do.

There are no required dependencies. The API is alpha and may change before 1.0.

## Models

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

`state()` declares a field you can assign. `@computed` declares a derived value, which is
cached until something it read changes.

Otherwise a model is a plain object: write any `__init__` you like, and share an instance by
passing it around. `squid_ui.Component` is a `Reactive` too, and its renderer tracks what
each render reads.

## Transactions

A bare assignment takes effect immediately. Inside `with transaction(...)`, the block sees
its own writes but nothing is published until it exits. If it raises, the writes are
discarded. A nested block is part of the outer transaction, not a savepoint.

A commit fails with `ReactiveConflictError`, publishing nothing, if a field it depended on
changed in the meantime. A transaction depends on the fields it both read and wrote, so
`counter.count += 1` is protected and a field you only looked at is not. To adjust that:

- Read inside `strong_read()` to depend on a field you do not write.
- Read inside `relaxed_read()` to not depend on a field you do write.

A write with no read overwrites: the last commit wins.

## Reacting to changes

`observe_reads()`, used above, records every field its block reads, directly or through
computed values. The result answers two questions:

- `dependencies.addresses()`: which fields should I listen to?
- `dependencies.current()`: is what I computed still right?

These differ. If the block read a computed `parity` of `count`, changing `count` from 1 to 3
notifies you, but `current()` is still true and there is nothing to redo.

To listen, give the addresses to a `SubscriptionReconciler`:

```python
from squid_reactivity import SubscriptionReconciler

subscriptions = SubscriptionReconciler(None, lambda address: print("changed:", address))
subscriptions.stage(dependencies.addresses())
subscriptions.commit()
...
subscriptions.close()
```

`stage` subscribes to a new set of addresses while keeping the old set, and `commit` drops
the old set, so no notification is missed while you recompute. Models hold subscribers
weakly, so keep the reconciler for as long as you want callbacks. The UI runtime does all of
this for component renders.

The block under `observe_reads()` is read-only, as are computed functions: assigning to
existing state raises `ReactiveWriteError`, though an object created there may still set up
its own fields. `capture_reads()` records without that restriction, and `untracked()` hides
reads from the recording.

To save and restore state across restarts, see `squid_storage.PersistentStatePool`.

## Finding out what happened

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

`tx.context` is the transaction's `ActionContext`: its name, its actor, and the earlier
action that caused it. `tx.result` is set once the outermost block exits, even if it raised.
It is a `TransactionCommit` listing what was published, or a `TransactionRollback` saying
what was discarded and why.

`after_commit`, `after_rollback`, and `after_result` run a callback with that result. A
callback cannot change the result, even by raising. To write from one, open a new
transaction with `caused_by=result.context` so the two stay linked. `ActionRelation` marks a
transaction as the undo, redo, or compensation of an earlier action.

`observe_events(sink)` is for code that did not start the work. While the block is open, and
in tasks started inside it, the sink receives a snapshot of each transaction result, resource
or operation change, and callback failure. Snapshots omit metadata and exception messages
unless you pass a `RedactionPolicy`. `EventLedger` keeps the most recent ones. For logs or
storage, write your own sink: any object with `accept(event)`.

## Asynchronous values

```python
from squid_reactivity.resources import resource


class BuildPage(Reactive):
    build_id: int = state(42)

    @resource
    async def title(self) -> str:
        return await fetch_title(self.build_id)
```

`page.title.status` is `Pending`, `Ready`, or `Failed`. Reading it never starts I/O;
`await page.title` runs the loader in your task. Changing `build_id` makes the resource
pending again. In the UI, `squid_ui.runtime.async_bindings` decides when to load.

A loader can run several times and an outdated result is thrown away, so keep side effects
out of it. An outdated load still runs to the end unless you load inside
`abandon_superseded_loads(anyio.CancelScope)`, which cancels it.

For work with side effects, declare an `@operation` from `squid_reactivity.operations`. Each
`start()` creates a separate execution with its own progress, outcome, and causal ID.

## Things outside your models

A topic stands for something reactive code depends on but cannot read as a field, such as a
database row:

```python
from squid_reactivity import LocalTopicBus

bus = LocalTopicBus()
build_changes = bus.topic("build", "42")
build_changes.watch()    # Read inside a render, computed function, or resource loader.
build_changes.publish()  # Notify after a database change or incoming bridge message.
```

`watch()` records the dependency; fetching the handle does not. Handles appear in
`dependencies.addresses()` and go to a `SubscriptionReconciler` like fields do. The same
name on two buses is two topics. A bridge that only has the name can call
`bus.publish(Topic("build", "42"))` to the same effect.

`LocalTopicBus` calls subscribers synchronously, in subscription order. If one raises, the
rest still run and the error goes to `on_subscriber_error`, which logs by default. For
queues or network delivery, implement the `TopicBus` protocol.

## Code layout

`core` declares models, `tracking` records reads, and `transactions` stages and publishes
writes. `transaction_results` defines results, `actions` defines contexts, and `diagnostics`
delivers events. `topics`, `resources`, and `operations` cover the last two sections.
