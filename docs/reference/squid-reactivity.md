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

::: squid_reactivity.batch

::: squid_reactivity.readonly_transaction

::: squid_reactivity.untracked

::: squid_reactivity.watch

::: squid_reactivity.block_writes

::: squid_reactivity.enlist

::: squid_reactivity.Reactive

::: squid_reactivity.TransactionView

::: squid_reactivity.TransactionParticipant

::: squid_reactivity.TransactionContribution

## Reads

::: squid_reactivity.relaxed_read

::: squid_reactivity.strong_read

::: squid_reactivity.observe_reads

::: squid_reactivity.capture_reads

::: squid_reactivity.read_only

::: squid_reactivity.Dependencies

::: squid_reactivity.ObservedRead

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

::: squid_reactivity.TopicCodec

::: squid_reactivity.DelimitedTopicCodec

::: squid_reactivity.Subscriptions

## Addresses and cell inspection

::: squid_reactivity.Address

::: squid_reactivity.CellAddress

::: squid_reactivity.addresses

::: squid_reactivity.inspect_cells

::: squid_reactivity.inspect_computed

::: squid_reactivity.export_state

::: squid_reactivity.restore_state

::: squid_reactivity.CellReport

::: squid_reactivity.ComputedReport

## Patches

Apply targeted cell changes, optionally conditioned on what the writer last saw.

::: squid_reactivity.CellPatch

::: squid_reactivity.CellPatchSet

::: squid_reactivity.ConditionalCellPatch

::: squid_reactivity.apply_conditional_patches

## Transaction context and results

`TransactionContext` identifies why work ran and what caused it. `transaction(name, actor=..., caused_by=...)`
yields a `Transaction` whose context is available immediately and whose result is set when the outer
block finishes. Nested blocks share the handle. Callbacks receive a `TransactionCommit` or
`TransactionRollback`; related operations and resource loads retain their own lifetimes and results.

::: squid_reactivity.current_transaction_context

::: squid_reactivity.fresh_transaction

::: squid_reactivity.TransactionContext

::: squid_reactivity.TransactionPurpose

::: squid_reactivity.TransactionCommit

::: squid_reactivity.TransactionRollback

::: squid_reactivity.Transaction

::: squid_reactivity.TransactionRelation

::: squid_reactivity.ActorRef

::: squid_reactivity.CausalRef

::: squid_reactivity.CommitSequence

::: squid_reactivity.RollbackReason

::: squid_reactivity.ConflictDetail

::: squid_reactivity.ChangeReport

## Observing results

Observer scopes deliver diagnostic snapshots for actions, operations, resource loads, and
callback failures. Each scope owns its registration and redaction policy. Tasks inherit active
registrations, but delivery stops when the owning scope exits. Ledgers and application-defined
sinks do not register themselves; applications own serialization, storage, and retention.

::: squid_reactivity.observe_events

::: squid_reactivity.EventSink

::: squid_reactivity.Transaction.after_commit

::: squid_reactivity.Transaction.after_rollback

::: squid_reactivity.Transaction.after_result

::: squid_reactivity.TransactionResult

::: squid_reactivity.TransactionResultSnapshot

::: squid_reactivity.EventLedger

::: squid_reactivity.RedactionPolicy

::: squid_reactivity.CausalEventSnapshot

::: squid_reactivity.OperationEventSnapshot

::: squid_reactivity.ResourceEventSnapshot

::: squid_reactivity.ContinuationFailureSnapshot

::: squid_reactivity.ExceptionReport

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
