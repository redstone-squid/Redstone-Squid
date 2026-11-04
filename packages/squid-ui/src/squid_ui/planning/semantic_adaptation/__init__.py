"""Translate authored meaning into Discord primitives for layout measurement.

`decisions` lists the representations available at each reachable semantic node,
such as buttons or a select menu for a group of actions. The planner chooses a
combination, then `lowering` builds its primitives and generated event handlers.
Each candidate collects its own assets, diagnostics, and presentation-state
updates so rejected representations leave no effects in the selected plan.
"""
