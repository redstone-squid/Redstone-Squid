# 28 — History: undo with external reversions

## Problem

`transaction()` is rollback — a failed action restores pre-action state. Undo is the
other case: the action *succeeded* and the user later changes their mind. Nothing models
it, and the interesting half is not component state (the transaction already captures
that) but the world: a setting written to the database cannot be undone by restoring a
dataclass. Cascade's per-view undo stack handles only the view; the requirement here is
explicitly to allow external reversions too.

## Design

> The framework restores what it owns; the author reverses what they own; neither
> pretends to do the other's half.

### 1. `sl.history(limit=20)`, declared like state

A descriptor, in the shape `sl.state()` already uses: declared as a class attribute,
annotated as the bound object, materialized per instance on first access.

```python
class SettingsPanel(sl.Component):
    history: sl.History = sl.history(limit=20)
```

The descriptor form is what makes the stack *reactive*: pushing and popping invalidates
the owning component, so undo/redo controls enable and disable themselves. A plain
attribute would need `Component.mutated("history")` at every mutation site.

```python
type Inverse = Callable[[], Awaitable[None]]

class History:
    can_undo: bool; can_redo: bool
    undo_label: TextLike | None; redo_label: TextLike | None
    entries: tuple[HistoryEntry, ...]      # oldest first
    redoable: tuple[HistoryEntry, ...]
    def record(self, label: TextLike, *, undo: Inverse | None = None,
               redo: Inverse | None = None) -> None: ...
    async def undo(self) -> HistoryEntry | None: ...   # None when the stack is empty
    async def redo(self) -> HistoryEntry | None: ...
    def clear(self) -> None: ...
```

### 2. Three tiers, not two

| `record(...)` | undo | redo |
|---|---|---|
| `record(label)` | framework restores `state_before` | framework restores `state_after` |
| `record(label, undo=f)` | `f()` then `state_before` | not redoable — dropped after undo |
| `record(label, undo=f, redo=g)` | `f()` then `state_before` | `g()` then `state_after` |

The middle row's rule is the important one: the world moved on undo, so a state-only redo
would re-apply the UI while leaving the world reverted — the exact lie §5 exists to
prevent. The top row has no such problem, because there is no world half at all, so an
action that touches nothing external is fully reversible *and* replayable by the framework
alone and should not have to declare an inverse to say so. `redo=` without `undo=` is a
`TypeError`: it describes a redo of something nothing undid.

### 3. Opt-in per action, and the entry is the whole action

Only explicit `record()` enters history. Auto-recording every transaction would fill the
stack with page turns, and worse, imply domain writes are reversible when no inverse was
declared. Same authorization philosophy as `best_effort`: consequential reversal requires
the author's signature.

Two consequences worth stating because they surprise:

- The entry covers every state write the action made, **including ones after the
  `record()` call**. `record()` marks the action, not the cursor position in the handler.
- The entry spans **every component** the action touched, not just the one holding the
  history — the transaction is per-action, not per-component. A child may hold the history
  and still undo writes its parent made in the same action.

A second `record()` in one action raises `HistoryError`; both calls would describe the
same delta. `record()` also clears the redo stack, as every undo stack does.

### 4. The mechanism: one new seam in `reactivity.py`

```python
def on_action_commit(callback: Callable[[StateDelta], None]) -> None:
    """Hand `callback` the action's whole state delta if this transaction commits."""
```

- `_Transaction` grows `on_commit: list[...]`, drained in `commit()`.
- `StateDelta` is built there from `snapshots` — each entry is `(owner, slot,
  existed_before, before, existed_after, after, copy)`. After-values are read with the
  same copy mode the snapshot recorded, so `copy="ref"` fields are never deep-copied on
  the way in.
- It raises outside a transaction and inside a `readonly_transaction`. That is where
  `record()`'s two errors come from for free: handlers only, and never from a
  `PARALLEL_READ` action, which by construction changed nothing.
- **A rolled-back transaction never runs its hooks**, so a handler that records and then
  raises leaves no entry. No cleanup path, no half-recorded stack.

