# Action ledger, history, and replicated state

Squid treats the transaction outcome as the source of truth. History, DevTools, profiling, audit
sinks, and optional replicated state consume that truth; none defines whether an action committed.

## Commit and rollback pipeline

A Discord dispatch receives an `ActionContext` before middleware. A local transaction then:

1. stages reactive and participant work in an isolated overlay;
2. closes staging and enters the runtime-local, no-await commit gate;
3. validates every strong shared/replicated read and explicit inverse precondition by version --
   strong meaning a cell the action also wrote, or one read inside `strong_read()`;
4. freezes `CellPatchSet` and `TransactionView`, then prepares every participant without publication;
5. installs cell patches and synchronously applies every prepared participant—the commit point;
6. releases the gate, notifies reactive owners, finalizes participants, emits one immutable outcome,
   and runs failure-isolated aftermath hooks.

Failure before publication aborts prepared participants in reverse order and emits one
`ActionRollback`. A handler exception, cancellation, conflict, and prepare failure publish nothing.
Participant `apply()` is infallible by contract; an exception there is framework-integrity damage,
is tagged on the committed diagnostic outcome, and fails loudly.

The gate and `CommitSequence` belong to one runtime. They do not promise cross-process atomicity or a
distributed total order. Network receive, authentication, limits, decoding, storage, and transport I/O
stay outside the gate.

## Register history

An ordinary component-local action records its whole successful commit once:

```python
class Editor(sl.Component):
    history: sl.runtime.History = sl.runtime.history(limit=20)
    title: str = sl.state("")

    async def rename(self, event: sl.PressEvent) -> None:
        self.title = "Squid"

    def render(self):
        return sl.actions(
            sl.action("Rename", self.rename, key="rename", record=self.history),
            sl.runtime.history_actions(self.history),
            key="editor",
        )
```

Modal submissions use the same declarative binding; the entry is reserved only when the parsed submission
enters its transaction, not when the form-opening button is pressed:

```python
sl.form(
    "Rename",
    rename_form,
    key="rename",
    on_submit=self.rename,
    record=self.history,
)
```

Undo is a new `UNDO` action. Its conditional patch requires the exact version written by the original
action. If a sibling changes the same `Shared` register, the result is a conflict and neither target
changes:

```python
result = await editor.history.undo()
match result.status:
    case sl.runtime.HistoryResultStatus.APPLIED:
        ...
    case sl.runtime.HistoryResultStatus.CONFLICT:
        explain(result.conflict)
```

Later writes elsewhere survive. Redo is the inverse of the committed undo, so an intervening same-slot
write makes redo conflict rather than overwrite it. A conflicted entry remains inspectable and may be
explicitly removed with `drop_conflicted()`.

A sibling write to a `Shared` register demonstrates the conditional rule directly:

```python
with transaction():
    history.record("Select project")
    workspace.selected = "a"

with transaction():
    workspace.selected = "b"

result = await history.undo()
assert result.status is HistoryResultStatus.CONFLICT
assert workspace.selected == "b"
```

## Semantic replicated inverse

`state()` and `Shared` remain transactional registers. The optional `squid-replicated` package exposes
immutable snapshots plus semantic methods:

```python
scope = ReplicatedScope("replica-a")
document = scope.open("project-7")

with transaction():
    history.record("vote and tag")
    document.counter("votes").increment(2)
    document.set("tags").add("mine")

other = ReplicatedScope("replica-b").open("project-7")
other.import_update(document.export_since())
with transaction():
    other.counter("votes").increment(3)
    other.set("tags").add("theirs")
document.import_update(other.export_since())

# selective undo leaves the other replica's contributions
result = await history.undo()
assert result.status is HistoryResultStatus.APPLIED
assert document.counter("votes").value == 3
assert document.set("tags").value == frozenset({"theirs"})
```

The fake conformance backend retains operation identities. Its inverse decrements only this action's
counter contribution and removes only this action's set tag, preserving another replica's increments
and tags. A mixed local/register/replicated inverse prepares all changes together and publishes none if
any participant conflicts.

