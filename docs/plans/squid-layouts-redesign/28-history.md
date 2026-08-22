# 28 — History: undo with external reversions

## Problem

`transaction()` is rollback — a failed action restores pre-action state. Undo is the
other case: the action *succeeded* and the user later changes their mind. Nothing models
it, and the interesting half is not component state (the snapshot substrate covers that)
but the world: an archive that wrote to the database cannot be undone by restoring a
dataclass. Cascade's per-view undo stack handles only the view; the requirement here is
explicitly to allow external reversions too.

## Design

> The framework restores what it owns; the author reverses what they own; neither
> pretends to do the other's half.

1. **`sl.History(limit=20)`**, held by a component. `history.record(label, undo=,
   redo=None)` may only be called inside a handler's transaction — it raises otherwise —
   because the entry's state capture *is* the transaction's existing pre-action snapshot,
   reused, plus the post-action state at commit. An entry is
   `(label, state_before, state_after, external_undo, external_redo)`.
2. **Opt-in per action.** Only explicit `record()` enters history. Auto-recording every
   transaction would fill the stack with page turns, and worse, imply domain writes are
   reversible when no inverse was declared. Same authorization philosophy as
   `best_effort`: consequential reversal requires the author's signature.
3. **The inverse is the author's promise.** The framework cannot verify that `unarchive`
   inverts `archive`; it sequences, labels, and refuses to pretend.
4. **`undo()` order: world first, then state.** Run the external inverse; only on success
   restore `state_before` and invalidate. A failed inverse keeps the entry on the stack
   and surfaces the error through the normal responder path — restoring the UI while the
   world stayed changed is the lie this ordering exists to prevent. `redo()` mirrors with
   `external_redo`/`state_after`; an entry recorded without `redo=` is dropped after a
   successful undo rather than left un-redoable on the stack.
5. **Undo dispatches through the funnel.** The undo/redo controls are ordinary actions
   (EXCLUSIVE), so `lock_to`, generation checks, and the transaction context govern who
   may undo and what happens on failure — no new concurrency model. Multi-user "whose
   action?" is answered by the existing author-lock semantics, not new ones.
6. **In-memory in v1, stated plainly.** External inverses are closures; they do not
   serialize. A durable history would need routed-style command codecs — deferred until
   someone needs undo to survive a restart, which is a much bigger claim than undo.

## Consumers

Candidate, not committed: the build edit panel's destructive actions (archive, media
removal). The wizard does not want this — Back plus retained answers already is its
undo — and the multichoice panel's staged-vs-committed already is undo before commit,
which is why this plan ships last in the round and only with a real consumer wired up.
