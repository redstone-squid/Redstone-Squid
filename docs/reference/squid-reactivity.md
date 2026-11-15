# squid-reactivity

Dependency-free transactional reactive state. `squid-ui` components use it through their own
`state`/`computed`/`resource` sugar; import this package directly when you hold reactive state
outside a component, share it across mounts, or observe committed actions.

```python
import squid_reactivity as sr
```

## State and transactions

Assignments outside a transaction publish immediately. Inside a transaction, assignments stage
and publish together on success; failure discards them.

::: squid_reactivity.state

::: squid_reactivity.computed

::: squid_reactivity.transaction

::: squid_reactivity.readonly_transaction

::: squid_reactivity.untracked

::: squid_reactivity.watch

::: squid_reactivity.Signal

::: squid_reactivity.transactions.block_writes

::: squid_reactivity.transactions.enlist

::: squid_reactivity.Reactive

::: squid_reactivity.transactions.TransactionView

::: squid_reactivity.transactions.TransactionParticipant

::: squid_reactivity.transactions.TransactionContribution

## Reads

::: squid_reactivity.relaxed_read

::: squid_reactivity.strong_read

::: squid_reactivity.observe_reads

::: squid_reactivity.capture_reads

::: squid_reactivity.transactions.read_only

::: squid_reactivity.Dependencies

## Topics

Optional invalidation for external dependencies, such as database records or files.
The application obtains a handle with `bus.topic("build", "42")` and passes it to readers.
A resource calls `handle.watch()`; after a committed database change, `handle.publish()`
notifies its consumers. Looking up a handle does not track a read. Equal names on different
buses are independent. Bridges use portable `TopicName` values and publish into their local bus.
Reactive objects notify readers directly and need no topic bus. `LocalTopicBus` operates in one process; a host-owned
bridge can publish events received from another process.

::: squid_reactivity.TopicName

::: squid_reactivity.Topic

::: squid_reactivity.TopicBus

::: squid_reactivity.LocalTopicBus

::: squid_reactivity.topics.TopicCodec

::: squid_reactivity.topics.DelimitedTopicCodec

::: squid_reactivity.on_change

::: squid_reactivity.ChangeListener

::: squid_reactivity.Subscriptions

## Addresses and cell inspection

`DependencyAddress` is a resolved `Topic` or `CellAddress`. Tracking and subscriptions use
this type. `BusAddress` also accepts `TopicName`, which the receiving bus resolves.

::: squid_reactivity.DependencyAddress

::: squid_reactivity.topics.BusAddress

::: squid_reactivity.CellAddress

::: squid_reactivity.addresses

::: squid_reactivity.inspection.inspect_cells

::: squid_reactivity.inspection.inspect_computed

::: squid_reactivity.export_state

::: squid_reactivity.restore_state

::: squid_reactivity.inspection.CellReport

::: squid_reactivity.inspection.ComputedReport

## Patches

Apply targeted cell changes, optionally conditioned on what the writer last saw.

::: squid_reactivity.transactions.CellPatch

::: squid_reactivity.transactions.CellPatchSet

::: squid_reactivity.transactions.ConditionalCellPatch

::: squid_reactivity.transactions.apply_conditional_patches

## Scoped execution state

Field reads need implicit access to their transaction and dependency collector. Other work
should carry its origin explicitly: use `operation.start(caused_by=tx.context)` for an operation
initiated by a transaction. `start()` alone creates an unlinked execution. Resource generations
capture the current causal origin because resource access happens through descriptors.

The remaining context variables are grouped by responsibility, with ownership and inheritance
rules documented at each declaration:

| Module | Scoped state | Lifetime |
|---|---|---|
| `transactions._state` | Active transaction, read policy, write restriction | Transaction body or nested read scope; test interception is separate. |
| `_tracking` | Dependency collector | Computation, resource load, or render. |
| `_evaluation` | Evaluation stack for cycle detection | Computed value, resource load, or operation. |
| `transactions._context` | Immutable causality and callback write guard | Transaction publication, operation execution, or result callback. |
| `_async_values` | Observed async bindings | One render expansion. |
| `resources` | Host cancellation-scope factory | Host execution, inherited by nested resource awaits. |
| `diagnostics._observers` | Event registrations | Observation scope; exit disables inherited registrations too. |

Child tasks inherit context values, not independent copies of mutable objects. A transaction
still checks its owning task before accepting writes. Causal references can outlive the
transaction; they do not grant access to its overlay or make it active again.

## Transaction context and results

`TransactionContext` identifies why work ran and what caused it. `transaction(name, actor=..., caused_by=...)`
yields a `Transaction` whose context is available immediately and whose result is set when the outer
block finishes. Nested blocks share the handle. Callbacks receive a `TransactionCommit` or
`TransactionRollback`; related operations and resource loads retain their own lifetimes and results.

::: squid_reactivity.current_transaction

::: squid_reactivity.transactions.fresh_transaction

::: squid_reactivity.transactions.TransactionContext

::: squid_reactivity.transactions.TransactionPurpose

::: squid_reactivity.TransactionCommit

::: squid_reactivity.TransactionRollback

::: squid_reactivity.Transaction

::: squid_reactivity.transactions.TransactionRelation

::: squid_reactivity.transactions.ActorRef

::: squid_reactivity.transactions.CausalRef

::: squid_reactivity.transactions.CommitSequence

::: squid_reactivity.transactions.RollbackReason

::: squid_reactivity.transactions.ConflictDetail

::: squid_reactivity.transactions.ChangeReport

## Observing results

Observer scopes deliver diagnostic snapshots for actions, operations, resource loads, and
callback failures. Each scope owns its registration and redaction policy. Tasks inherit active
registrations, but delivery stops when the owning scope exits. Ledgers and application-defined
sinks do not register themselves; applications own serialization, storage, and retention.

::: squid_reactivity.observe_events

::: squid_reactivity.diagnostics.EventSink

::: squid_reactivity.Transaction.after_commit

::: squid_reactivity.Transaction.after_rollback

::: squid_reactivity.Transaction.after_result

::: squid_reactivity.TransactionResult

::: squid_reactivity.diagnostics.PreconditionSnapshot

::: squid_reactivity.diagnostics.TransactionValidationSnapshot

::: squid_reactivity.diagnostics.TransactionResultSnapshot

::: squid_reactivity.EventLedger

::: squid_reactivity.diagnostics.RedactionPolicy

::: squid_reactivity.diagnostics.CausalEventSnapshot

::: squid_reactivity.diagnostics.OperationEventSnapshot

::: squid_reactivity.diagnostics.ResourceEventSnapshot

::: squid_reactivity.diagnostics.ContinuationFailureSnapshot

::: squid_reactivity.transactions.ExceptionReport

## Errors

Every deliberate failure derives from `ReactivityError` alongside its stdlib base.

::: squid_reactivity.ReactivityError

::: squid_reactivity.ReactiveWriteError

::: squid_reactivity.ReactiveConflictError

::: squid_reactivity.ReactiveCycleError

::: squid_reactivity.StaleReactiveContextError

::: squid_reactivity.UndeclaredStateError

::: squid_reactivity.TransactionValidationError

::: squid_reactivity.FreshTransactionError

::: squid_reactivity.FrameworkIntegrityError
