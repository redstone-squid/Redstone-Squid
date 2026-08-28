# squid-reactivity

Dependency-free transactional reactive state. `squid-ui` components use it through their own
`state`/`computed`/`resource` sugar; import this package directly when you hold reactive state
outside a component, share it across mounts, or observe committed actions.

```python
import squid_reactivity as sr
```

## State and transactions

State mutates only inside a transaction; everything staged commits atomically or rolls back
whole.

::: squid_reactivity.state

::: squid_reactivity.computed

::: squid_reactivity.transaction

::: squid_reactivity.batch

::: squid_reactivity.readonly_transaction

::: squid_reactivity.untracked

::: squid_reactivity.watch

::: squid_reactivity.block_writes

::: squid_reactivity.enlist

::: squid_reactivity.StateOwner

::: squid_reactivity.TransactionView

::: squid_reactivity.TransactionParticipant

::: squid_reactivity.TransactionContribution

## Reads

::: squid_reactivity.relaxed_read

::: squid_reactivity.strong_read

::: squid_reactivity.observe_reads

::: squid_reactivity.Observation

::: squid_reactivity.ObservedRead

## Shared state

One reactive namespace shared across owners, pooled per scope.

::: squid_reactivity.SharedState

::: squid_reactivity.SharedStatePool

::: squid_reactivity.SharedStateFactory

## Topics

Addressed publish/subscribe that lets a write in one process repaint a mount in another.

::: squid_reactivity.Topic

::: squid_reactivity.TopicBus

::: squid_reactivity.LocalTopicBus

::: squid_reactivity.TopicCodec

::: squid_reactivity.KindKeyCodec

::: squid_reactivity.SubscriptionReconciler

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

::: squid_reactivity.apply_local_overwrite_patches

## Actions

An action is one named, attributable unit of work. These names identify the running action,
control its transaction, and describe its outcome.

::: squid_reactivity.action_scope

::: squid_reactivity.current_action

::: squid_reactivity.fresh_action_transaction

::: squid_reactivity.ActionContext

::: squid_reactivity.ActionPurpose

::: squid_reactivity.ActionCommit

::: squid_reactivity.ActionRollback

::: squid_reactivity.ActionContinuation

::: squid_reactivity.ActorRef

::: squid_reactivity.CausalRef

::: squid_reactivity.CommitSequence

::: squid_reactivity.RollbackReason

::: squid_reactivity.ConflictDetail

::: squid_reactivity.ChangeReport

## Observing results

Sinks observe redacted snapshots of committed and rolled-back actions -- the seam for audit
logs and telemetry.

::: squid_reactivity.action_result_sink

::: squid_reactivity.add_action_result_sink

::: squid_reactivity.remove_action_result_sink

::: squid_reactivity.on_action_commit

::: squid_reactivity.on_action_rollback

::: squid_reactivity.on_action_result

::: squid_reactivity.ActionResult

::: squid_reactivity.ActionResultSnapshot

::: squid_reactivity.ActionResultCodec

::: squid_reactivity.ActionLedger

::: squid_reactivity.RedactionPolicy

::: squid_reactivity.DurableResultPolicy

::: squid_reactivity.DurableResultSink

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

::: squid_reactivity.ActionValidationError

::: squid_reactivity.FreshActionError

::: squid_reactivity.FrameworkIntegrityError