`StateDelta.restore_before()`/`.restore_after()` reuse a `_restore(owner, name, existed,
value, copy)` helper factored out of the existing `_Transaction.rollback` body (re-`_observe`
deep values, `__dict__.pop` when the attribute did not exist before), wrapped in
`_before`/`_after` so the writes are recorded by the *undo action's own* transaction and
invalidate at its commit.

### 5. `undo()`: world first, then state

Run the external inverse; only on success restore `state_before`, pop, and invalidate. A
failed inverse propagates: the entry stays on the stack, the ambient transaction rolls
back anything the undo handler had already written, and the error reaches the host through
`Mount.handle_error` like any other handler failure. Restoring the UI while the world
stayed changed is the lie this ordering exists to prevent. `redo()` mirrors it with
`external_redo`/`state_after`.

`undo()` opens `with transaction():` itself. That is a no-op inside a handler (transactions
do not nest) and real rollback safety for a background caller, and it makes the state
restore atomic with everything after it.

**Amendment (external review 43):** the stack pop/push and owner invalidation are action
commit hooks, just like `record()`'s push. The state restore is therefore staged immediately,
but an enclosing action that later raises rolls back the restore without changing either
history stack. The hook key also reserves the history for one operation per action: a second
`record()`, `undo()`, or `redo()` raises instead of applying the same pending stack entry
twice.

The framework cannot roll back a successful external inverse. After an undo or redo carrying
one succeeds, do not perform unrelated fallible work in the same action: a later rollback can
restore component state and preserve the stack, but it cannot un-call a database or API.

### 6. The inverse may not write component state

For the duration of the `await`, the ambient transaction carries a write block
(`_Transaction.write_block: str | None`, checked in `mark_changed` and
`report_undeclared_write` ahead of the readonly check); an inverse that assigns declared
state raises `ReactiveWriteError` naming history. The reason is mechanical: the framework's
restore runs *after* the inverse and would clobber the write silently.

The escape hatch is explicit and outside the window — `undo()` returns the entry, so a
handler that needs fresh data re-reads the world itself:

```python
async def _undo(self, event: sl.PressEvent) -> None:
    if not await self._may_event(event, SETTINGS_SERVER_EDIT):
        return
    if entry := await self.history.undo():
        await event.notice(L("Undid: {what}", what=entry.label))
```

Note what that handler must do: **repeat the authorization check of the action it
reverses.** The framework cannot know which permission an inverse needs, and permissions
change during a session — `_may_event` already exists and already says "You are no longer
allowed to change this".

### 7. Undo dispatches through the funnel

The controls are ordinary EXCLUSIVE actions, so `lock_to`, generation checks and the
transaction context govern who may undo and what happens on failure — no new concurrency
model. Multi-user "whose action?" is answered by the existing author-lock semantics, not
new ones.

`Chrome` gains `undo`/`redo` (two strings, resolved in `localize_chrome` like the rest),
and `sl.history_actions(history, chrome, key="history")` returns an `ActionGroup` of two
`Action`s gated on `can_undo`/`can_redo` — the shape `navigation_controls` already has
(`planning/navigation.py`). Anything richer than two buttons the author writes with
`sl.action`; the factory covers the common case in one line and does not grow a hook zoo.

### 8. In-memory in v1, and invisible to durability

External inverses are closures; they do not serialize. `History` is not a `_State`, so
`export_state` never sees it and a snapshot-restored mount (plan [27](27-snapshot-stores.md))
starts with an empty history — correct rather than lossy. A durable history would need
routed-style command codecs, deferred until someone needs undo to survive a restart, which
is a much bigger claim than undo.

## Known limits, deliberately

- **LIFO with no divergence detection.** If an *unrecorded* action wrote the same field
  after a recorded one, undo restores the recorded action's pre-value and that later write
  is lost. Inherent to opt-in recording. The rule for authors: record all the actions that
  write a field, or none of them. Detection was considered (compare current against
  `state_after` at undo time) and rejected — it cannot help the multi-user case, which is
  what `lock_to` is for, and equality on arbitrary state values is not a check the
  framework can run safely.
- **`copy="ref"` restores the reference, not the object.** Plan [08](08-transactional-state-coverage.md)'s
  known limit, inherited unchanged: undoing a field that points at a `Build` restores
  *which* build it points at, not what that build contains.
