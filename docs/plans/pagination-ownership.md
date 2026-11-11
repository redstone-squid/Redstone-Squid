# Pagination ownership: design work before implementation

> **Status:** blocked on the multi-choice design below. `Paginate(page=...)` and the `RankedList` migration
> have landed. Do not implement the remaining migrations until the selection-window contract is settled.

## Where things stand

Stateful controls use `Ownership`: `Uncontrolled(initial)` stores the value in `PresentationState`,
and `Controlled(value, on_change)` takes the application's value on each render. `Paginate` now takes
`page: PageOwnership` (`squid_ui/ownership.py`). Planner page turns go through `PageCommit` in
`squid_ui/planning/navigation.py`.

`Paginate` is special: it is an overflow policy whose page boundaries and page count depend on layout,
text budgets, and navigation chrome. The planner resolves the requested page against the content that fits.
Sharing ownership vocabulary with other controls does not make pagination an ordinary stateful control,
or require option windows to use the same API. Its routed API needs to account for that planning work.

`RankedList` keeps its page in machine state and calls `MachineControls.paged_lines`. Under the mounted driver
that is a controlled `Paginate`, so the planner draws the host's navigation and footer, with an optional jump
select. Under the routed driver it is a hand-written slice with routed Previous and Next controls, because
planner page buttons currently carry Python callbacks that cannot survive a restart.

## Resolve multi-choice windows first

The previous plan called this widget `MultiChoicePanel`; its current implementation is `MultiChoice` in
`squid_ui_widgets/multi_choice.py`. Its manual pagination carries selection semantics, not just presentation.
Adding `PageOwnership` to option windows is insufficient to replace it.

Ordinary `Choices` currently rejects multi-selection when the available options exceed one select menu's
capacity. `RoutedChoices` does not paginate. `MultiChoice` works by supplying an already sliced window and
merging submitted values into its staged selection. It retains selections outside that window, clears
exclusive rival groups when a replacement is nonempty, and keeps staged and committed selections separate.
Its render also calculates the menu's selection allowance from selections outside the visible window.

Moving the window into the planner would hide information that both rendering and transitions currently
need. There is also a boundary decision to resolve: the widget slices before unavailable choices are
filtered, whereas ordinary `Choices` filters before slicing. These can produce different visible windows.

### Proposed direction to evaluate

Keep group exclusivity, global cardinality, staging, and commit policy in the machine. Investigate a shared,
explicit option-window description that rendering and transitions can both use. It should identify the
resolved page and the exact selectable keys a submission replaces. A page number alone is insufficient if
the options or their availability change between rendering and interaction.

For example, with `a` selected on page one and `b` on page two, submitting only `c` from page two should
produce `a, c`; submitting an empty selection on page two should leave `a`. The transition must know which
keys belonged to the submitted window, rather than recomputing an unrelated window from newer state.
Decide whether an outdated window is rejected or reconciled, and how the host validates routed input.

Compare two designs before choosing an API:

- The machine resolves an option window and passes it to shared picker/navigation rendering. This keeps
  selection policy available before planning while removing duplicated navigation where practical.
- The planner resolves the window and emits selections with explicit window scope. This needs a way to
  calculate window-dependent menu limits during planning and to carry the same scope through routed input.

Prefer the first unless the second has a clear contract without moving widget policy into the planner.
Keeping a shared explicit slicer is an acceptable outcome; deleting `_paging.window`, `PagePosition`, and
`PageAction` is conditional on a replacement that actually handles their responsibilities.

Before implementation, record the chosen contract and work through these cases for mounted and routed use:

- Select and deselect across pages and groups, including an empty submission and unavailable options.
- Reach the global maximum, then replace selections, including selections in an exclusive rival group.
  Decide how the menu permits a replacement that the machine would accept after clearing rivals.
- Preserve invalid staged values for correction, and distinguish explicit Apply from immediate commits.
- Use a window smaller than 25, shrink or reorder options, and resolve an out-of-range page.
- Handle a submission from an older rendered window and reconstruct its meaning after a process restart.
- Preserve the small-panel modal fallback's whole-selection semantics alongside page-scoped submissions.

These examples should become focused behavior tests once the design is settled. Check `CollectionEditor`
against the same contract before deciding that both widgets should migrate to planner-owned windows.

## Routed page navigation, after that decision

A routed pager still receives an application-owned page on each render. A page turn is delivered through a
route rather than `on_change`. Decide how to represent that distinction in `Paginate` after resolving the
window design. `Paginate(page=3, route=...)` is only a possible spelling, not an agreed API: it would change
what `page` accepts depending on another argument. Consider an explicit routed configuration or separate
policy, while preserving clamping, negative page indices, and the absence of presentation-owned cursor writes.

The implementation needs to settle:

- Routed navigation in `NavigationContext`, with Previous and Next rendered as `RoutedButton` and jumps as
  `RoutedSelect`. `Row` and `NavNode` accept both routed and callback controls; every nav factory must handle
  both modes. Squid has no custom factories today.
- Jump dispatch: `RoutedSelect` has one route ID for the entire menu. Choose between one input route receiving
  the selected page and route IDs encoded in option values with corresponding dispatch support. A route per
  target page alone only explains buttons.
- Route generation during planning: candidate layouts may be discarded. Require route factories to be safe
  to call repeatedly for discarded candidates, or defer route registration until the final plan is selected.
- Test observation: `squid_ui_widgets.testing.routed` and the routed conformance tests in
  `test_machine_conformance.py` inspect render-time routes today. They must inspect planned controls too.

Acceptance tests should cover boundary buttons, clamped pages after content shrinks, jump selection, repeated
planning, and dispatch after reconstructing the driver without the original callbacks. Include content that
splits further under the text budget, so routed and mounted pagers follow the same layout-derived boundaries.

Only then can `_RoutedControls.paged_lines` return a planner pager and lose its hand-written slice, footer,
and buttons. Nothing in Squid uses `RouteDriver` yet; this supports future persistent messages such as a
posted leaderboard.

## Deferred semantic follow-ups

These remain useful, but do not start them ahead of the multi-choice design decision:

- Replace `semantic.Paged` (`sl.paged`)'s `initial: "start" | "end"` with `page: PageOwnership`.
- Expose page ownership on semantic `List`, which currently exposes only `page_size`.
- Revisit whether `paged_lines` can return the portable `List` instead of Discord-only `Lines`. A `start`
  number would preserve ordered numbering for manually sliced lists; reassess its necessity if routed
  pagination instead passes the complete list to the planner.

After the design is settled, choose small implementation commits around the agreed boundaries, with focused
selection and navigation tests. API uniformity and deleting helpers are secondary to preserving behavior.
