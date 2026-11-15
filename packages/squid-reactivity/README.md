# squid-reactivity

Reactive models with signals, cached computed values, asynchronous values, and transactions.

You declare fields on a model. The package records which fields were read and notifies the
reader when one of them changes. A transaction groups related writes so that all of them
publish or none do.

Access reactive state on one owning thread, normally the event-loop thread. Background
workers must send their results back to that thread before reading or changing reactive state.
Independent threads can each own separate models, buses, subscriptions, and async executions,
including on free-threaded Python. Start each worker with a fresh context so it does not inherit
a caller's transaction, dependency collection, or diagnostic observers:

```python
from contextvars import Context
from threading import Thread

worker = Thread(target=run_worker, context=Context())
worker.start()
worker.join()
```

Here `run_worker` creates and uses its own reactive objects; it can run its own event loop.
Live reactive objects must not cross threads. Copied transactions are rejected, but the package
does not check thread ownership on every object access.

There are no dependencies. The API is alpha and may change before 1.0.

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

`state()` declares a field, and each field is a signal. `@computed` declares a derived value,
which is cached until a reactive value it depends on changes. A `@computed` can depend on
another `@computed`. Reactivity is fine-grained: changing a field affects only what read
that field. Otherwise, a `Reactive` model is a plain object.

Computed values keep separate caches for committed state and each transaction/read policy.
The first read in a transaction evaluates the function to record conflict checks. Returning
to a previous view reuses its cache if valid; closing a transaction discards its caches.

## Transactions

A bare assignment takes effect immediately. Inside `with transaction(...)`, the block sees
its own writes but nothing is published until it exits. If it raises, the writes are
discarded. A nested block is part of the outer transaction, not a savepoint.

A transaction can stay open across an `await`, and another task may write while it waits.
The transaction could then publish a value it calculated from outdated data:

```python
with transaction("Increment counter"):
    seen = counter.count
    await save(seen)  # Meanwhile, another task sets counter.count = 5.
    counter.count = seen + 1  # Would replace that 5 with 1.
```

This block raises `ReactiveConflictError` when it exits and publishes nothing. It read
`count` and wrote `count`, and `count` changed in between. That is the check: every field
the transaction both read and wrote must be unchanged at commit.

A field the transaction only read is not checked. If the block had read `counter.limit` to
decide what to write to `count`, a change to `limit` would go unnoticed. Two blocks change
which reads are checked:

- `with strong_read():` also checks a field you read but do not write, such as `limit`.
- `with relaxed_read():` skips the check for a field you read and write.

A field you write without reading is never checked: the last commit wins.

## Reacting to changes

```python
from squid_reactivity import effect

logging = effect(lambda: print("count is", counter.count))  # Prints "count is 1".
counter.count = 5  # Prints "count is 5".
logging.close()
```

`effect(run)` runs the function now and again whenever a field it read has a new value. A
transaction that writes several of those fields causes one run, after it commits; one that
rolls back causes none. If `run` returns a function, that is its cleanup: it is called before
the next run and when the effect is closed.

An effect may assign fields, for example to keep one model in step with another. If it
assigns a field it also reads, it runs again until a run changes nothing.

`on_change(read, callback)` separates the two halves. Use it when you care about one value
and want to be told only when that value differs:

```python
from squid_reactivity import on_change

listener = on_change(lambda: counter.doubled, print)
counter.count = 6  # Prints 12.
listener.close()
```

`read` is read-only and decides what is watched. `callback` receives the new result, is not
called for the starting value, and cannot assign a field directly: to write, it opens its own
`transaction(...)`.

Both follow the same rules:

- Models hold them weakly. Keep the returned object for as long as it should run, and close
  it when you are done.
- They run after the commit has told its other subscribers and run its `after_commit`
  callbacks, never while the code that made the change still has a transaction open.
- They run one at a time, in the order they were notified. When one of them changes state,
  whatever that affects waits behind those already queued, so each hears about a change
  before it hears about what the change led to.
- One that reruns 100 times for a single change is treated as a loop, logged, and stopped.

### Doing it yourself

`effect` and `on_change` rerun as soon as the commit has been announced. A consumer that
needs to choose when to recompute, such as a renderer that delivers its result later, uses the parts underneath.

`observe_reads()`, used above, yields a `Dependencies` object and adds to it every field the
block reads, directly or through computed values. Use it after the block has finished. It
answers two questions:

- `dependencies.addresses() -> tuple[Any, ...]`: which fields should I listen to?
- `dependencies.current() -> bool`: is what I computed still correct?

