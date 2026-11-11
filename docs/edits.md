# Edits, revisions, and history

Several features change stored records on behalf of a user and need to know whether
someone else changed the record first. Some of them also let the user undo the change,
and builds keep a permanent log of every change. These are separate mechanisms that
share vocabulary. This page defines each term and shows how the pieces fit.

| Term | Meaning | Where |
|---|---|---|
| Revision | A counter on a stored record that increases with every change | `revision` columns, `squid/persistence/revisions.py`, `squid/edits/domain.py` |
| Edit | One change requested by a user, checked against revisions and committed in one transaction | `squid/edits` |
| Session history | The undo and redo stack of one open editor | `squid_ui.runtime.History`, `squid/bot/edit_controller.py` |
| Build history | A permanent log of every change to a build | `build_edit_history`, `squid/builds/history.py` |

## Revisions

A *revision* is an integer column on a record that changes on every write. Readers
remember the revision they displayed, and a writer sends it back as the *expected
revision*. If the stored revision differs, someone else changed the record in the
meantime, and the write fails with `RevisionConflictError` instead of overwriting
their change.

An *edit target* (`EditTarget`) names one versioned record, such as
`EditTarget("build", "42")`. Its `identity` string, `"build:42"`, identifies the target in session
history. A `Revision` pairs a target with a counter value.

Revisions advance in one of three ways:

- **Changed columns.** Most records use the `Revisioned` mixin, which sets SQLAlchemy's
  `version_id_col`. The ORM increases the counter on each flush that changes a column
  and adds the expected value to the `UPDATE`. A concurrent write then matches no row,
  which the unit of work and `flush_revision` report as `ConcurrentEditError`.
- **Child rows or direct SQL.** The mapper sees no change to the parent row, so the
  writer increases the parent's counter itself: `flush_revision` in edit handlers,
  `revision += 1` or `revision = revision + 1` in repositories.
- **Builds.** `Build` sets `version_id_col` in its own mapper arguments, because they
  also hold `polymorphic_on`. In addition, the build history trigger advances the
  revision at commit when the build's snapshot changed and nothing else advanced it.
  Code that must return the new revision before commit, such as edit handlers,
  still advances it explicitly.

Submission drafts start at revision 0 rather than 1 and do not use `version_id_col`.
Every draft writer advances the counter itself.

HTTP clients receive revisions as ETags such as `"build-42-r7"` and return them in
`If-Match` (`squid/api/revisions.py`). A request without `If-Match` applies to the
current record: the command carries a `LatestRevision`, and the handler reports
whatever revision it finds after locking the record.

## Edits

An edit moves through these steps:

1. A transport (a bot screen or an HTTP route) builds an `EditCommand`. It names the
   handler (`kind`), the expected revision of each target, and the requested values.
   Each command has an `operation_id`.
2. `EditExecutor.execute` opens a transaction and takes an advisory lock on the
   operation ID. If an `edit_operations` row already exists for that ID, the command
   is a retry: the executor returns the stored outcome instead of applying it again.
3. The handler's `prepare` locks the targets, checks permissions, and returns the
   values before and after the change with the current revision of each target. The
   executor rejects the edit if a revision differs from the command's expectation;
   for `LatestRevision`, any current value is accepted.
4. If the values differ, `apply` writes them and returns the new revisions.
5. The executor stores an `EditOutcome` (operation ID, actor, new revisions) in
   `edit_operations` and commits.

The caller receives an `EditReceipt` containing the values before and after the change.
Receipts are kept only in memory; the database does not store the old values. Outcome
rows expire after 24 hours, and the executor rejects commands issued more than 24
hours ago, so a retry cannot apply an edit twice after its outcome has been deleted.

If the commit fails in a way that may or may not have saved the change, the executor
raises `EditCommitUncertainError`. The bot keeps such an edit as *pending* and blocks
further edits to its targets until the user presses "Check saved result", which calls
`EditExecutor.reconcile` to find out whether the outcome row exists.

The handlers are registered by kind in `squid/bootstrap.py` (`_ServiceGraph.edits`).
Most are `FieldCommand` subclasses (`squid/edits/fields.py`), which edit the fields
declared by a Pydantic model on one record.

## Session history

Bot editors keep an undo and redo stack for the lifetime of the open screen. The stack
is the generic `squid_ui.runtime.History`. `EditController` connects it to the edit
executor:

- After a committed edit, the controller adds an entry that holds the receipt.
- Undo builds the inverse command from the receipt: the same handler with the old
  values, expecting the latest revision this controller saw for each target. If
  another user changed the record since, the undo fails with a conflict instead of
  discarding their work.
- A successful undo stores its own receipt, so redo works the same way.
- A *final* edit, such as a deletion, cannot be undone. Accepting it removes every
  history entry for the same targets.

Forms with unsaved widget values also record them in a `History`, using a different
operation that restores the widget state (`track_local_edits` in
`squid/bot/edit_history.py`).

### Removed files

Removing an attachment from a draft only marks the draft's reference as discarded, so
undo can restore it. Cleanup keeps any file that an open draft references, even
through a discarded reference (`draft_keeps_file`). Staged uploads, which are hidden
until an attachment edit accepts them, are kept the same way. Once a draft is submitted
or expires, files it no longer lists become deletable.

## Build history

Every change to a build and its child rows is recorded permanently in
`build_edit_history`, with snapshots of the build before and after
(`build_history_states`). PostgreSQL triggers capture the snapshots, so writes made with
SQL are recorded too.

A transaction that changes a build must first open a *build edit scope*
(`build_edit_scope`, usually through `uow.builds.editing(...)`) with a
`BuildEditContext` naming the actor and the kind of operation. The triggers reject build
changes outside a scope. Build history is independent of session history: undoing an
edit adds a new history entry rather than removing one.

## Other names that look related

- **Draft changes and schema revisions.** `submission_draft_changes` logs each accepted
  answer change so a client retry returns the same result. `schema_revision` is the
  version of the form a draft answers, not a revision counter.
- **Recalculation proposals.** Inferred corrections to an existing build, held for
  review (`squid/submissions/application/recalculation.py`). Their HTTP routes and
  table still use the older name "revision proposal".
- **Minecraft versions.** `squid/versions` handles the game versions a build works in.
- **Snapshots.** `squid/tags/application/snapshots.py` and
  `squid/starboard/application/snapshots.py` hold detached copies of records that
  handlers compare before replacing.
