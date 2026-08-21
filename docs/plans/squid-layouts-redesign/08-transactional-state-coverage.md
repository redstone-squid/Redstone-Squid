# 08 — Honest transactional coverage for component state

Shipped. `layouts: report undeclared component writes instead of hiding them` and
`bot: declare the mutable state the panels were keeping plain`.

## Problem

`sl.state` gave reactivity, transactional rollback, and durability — but only for values
that survive `deepcopy`/JSON. Real components therefore kept most state in plain attributes
with manual `invalidate()`. `SettingsPanel` was the tell: three `sl.state` fields (`page`,
`kind`, `confirming_reset`) versus ~eight plain ones, and `set_channel` ended in a
hand-written `self.invalidate()`.

The architecture doc claimed "a failed callback cannot leave state half-applied". That was
only true for the declared minority: a handler that mutated `self._channels` and then raised
left it mutated, and `readonly_transaction` (PARALLEL_READ) did not reject plain-attribute
writes either. Nothing warned at the boundary — the framework's central guarantee applied to
whichever fields happened to be copyable, and the author had to remember which tier each
field was in.

## What the first design got wrong

The original plan here proposed a `Component.__setattr__` that snapshotted plain-attribute
writes *by reference* during a transaction and rolled them back on failure. Reading the nine
consumers before implementing killed it:

- **It did not fix its own flagship example.** `set_channel` does
  `self._channels[setting] = channel_id` — in-place container mutation, which reference-level
  rollback leaves untracked. The hand-written `invalidate()` would have survived the plan
  that cited it.
- **Plain *assignment* was the minority case.** Across the manual `invalidate()` sites in
  `settings_view`, `notifications_view`, `account_view`, `poll_wizard` and
  `submission/ui/views`, the dominant patterns were in-place mutation and
  `await self.load()`-then-refresh. Only `self.draft = replace(...)` had the tracked shape.
- **It conflated two categories.** Mutable view state kept plain out of friction
  (`_channels`, `_weights`, `_preset`, `draft`) is all deep-copyable and should simply be
  declared. Collaborator handles set once in `__init__` (`_settings`, `_guild`,
  `_capabilities`) never need rollback at all, and are only ever written during construction,
  which no transaction covers. The mechanism spent its complexity on the second group while
  giving the first the weakest possible guarantee.

Adding a third, weaker tier would have made the boundary harder to predict, not easier.

## Design

Inverted: make the boundary loud, and remove the reasons to sit outside it.

1. **Report undeclared writes; do not half-cover them.** `Component.__setattr__` checks one
   contextvar, and when a transaction is in flight and the attribute is not a `_State` slot,
   calls `report_undeclared_write`. Read-only actions raise `ReactiveWriteError`; mutating
   ones log a warning naming `Type.attr`; `sl.strict_state()` promotes that warning to
   `UndeclaredStateError`. Both test suites run strict.
2. **Exempt components that are not in the tree.** `Component._state_tracked()` is false
   until `_runtime` or `_parent` is set, and `_Transaction.mark_changed` consults it before
   rejecting. Without this, a read-only handler could not construct a component at all —
   every `self._x = ...` in `__init__` reached `mark_changed` and raised. `_runtime` and
   `_parent` themselves are exempt by name, since the tree walker writes them.
3. **Make declaring a field the easy answer.** `sl.state()` now accepts neither a default nor
   a factory, for fields `__init__` assigns; `sl.state(copy="ref")` snapshots the reference
   instead of a deep copy, so services, guilds and sessions can be declared (never persisted,
   and their containers are not proxied, since both would reintroduce the copy); and `state()`
   gained typed overloads, so `count = sl.state(0)` infers `int`.
4. **Migrate the consumers.** The panels' service caches became `sl.state(persist=False)`.
   Declaring `_channels` is what actually fixed `set_channel`: it is a dict, so it is wrapped
   as a `ReactiveDict` and its in-place writes roll back for free — the case the rejected
   design would have missed. `BuildEditComponent.build`/`._node` are `copy="ref"`.
5. **Fix the docs claim.** `docs/squid-layouts-architecture.md` now carries a three-row table
   instead of the false sentence.

## Known limits, deliberately

- Attribute mutation on a nested domain object (`self.build.door_orientation = ...`) is not
  tracked: `_observe` wraps containers, not arbitrary objects. `__setattr__` never fires for
  it either, so it is not even reported. `_door_changed` and `_location_changed` in
  `submission/ui/views.py` keep their manual `invalidate()` for exactly this reason.
- `copy="ref"` rolls back the reference, not the object. `_apply` mutates the `Build` in
  place before saving it; a failure there leaves those mutations applied.

## Verification

- `packages/squid-layouts/tests/test_transactions.py` covers the reporting rules, the
  construction exemption, and `copy="ref"` (asserted with a class whose `__deepcopy__`
  raises).
- `tests/unit/bot/test_settings_panel.py` covers the guarantee on the panel that motivated
  the plan: a channel written before a later failure, a half-loaded voting page, and a
  read-only action. Each fails without its declaration.
- `tests/unit/conftest.py` and the package conftest enable `sl.strict_state()` autouse.
