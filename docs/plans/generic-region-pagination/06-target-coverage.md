# Target coverage and migration

Status: in progress.

## Evidence and decisions

Phases 2–5 provide a target-neutral solver, but production selection still gates
on three limit classes and a smaller vocabulary than the semantic graph supports.
Classic messages and Slack Home need direct composition rules. Slack modals are
form submission surfaces: their existing contract requires one top-level form
trigger, rather than an independently navigable document. Preserve that contract
and report it explicitly when region pagination is requested.

Extend the existing realization boundary, keeping target assembly outside search.
Share Slack block evaluation between message and Home surfaces. Classic Discord
must account separately for content, embed text, rows, controls, and attachments.
Do not convert a completed V2 scene into classic output or a completed HTML scene
into Slack output.

Expand shared vocabulary where semantics and target resources can be preserved.
Keep native extensions and authored adaptation ladders on their explicit target
paths until they supply shared offers; do not guess equivalence or flatten hard
structure requirements. Unsupported regions must report the missing capability,
not run the obsolete V2-only region search.

## Reviewable steps

1. Implement and test direct classic and Slack Home composition and production
   dispatch, including reduced capacities and independent page coordinates.
2. Extend portable semantic realizations and migration routing. Exercise shells,
   text, fields and controls, preserving source identity and authored constraints.
3. Remove the superseded region planner, retain only needed discovery helpers,
   and test unsupported combinations with concrete capability diagnostics.
4. Exercise crowded application and help layouts, update the public capability
   matrix and migration documentation, and record deferred capabilities honestly.

## Validation

Use cross-target conformance cases for coverage, nested pages, navigation costs,
reservations, bindings, and retained turns. Run focused tests with `--no-cov`
after edits, both UI package suites where broad integration warrants it, Ruff via
installed hooks, `git diff --check`, and whole-project `just typecheck`.
Unrelated application edits present at task start remain outside these commits.
