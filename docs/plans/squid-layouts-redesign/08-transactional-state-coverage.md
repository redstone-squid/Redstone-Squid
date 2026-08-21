# 08 — Honest transactional coverage for component state

## Problem

`sl.state` gives reactivity, transactional rollback, and durability — but only for
values that survive `deepcopy`/JSON. Real components therefore keep most state in plain
attributes with manual `invalidate()`. `SettingsPanel` is the tell
(`squid/bot/settings_view.py:352-380`): three `sl.state` fields (`page`, `kind`,
`confirming_reset`) versus ~eight plain ones (`_channels`, `_weights`, `_preset`,
`_locale_override`, even `locale`), and `set_channel` ends in a hand-written
`self.invalidate()` (settings_view.py:623).

The architecture doc claims "a failed callback cannot leave state half-applied"
(docs/squid-layouts-architecture.md, Components section). That is only true for the
declared minority: a handler that mutates `self._channels` and then raises leaves it
mutated; `readonly_transaction` (PARALLEL_READ) does not reject plain-attribute writes
either. Nothing warns at the boundary — the framework's central guarantee applies to
whichever fields happened to be copyable, and the author must remember which tier each
field is in.

## Design

Two layers: widen the mechanism where cheap, and make the remaining boundary loud.

1. **Track all instance-attribute writes during a transaction.** Add
   `Component.__setattr__`: when a transaction is active and the attribute is not a
   `_State` slot, record a snapshot (best-effort: keep the old *reference*, not a
   deepcopy — reference-level restore is what plain attributes can honestly support)
   and roll it back on failure. This closes the common case (`self._locale_override =
   x` then a later await raises) without pretending to deep-copy service objects.
   In-place mutation of plain containers (`self._channels[k] = v`) remains untracked —
   that is the honest limit, documented.
2. **Reject plain writes in read-only transactions.** The same `__setattr__` hook makes
   PARALLEL_READ raise `ReactiveWriteError` for plain attributes too, closing the
   loophole where a "read-only" action mutates undeclared state silently.
3. **Fix the docs claim.** Architecture doc and README state the actual guarantee:
   assignment-level rollback for all attributes, deep rollback for declared state,
   nothing for in-place mutation of undeclared containers. One table, three rows.
4. **Nudge toward declared state.** `state()` currently types as `Any`; add overloads so
   `count: int = sl.state(0)` and `results: list[str] = sl.state(factory=list)` infer
   without the annotation being load-bearing. Cheap, reduces one reason authors avoid it.

Deliberately rejected: forcing all state through `sl.state` (real components hold
services, guilds, capabilities — non-copyable by nature), and deep-copying plain
attributes on first touch (surprising cost, and identity-sensitive objects break).

## Verification

- `test_composition.py`/new `test_transactions.py`: plain-attribute assignment rolls
  back on handler failure; PARALLEL_READ rejects plain writes; in-place plain-container
  mutation documented-as-untracked (test asserts current behavior so a future change is
  deliberate).
- Perf sanity: `__setattr__` overhead outside transactions must be one contextvar read;
  assert no measurable regression in the render benchmark under
  `tests/test_planner.py`'s latency budgets.
- `just typecheck` — the `state()` overloads must not break existing annotations.