Remote bytes are checked and decoded before admission, then imported as a `REMOTE` action under the same
gate. A local action that read document version 10 and intends to publish state conflicts if a remote
import advances the document before local commit—even though the backend can merge the document data.
Convergence does not prove that the business decision derived from version 10 is still valid.

For example, a receiver admitted outside the local transaction can land between its read and commit:

```python
# local decision action
with transaction():
    if target.counter("votes").value < 10:  # strong document-version read
        receiver_checkpoint()              # the receiver imports an envelope here
        panel.accepted = True               # commit raises ReactiveConflictError

assert panel.accepted is False
```

The deterministic test harness drives that checkpoint in tests; production receivers decode and route
the envelope before entering the same synchronous commit gate.

The Loro 1.13.2 and pycrdt 0.14.2 extras are conformance spikes. Both selectively reverse a non-latest
text insertion while preserving later local and remote insertions, converge under reordered delivery,
and reload their action token. Neither adapter is production-ready. The
[conclusive backend report](plans/68-replicated-backend-report.md) selects Loro for generalized backend
hardening and records the remaining register-conflict, compaction, staging-cost, and ownership gates.

The collaborative-text spike targets an action token, not “the latest local edit”:

```python
engine = LoroTextEngine()
first = engine.branch()
first.apply(LoroTextOperation("insert", 0, "A"))
token = engine.apply(first.prepare(engine.version()))

later = engine.branch()
later.apply(LoroTextOperation("insert", 1, "B"))
engine.apply(later.prepare(engine.version()))
engine.apply(engine.plan_inverse(LoroChangeToken.decode(token.encode())))

assert engine.snapshot() == "B"
```

This is evidence for the experimental adapter only. It is deliberately not exposed as a production
`ReplicatedDocument.text()` until the remaining backend gate passes.

## Compensation is not rollback

External I/O cannot join an in-memory commit unless the service offers a real prepare protocol.
Record an idempotent compensator instead:

```python
history.record(
    "Create channel",
    compensate=sl.runtime.CompensationSpec(
        operation=delete_channel,
        idempotency_key=lambda commit: f"undo:{commit.context.action_id}",
    ),
)
```

Each retry receives a fresh execution ID and the same idempotency key. External failure is `FAILED`.
External success followed by a local inverse conflict is `NEEDS_RECONCILIATION`; Squid never labels
either path an atomic rollback. Durable applications persist the intent and dispatch it through their
own transactional outbox.

```python
first = await history.undo()
assert first.status is HistoryResultStatus.FAILED

second = await history.undo()  # new execution, same idempotency key
assert second.status in {HistoryResultStatus.APPLIED, HistoryResultStatus.NEEDS_RECONCILIATION}
```

## Aftermath and recovery

Hooks observe an outcome after the transaction is closed. Direct reactive mutation is rejected. A
recovery is another causally linked action:

```python
def present_error(rollback, aftermath) -> None:
    with aftermath.start_action("Present conflict"):
        panel.error = rollback.reason.value

with transaction():
    on_action_rollback(present_error)
    change_project()
```

A hook cannot suppress the original exception, turn a commit into a rollback, or write through the dead
transaction. Async consequences use an owned operation or an application outbox; returning an un-awaited
coroutine from a hook is an error.

## Retention and privacy

`ActionCommit` is an ephemeral in-process event and may carry opaque inverse handles. The bounded
`ActionOutcomeSnapshot` and JSON schema 1 retain only stable IDs, causality, times, terminal status,
safe tags, and change counts. They retain no values, owners, mutable backend objects, closures,
tracebacks, or arbitrary `repr()`. Unknown schemas are rejected. A durable application must separately
define codecs, redaction, actor privacy, access, encryption, retention, and deletion policy. Use
`DurableOutcomeSink` with a `DurableOutcomePolicy` to make those host decisions inspectable; values remain
summary-only in schema 1.

DevTools owns a bounded ledger and exposes it with `dev ui actions [limit]`; it remains available when
profiler sampling is disabled or detailed traces have been evicted.

See [the breaking migration](plan68-migration.md) for direct old/new call-site examples.