A notification says that a field you read was written, not that your result is wrong. If
the block read a computed `parity := count % 2`, changing `count` from 1 to 3 notifies you,
but `parity` is the same and `current()` is still true. Check `current()` when you are
notified, and skip the recomputation if nothing you read has a new value.

To listen, give the resolved addresses (`Topic` or `CellAddress`) to `Subscriptions`:

```python
from squid_reactivity import Subscriptions

subscriptions = Subscriptions(lambda address: print("changed:", address))
subscriptions.stage(dependencies.addresses())
subscriptions.commit()
...
subscriptions.close()
```

`stage` subscribes to a new set of addresses while keeping the old set, and `commit` drops
the old set, so no notification is missed while you recompute. Models hold subscribers
weakly, so keep the subscriptions for as long as you want callbacks.

The block under `observe_reads()` is read-only, as are computed functions: assigning to
existing state raises `ReactiveWriteError`, though an object created there may still set up
its own fields. `capture_reads()` records without that restriction, and `untracked()` hides
reads from the recording.

To save and restore state across restarts, `export_state(model)` returns the model's
persistent fields as a dictionary, and `restore_state(model, values)` assigns them back in
one transaction. Declare a field with `state(persist=False)` to leave it out.

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

`tx.context` is the transaction's `TransactionContext`: its name, its actor, and the earlier
transaction that caused it. `tx.result` is set once the outermost block exits, even if it raised.
It is a `TransactionCommit` listing what was published, or a `TransactionRollback` saying
what was discarded and why.

`after_commit`, `after_rollback`, and `after_result` run a callback with that result. A
callback cannot change the result, even by raising. To write from one, open a new
transaction with `caused_by=result.context` so the two stay linked. `TransactionRelation` marks a
transaction as the undo, redo, or compensation of an earlier transaction.

`observe_events(sink)` is for code that did not start the work. While the block is open, and
in tasks started inside it, the sink receives a snapshot of each transaction result, resource
or operation change, and callback failure. Snapshots omit metadata and exception messages
unless you pass a `RedactionPolicy`. `EventLedger` keeps the most recent ones. For logs or
storage, write your own sink: any object with `accept(event)`.

To inspect transaction validation, use `observe_events(sink, include_validation=True)`.
It additionally delivers a `TransactionValidationSnapshot` after each outcome that reached
validation. Its `preconditions` list opaque target IDs, expected and actual versions, and
the effective origin: `read_write`, `strong_read`, or `explicit`. Explicit conditions take
precedence over strong reads, which take precedence over ordinary reads of written cells.
Versions are sampled at validation, before publication; snapshots retain no cell values or owners.
Empty conditions mean no cell version checks applied. Participant-specific checks are not included.

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
pending again.

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
build_changes.watch()  # Read inside a computed function, resource loader, or observe_reads() block.
build_changes.publish()  # Notify after a database change or incoming bridge message.
```

`watch()` records the dependency; fetching the handle does not. Handles appear in
`dependencies.addresses()` and go to `Subscriptions` like fields do. The same
name on two buses is two topics. A bridge that only has the name can call
`bus.publish(TopicName("build", "42"))` to the same effect.

`LocalTopicBus` calls subscribers synchronously, in subscription order. If one raises, the
rest still run and the error goes to `on_subscriber_error`, which logs by default. For
queues or network delivery, implement the `TopicBus` protocol.

## Modules

Import from the modules below. A module or name that starts with an underscore is private
and can change in any release.

- `squid_reactivity` has what models and their readers need: fields, computed values,
  transactions, read tracking, effects, topics, and the error types.
- `squid_reactivity.transactions` adds patches, participants, causal context, and results.
- `squid_reactivity.diagnostics` has event snapshots, sinks, and redaction.
- `squid_reactivity.resources` and `squid_reactivity.operations` have asynchronous values and
  explicitly started work.
- `squid_reactivity.topics` has buses, codecs, and the address types.
- `squid_reactivity.inspection` reports the versions and dependencies of a model's fields and
  computed values, for debugging tools.
- `squid_reactivity.completion` has `Completion`, an asynchronous result several tasks can await.
- `squid_reactivity.testing` runs callbacks at named points of a commit, to test interleavings.
- `squid_reactivity.internals` exposes storage and execution types to packages that integrate
  closely, such as a UI runtime. It is public to import but carries no stability promise.

Inside the package, `_core` declares models, `_cells` stores versioned values, `_tracking`
captures and checks dependencies, and `_addresses` delivers notifications.
The `transactions` package separates the `with transaction(...)` block (`_scope`), execution
(`_engine`), reversible changes (`_patches`), identity (`_context`), outcomes (`_results`),
and scoped controls (`_state`).
