# 19 — Patterns on forms: Wizard, MultiChoicePanel, and the two-shell rule

## Problem

- `poll_wizard.py` is a hand-rolled wizard — a modal step feeding a panel step —
  reimplementing chrome, state carry, and edit-reopen by hand.
- Assignment/roles panels are blocked on the rejected cross-page multi-select boundary
  ("pending an explicit grouping/commit model", `90-deferred.md`).
- A pattern that hard-codes `sl.action` can never have a restart-surviving variant;
  Cascade's answer is `PersistentFoo` class proliferation, which we already rejected.

## Design

Three pieces; the first is a rule, not code.

1. **The two-shell rule.** Author every pattern as a pure `state → tree` function with
   control construction injected. The component shell stores state in `sl.state` and
   injects `sl.action` closures; the router shell stores state in route parameters and
   injects `sl.routed_action`, rebuilding the whole document per interaction — the
   message is its own session, every control encoding the next state.
   `PageBroker.overrides` (the explicit page that outranks the stored cursor) is the
   stateless render's pagination entry, since no stored cursor exists. The rule binds
   Tabs/Menu/RankedList (tracked separately) as much as the two patterns below.

2. **`Wizard`.** Steps are computed from collected state on each render — branching is
   free, the fixed tuple is the constant case. A step is a Form (plan 18) or plain
   content. Answers live keyed by step; when a changed earlier answer hides steps,
   their answers are retained invisibly and restored if the branch returns, but
   **Finish collects live steps only** — orphans never submit. Two Discord mechanics,
   both consequences of the verified no-modal-after-modal-submit rule:
   - Next is a component interaction, so it may open a form step's modal directly.
   - Two consecutive form steps force one interstitial hop: the submit updates the
     wizard panel (progress, collected values, Continue), and Continue opens the next
     modal. The framework owns the hop the way plan 18 owns the retry loop — same
     rule, same misery, same owner.

3. **`MultiChoicePanel`.** Cross-page multi-select cannot be a `Choices` feature: a
   select submits the complete selection *among visible options*, so once a group pages
   into 25-option windows each submit speaks only for its window; `max_values` is
   per-window, so cardinality violations are only discoverable after the interaction;
   and no single window shows the reader their whole selection. The "explicit
   grouping/commit model" the rejection demanded is Form's submission model:
   - staged vs committed sets held in the component, `Controlled` everywhere —
     engine-side `Managed` merging across windows stays rejected;
   - per-window merge: replace membership of visible options, preserve the rest;
     exclusivity between groups is a merge rule;
   - a summary line ("6 selected: …"), `sl.status` for violations, Apply gated on
     validity, commit dispatching once;
   - each window's `max_values` set to `min(25, maximum − staged elsewhere)`, so the
     control prevents what it can and commit validation catches what it cannot.
   Small-cardinality panels may adapt into a modal, now that checkbox and radio groups
   are modal-legal.

## Sequencing

After 18 — both patterns consume its submission model. The two-shell rule binds any
pattern implemented in the meantime.

## Verification

- Wizard: a branch flip hides a step; its answers restore on return; Finish excludes
  orphans; the interstitial hop between consecutive form steps; Back/Next chrome.
- Panel: window merge preserves off-window staging; an exclusive pick clears its rival
  group; the cardinality gate blocks Apply; commit dispatches exactly once.

## Status

Implemented 2026-08-21.

## Implemented API

All five patterns implement one pure `initial_state` / `transition` / `render(state, controls)`
contract. `ComponentShell` owns `pattern_state` in `sl.state` and injects callback controls;
`RouterShell` accepts explicit state and a `PatternRoute -> route_id` builder and injects routed
controls. `PatternRoute.phase` distinguishes deterministic button state (`next`) from state awaiting
select or form input (`input`). There is no parallel `PersistentFoo` class hierarchy.

- `Tabs.component()`, `Menu.component()`, `RankedList.component()`, `Wizard.component()`, and
  `MultiChoicePanel.component()` are convenience constructors for the same generic component shell.
- `WizardStep` accepts a `FormSpec`/`Form` or plain content. `WizardState.answers` retains hidden
  branch values, `Wizard.live_answers()` filters them at Finish, and `Wizard.form_for()` supports
  routed modal presentation with restored prefill.
- `MultiChoiceGroup` declares explicit group exclusions. `MultiChoiceState` carries staged,
  committed, and per-group page state; `MultiChoicePanel.form_for()` supplies the small-cardinality
  modal alternate.
- Explicit RankedList and MultiChoicePanel windows resolve their route/component page through
  `PageBroker.overrides`, rather than growing a second clamping policy.