- **Presentation is not state.** Cursor positions live in `PresentationSession`, so undo
  does not restore which page you were on.
- **Components born during the action are exempt** (`_Transaction.protects`), so undo
  cannot un-create one. The declared field pointing at it *is* restored, which is what the
  reader sees; the orphan is garbage.
- **Redo replays recorded values, not a re-execution.** An external redo that produces
  different data than the original action should not expect the state half to notice.
- **Routed controls have no history.** Stateless dispatch runs no transaction and owns no
  component state; history is a mounted-session feature, consistent with plan
  [14](14-routed-actions.md)'s "not a durability feature".
- **Entries pin their owners** through `StateDelta`, bounded by `limit`.

## Considered, not done

- Surfacing the stack in `MountSnapshot` for `!dev ui inspect`. That snapshot's contract is
  cheap scalars and already-immutable values; `history.entries` is the surface an author or
  a test reads. Revisit if devtools grows a per-component view.
- Merging several `record()` calls in one action into a composite entry. Raising is honest
  and reversible; merging is not.

## Consumers

`SettingsPanel` (`squid/bot/settings_view.py`), the best fit in the tree: the state half of
every one of its writes is already declared (plan 08 did that), and every service method it
calls is an idempotent setter, so the inverse is one line holding the value the panel
already had.

| Action | Inverse |
|---|---|
| `set_channel(setting, id)` | `_write_channel(setting, previous)` — `set_channel`, or `clear` when `previous is None` |
| `set_locale(locale)` | `_write_locale(previous_override, previous_effective)` — the stored locale plus `Mount.localize`, neither of which is component state |

The pre-value is read off declared state the panel already holds
(`self._channels[setting]`, `self._locale_override`) before the write. Both actions pass
`redo=` as well, and the server page grows Undo and Redo buttons that appear only once their
stack has something in it — the stack invalidates its owner, so that is free.

The split each write needed is the design working: `set_channel` became a thin `record()`
around `_write_channel`, which does the stored half *only*, because §6 forbids an inverse
from writing `_channels` — and it does not need to, since the framework restores it.

**Not recorded, and the plan says why:**

- `set_weight` and `set_emojis` reach the panel from `RoleWeightModal`/`VoteEmojiModal`,
  which are raw `ErrorHandledModal`s rather than plan [18](18-forms.md)'s `Form` path. They
  never enter `Mount.dispatch`, so there is no transaction and nothing to capture. Recording
  them means migrating those two modals first — which is plan 18's leftover work, not this
  plan's.
- `reset_voting`, because `VoteService.emoji_preset` synthesizes a default preset when
  nothing is stored: "restore what was there" would write the defaults as an explicit row, a
  different state from the one it claims to restore. Exact parity needs a service that
  reports storedness. This is §5 in miniature — the framework cannot verify that an inverse
  inverts, and here the author can see that it does not.

The earlier candidate, the build edit panel's destructive actions, was dropped:
`BuildEditComponent` has no archive and no media removal, and its one domain write
(`_apply`) finishes the mount, so there is nothing left to draw an undo control on.

## Verification

- `packages/squid-layouts/tests/test_history.py` (new): `record` outside a transaction and
  inside a `PARALLEL_READ` action; a handler that records then raises leaves the stack
  empty; undo restores only the fields the action wrote; inverse-before-restore order
  asserted with a call log; a failing inverse leaves entry and state untouched and
  propagates; a state write inside an inverse raises; `redo=None` drops after undo while a
  pure-state entry redoes; `record` clears the redo stack; `limit` drops the oldest;
  `copy="ref"` undo pins the known limit; `history_actions` availability gating.
- `packages/squid-layouts/tests/test_transactions.py`: extend for `on_action_commit` —
  hooks do not run on rollback, and the delta's after-values respect `copy="ref"`.
- `tests/unit/bot/test_settings_panel.py`: change a channel then undo, asserting the
  service saw the old value and the panel kept it; the same change redone; a failed action
  recording nothing; the Undo control appearing only once the stack is non-empty; and undo
  after the permission was revoked, which notices and calls no service.
- `packages/squid-layouts/tests/test_public_api.py` covers the new exports.
- `just typecheck` (compare against a pre-change run — the tree is not at zero) and
  `git diff --check`.
