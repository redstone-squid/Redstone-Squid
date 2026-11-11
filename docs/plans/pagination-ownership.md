# Pagination ownership: remaining work

> **Status:** in progress. `Paginate(page=...)` and the `RankedList` migration have landed; the items below have not.

## Where things stand

Every stateful control takes an `Ownership`: `Uncontrolled(initial)` stores the value in `PresentationState`,
and `Controlled(value, on_change)` reads it from application state on every render. `Paginate` now follows the
same rule with `page: PageOwnership` (`squid_ui/ownership.py`). Page turns on planner pagers go through
`PageCommit` in `squid_ui/planning/navigation.py`, the same way choice and navigation changes go through the
planner's generated handlers.

`RankedList` keeps its page in machine state and calls `MachineControls.paged_lines`. Under the mounted driver
that is a controlled `Paginate`, so the planner draws the host's navigation, footer and jump select. Under the
routed driver it is still a hand-written slice with routed Previous and Next controls, because the planner's page
buttons are Python callbacks that cannot survive a restart.

## Routed page navigation

A routed pager does not need a new ownership model. The page is still a value the caller supplies on every render,
as with `Controlled`. Only the delivery of a page turn differs: a route ID per target page instead of an
`on_change` callback. Existing routed controls separate routing the same way: `RoutedChoices` and
`RoutedActionControl` are separate nodes that carry a route ID and no ownership.

What it would take:

- A way for `Paginate` to carry a page plus a function from target page to route ID, for example
  `Paginate(key=..., page=3, route=lambda page: ...)`. The planner calls it for Previous, Next and each jump entry.
- A routed mode in `NavigationContext`, so `navigation_controls` and `seek_control` draw `RoutedButton` and
  `RoutedSelect`. `Row` and `NavNode` already accept both. Every nav factory must handle both modes; Squid has no
  custom factories today.
- Routes are then created while planning rather than while rendering. `squid_ui_widgets.testing.routed` and the
  routed conformance tests in `test_machine_conformance.py` inspect render-time routes and would need to plan.

When this lands, `_RoutedControls.paged_lines` in `squid_ui_widgets/drivers.py` can return the same `Paginate` as
the mounted driver, and its hand-written slice, footer and buttons can be deleted.

Nothing in Squid uses `RouteDriver` yet, so this mainly matters for persistent messages, such as a posted
leaderboard, that should keep paging after a restart.

## Option windows

`CollectionEditor` and `MultiChoicePanel` page `Choices` pickers, which the planner windows 25 options at a time.
Those windows still take no ownership, so both widgets slice with `_paging.window` and draw their own controls.
Giving the option window a `PageOwnership` would let them move over and allow deleting `_paging.window`,
`PagePosition` and `PageAction`.

## Smaller follow-ups

- `semantic.Paged` (`sl.paged`) still takes `initial: "start" | "end"`; it should take `page: PageOwnership` like
  `Paginate`.
- Semantic `List` pages through `Paginate` but exposes only `page_size`. It is the portable paged node and should
  take `page` too. `paged_lines` returns the Discord-only `Lines` because an ordered `List` restarts its numbering
  on each routed slice; a `start` number on `List` would fix that.
