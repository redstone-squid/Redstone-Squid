# Plan 68 breaking migration

Plan 68 is one coordinated breaking cutover. There is no compatibility mode: obsolete signatures fail
at the call site, and pre-cutover in-memory history entries have no safe lineage to import.

## Outcome hooks

Before, commit hooks received a value-only delta and could restore it blindly:

```python
def committed(delta: StateDelta) -> None:
    audit(delta)

on_action_commit(committed)
```

After, hooks receive the immutable terminal commit and aftermath authority. Recovery is a new action:

```python
def committed(commit: ActionCommit, aftermath: Aftermath) -> None:
    audit(commit.context.action_id, commit.patches)

on_action_commit(committed)
on_action_rollback(lambda rollback, aftermath: report(rollback.reason))
```

`StateDelta`, `restore_before()`, and `restore_after()` no longer exist. Use `CellPatchSet` for lineage,
`ActionResultSnapshot` for safe retention, and `UndoPlan` through `History` for inversion.

## History and conflicts

Before, raw closures and blind restore made an external effect appear atomic with state:

```python
history.record("Create channel", undo=delete_channel, redo=create_channel)
await history.undo()  # callers assumed success
```

After, external work is an idempotent compensation execution and every caller handles its typed result:

```python
history.record(
    "Create channel",
    compensate=CompensationSpec(
        operation=delete_channel,
        idempotency_key=lambda commit: f"undo:{commit.context.action_id}",
    ),
)

result = await history.undo()
if result.status is HistoryResultStatus.CONFLICT:
    show_conflict(result.conflict)
elif result.status is HistoryResultStatus.NEEDS_RECONCILIATION:
    queue_manual_reconciliation(result.entry)
```

The default changed from “undo always restores” to “undo preserves later work or reports conflict.”
`UndoMode.LOCAL_OVERWRITE` is the named escape hatch for ephemeral component-local registers; it
rejects `Shared` and participant changes. Redo is freshly based on the committed undo and may conflict
after another same-target write.

Declarative action recording is now `record=history`. A handler may instead call
`history.record(label, compensate=..., strategy=...)` once, but the same action cannot use both forms.

## Conflicts and participants

Catch `ReactiveConflictError`, whose `detail` identifies the expected and current target version. The
removed `SharedStateConflictError` name is not aliased.

Transaction participants now prepare against a staged, read-only view:

```python
class Participant:
    def prepare(self, view: TransactionView) -> Prepared:
        staged_mode = view.read_staged(mode_target)
        return prepare_without_publication(staged_mode)
```

`prepare()` may reject the action. `apply()` is synchronous and infallible after publication begins.
Post-commit publication, telemetry, and presentation belong in `finalize()` or an aftermath hook.

## Operations

An operation descriptor is a repeatable definition, not a permanently bound one-shot awaitable:

```python
execution = panel.publish.start()
result = await execution

retry = panel.publish.start()  # fresh execution ID and status
result = await retry
```

Every operation completion that publishes domain state starts a fresh action with
`execution.start_action(...)`. Detached work retains only the operation/action causal token, never a
live transaction context.

## Replicated state

Do not add CRDT behavior to `state()` or `Shared`. Use the optional `squid-replication` package for
immutable snapshots and semantic mutations. Transport messages are `ReplicatedUpdate` envelopes with
document/backend/schema/source/action identity and a verified payload hash. Loro and pycrdt text engines
remain experimental. Loro is the selected generalized-backend hardening direction, while its production
promotion and the failing rows are recorded in
[the conclusive backend report](plans/68-replicated-backend-report.md).

## Durable records

Portable outcome and compensation formats start at schema version 1. Unknown, corrupt, and oversized
records are rejected; there is no unsafe value or token fallback. A durable outcome sink declares its
policy explicitly:

```python
sink = DurableOutcomeSink(
    store.append,
    policy=DurableOutcomePolicy(
        redaction=RedactionPolicy(include_actor=False),
        actor_privacy="omitted",
        encryption="application KMS",
        retention="30 days",
    ),
)
```

Closing the sink unregisters it. Durable replicated history must reload a valid backend token; an expired
compaction epoch produces a conflict and leaves application state unchanged.
