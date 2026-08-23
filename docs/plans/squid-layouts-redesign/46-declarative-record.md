# 46 — `sl.action(…, record=history)`

## Problem

CascadeUI's `enable_undo = True` is one line. Squid's [28](28-history.md) is the stronger
contract — the entry is the whole action's state delta, external effects need explicit
inverses, undo runs world-first — and `sl.history_actions(self.history)` already gives the
controls in one line. What is still per-handler is *recording*: every undoable tier-1 action
opens with `self.history.record("label")`, which is boilerplate precisely in the case where
the framework could do it alone.

## Decision

Declarative, per action, tier 1 only. Auto-recording every action stays rejected (28 §3):
an entry is opt-in because most clicks are navigation.

```python
sl.action("Cycle accent", self.cycle, key="accent", record=self.history)
```

`ActionBinding.record: History | None` (`actions.py`); `sl.action(..., record=)`
(`factories.py`); the mount's invoke path calls `record(binding.label)` inside
`_action_transaction` before the handler runs. Anything that touches the world keeps calling
`record(label, undo=...)` in the handler, where the pre-values live — and a handler that does
so under a `record=` action hits the existing "already recorded" `HistoryError`, which is the
right signal. Nothing in 28 moves: one entry per action, world-first undo, inverse write block.

## Verification

- A `record=` action produces one tier-1 entry with the binding's label; undo restores the
  delta; redo replays it.
- A handler calling `record()` under a `record=` action raises `HistoryError`.
- `tests/test_history.py`; consumer `squid/bot/layout_showcase.py`'s history demo loses its
  manual `record` call.

## Status

Implemented 2026-08-23. Last in the series; independent of 42–45.

Two additions the design implied. `ActionBinding` gained a `label` beside `record`, because
the entry's name is the control's and the binding is where the invoke path can still read it;
recording rides the route bindings too, so a row of grouped actions that collapses into a
picker keeps it. `sl.action` rejects `record=` under `ActionPolicy.PARALLEL_READ` at authoring
time -- the transaction would have raised the same objection at press time, one press later.

The showcase's density button keeps its manual `record`: that control's label names the
density it is showing, which would be a confusing name for the entry that changes it. Both
shapes now stand side by side in the demo.
