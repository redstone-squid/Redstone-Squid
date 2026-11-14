# Target coverage and migration

Status: in progress. The initial rollout is validated, but vocabulary integration is incomplete.

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

## Target capability matrix

This is the production pagination boundary, not an assertion that every legacy
semantic adaptation is now a shared offer. Unsupported vocabulary outside explicit
regions still uses its existing target fitter; the report names that route.

| Surface | Shared pagination and resource contract | Deliberate constraints |
| --- | --- | --- |
| Discord V2 | Regions, root pages, lists, character-based `Paged`, and keyed primitive `Paginate`; component, text, and attachment capacities. | Explicit nested blocks cannot become nested V2 containers. Nested semantic sections retain headings inside the enclosing panel. |
| Classic Discord | Regions, root pages, lists, count-based keyed primitive `Paginate`; independent content, embed text, embed count, row, control, and attachment capacities. Blocks become embeds; loose prose uses message content. | Character grants have no single text pool. Inline download widgets and spoiler embed images have no classic representation; document attachments remain available. |
| HTML | Regions, root pages, lists and character-based `Paged`; visible text is measured for authored budgets, with no global message budget. | Native extensions need typed target rules. |
| Slack message | Regions, root pages, lists; block capacity, local text/control limits, and message fallback-text capacity. | No disabled buttons/selects, inline attachments, spoiler images, or accent-bearing panels. Images require public URLs. |
| Slack Home | Same direct block composition and navigation as messages, using Home's block capacity. | Same block vocabulary constraints; no message fallback-text budget. |
| Slack modal | Existing form planner and modal limits. | The surface requires one top-level form trigger. Region navigation is rejected with this reason; it cannot preserve a submitted form's ownership contract. |

Shared vocabulary now includes headings, paragraphs, notes, code, quotations,
fields, tables, lists, images and figures, actions, choices, toggles, form triggers,
checked alternatives, regions, and the supported grouping/budget wrappers. Actual
realizations remain target-specific: for example, classic displays an image's
alternative description as embed text, and Slack requires plain text in headers
and native table cells. Slack rich inline text is encoded directly; combinations
that cannot preserve literal punctuation in mrkdwn are refused instead of silently
changing it. Section/article thumbnails repeat with their headings on every page.
V2 uses a heading accessory, classic uses the owning embed thumbnail, HTML keeps
the native section/article element, and Slack places an image after the heading.
Classic rejects thumbnails inside an enclosing embed; Slack rejects section accents.

Actions keep ordered button rows and may offer a select when every action permits
grouping. A select must fit the target's option and cardinality limits; cross-page
multi-selection is deliberately not invented. Mixed groups retain their buttons.
Session-owned choices and toggles are rebuilt after state changes rather than
replaying their old certified values. Unambiguous short public control keys remain
stable; scoped or long identities use derived wire keys.

Native V2 sections, panels, thumbnails and separators now participate directly.
Sections retain their exact text slots and accessory; panels may continue across
pages while retaining accent and spoiler settings. Thumbnail descriptions and
accessory controls count against native limits, and only visible accessories
publish bindings. A section text slot cannot host a pager's controls; paginate the
enclosing region instead. Other native extensions still need explicit composition rules.

## Compatibility and deferred contracts

`regions.py` and its backend-probing frontier search are removed. Discovery lives
in `region_detection.py`; unsupported explicit regions raise `JointUnsupportedError`
with diagnostics. Shared search failures never retry through a target fitter.

Section headings now repeat on continuation pages, including character-based
`Paged` sections. Classic loose prose uses the content pool in shared composition;
authored blocks use embeds. Page counts, exact cuts, and historical adaptation
event names may change. Reports use shared search metrics and fidelity effects.

Per-line alternatives combined with native `Paginate` and rich primitive text
fragmentation retain their existing non-region paths.
They do not declare the source correspondence and permitted fragments required
for shared certification. Use checked `alternatives`, semantic paragraphs/lists,
or typed atomic target rules inside regions. No equivalence is inferred from
arbitrary replacement text. Native extensions are usable through explicit custom
composition rules, rather than an opaque production backend probe.

Item browsers and disclosures now expose only their open content to shared
allocation, retaining hidden layout cursors until that content reopens. Navigation now uses shared
offers with destination ownership separate from layout page coordinates. Palette overrides,
status/progress content and section thumbnails now use shared composition. The retained
fitters still serve legacy adaptation permissions and native extensions; they are
active machinery, not dead code.

Explicit prose truncation and native `Truncate` text now offer shared shortening
effects. Head/tail retention and importance survive migration, while strict mode
requires lossless output. Styling remains balanced and controls are never shortened.
Rich inline links and entities still report a concrete shortening limitation.

Native Discord text `Alts` and `Drop` policies also compose inside shared layouts.
Fallback rungs report replacement, shortening the final rung adds its own effect,
and whole-text drops report omission. An enclosing truncation wrapper retains its
policy precedence. Strict planning still requires the primary content to fit.

Static native `Lines` now expose complete-entry fragments and explicit spill,
truncation and drop offers. Lossless pagination precedes spilling; an omitted-entry
notice consumes real capacity. Omission and shortening reports identify the affected
lines, including when the retained text crosses a joiner. Per-line `Alt` ladders
step the largest eligible entry first. Spill removes lower-priority entries only
after those ladders are exhausted; condensation then shortens the joined text.
Combining per-line alternatives with native `Paginate` remains on its compatibility path.

Portable `spill` and `best_effort` wrappers now use shared offers. Fields and lists
omit lower-priority entries with measured notices; best effort also permits prose
shortening and makes enclosing or directly wrapped character-budget minima advisory.
Hard caps still apply. Controls retain their labels and callbacks, and strict mode
rejects actual content loss rather than an unused reservation. Permissions and
character caps survive entry into a checked alternative's presentation graph;
equivalent alternatives cannot independently weaken a source's hard budget floor.

## Application outcome and validation

Focused command help now uses a shared region instead of truncating the command
description. A long-description regression reconstructs the complete text from all
certified pages and checks that the command heading remains visible. Build-browser
regressions exercise full catalogue/search pages, reference tabs, controlled record
filters, help toggles, and unchanged data-fetch counts. Application selection windows
remain application-owned.

Cross-target tests exercise independent coordinates, retained allocation reuse,
production lists, text roles, repeated headings, packed controls, choices, image
coverage, visible bindings, shared attachments, and concrete modal/legacy-fallback
refusals. Mounted Discord regressions cover devtools controls and pressure-driven
action grouping. The combined UI and application run passed 3,066 tests with `--no-cov`.
After the final classic primitive-route change, 217 classic and pagination checks
passed; the final control-order and Slack continuation changes passed 64 focused
checks. Whole-project Pyrefly reports zero errors. Installed Ruff/format hooks and
`git diff --check` pass. Async delivery tests ran outside the sandbox because its
thread wakeups stall the file-delivery tests, as observed in earlier phases.

The roadmap remains open. The implementation gaps above are phase 6 work, not
accepted reductions of scope. The original completion claim was too broad.

## Remaining implementation sequence

1. Translate the remaining legacy adaptation permissions and fragment policies explicitly;
   remove each remaining pagination compatibility route once its contracts pass.
2. Integrate the remaining native layouts and extensions with typed target offers.
3. Re-run target and application conformance and reconcile the final matrix before
   marking phase 6 complete. Platform limits remain distinct from implementation gaps.


## Continuation: palette and status/progress integration

Palette overrides now travel with semantic occurrences into fixed and automatic
search, including when an alternative introduces a separate presentation graph.
Nested overrides restore the enclosing palette for subsequent siblings. Production
migration accepts themed containers, and retained allocations compare their
palettes so a changed theme is certified again before delivery.

Status and progress are shared atomic realizations. HTML retains status roles,
tone/emphasis and resolved colour, and native progress values, maxima and accessible
labels. Text-only targets display the authored progress quantities instead of
rounding them to a percentage. These nodes do not authorize truncation.

The core UI suite and focused Discord region/pressure suites pass (1,164 tests).
Whole-project type checking found no errors in these changes, but currently fails
on an unrelated in-progress range-publication test comparing an optional SQL scalar
to an integer. Phase 6 remains open for the other integration work above.

## Continuation: section and article thumbnails

Thumbnail placement and costs now participate in shared composition. Tests cover
repeated headings, reserved capacity, nested V2 sections, explicit classic/Slack
limitations, and retained-allocation invalidation when an image changes. The core
UI suite passes (1,143 tests), and whole-project Pyrefly reports zero errors.

## Continuation: native entity controls

Entity selection now uses shared offers, including typed defaults, ownership
callbacks, conversation filters and target cardinality limits. Unsupported native
families can use explicitly authored enumerated choices. Slack reports missing
role/mentionable discovery or incompatible conversation filters directly. Controls
on other pages produce no live bindings; session selection changes rebuild output.
The core UI and focused Discord entity/region suites pass (1,207 tests), and
whole-project Pyrefly reports zero errors.

## Continuation: native Discord controls

Discord buttons, form triggers, string/entity selectors, exact rows and automatically
packed control groups now participate directly in shared composition. Exact rows
cannot split or exceed native limits. Control groups expose legal page boundaries,
and only visible controls publish callbacks or form bindings. Checked alternatives
validate native interaction changes and preserve exact row boundaries.

Native text, headings, code and footers with `Never` overflow also compose directly.
Keyed document roots can now contain local pagers with independent positions; the
legacy prohibition on simultaneous root and local pages no longer applies to shared
layouts. A crowded toolbar and long code regression reconstructs all controls and
all text across the resulting page family. Legacy reports and small choice controls
may change representation when their enclosing layout first enters shared planning.
Both UI suites pass (3,173 tests with `--no-cov`), and whole-project Pyrefly reports
zero errors.

## Continuation: replacement ladders

Legacy `fallback` branches and field value ladders now use shared offers. Selecting
a replacement records a `Replacement` effect; strict mode requires the primary to
fit or an explicitly equivalent checked alternative. Only the selected branch
publishes bindings, assets and pagers. Field labels remain intact, and replacement
effects identify their values. HTML measures visible text for authored resource
budgets without imposing a global message limit.
Classic Discord retains native embed fields, including on continuation pages.
Names, values, per-embed counts and aggregate embed text all participate in shared
resource accounting.

After a fixed layout fails, search probes intermediate and final ladder choices
before enumerating all combinations. This finds useful text allocations when
several blocks share a tight budget, as well as certificates for independent field
ladders. The work allowance accounts for their combined text choices. These probes
change work order only; exhaustive comparison remains queued, and the search report
distinguishes a certificate from proven optimality.

Both complete UI suites pass (3,202 tests with `--no-cov`), and whole-project
Pyrefly reports zero errors.

## Continuation: navigation ownership

Navigation now offers individual buttons and grouped selectors through shared
search. Destination options retain their owner in the semantic graph, so checked
alternatives cannot silently replace callbacks. Crowded menus use an independent
layout pager and preserve their selected destination across page turns. HTML keeps
its navigation element and current-page marker. Exact legacy cuts may change;
coverage and native option limits remain enforced.

## Continuation: disclosure visibility

Closed disclosures expose their toggle but no body bindings or pager updates.
Opening them makes the body available to shared search; continuation pages repeat
the toggle. Closing a disclosure preserves previously delivered nested cursors,
so reopening restores the remembered page. HTML keeps native details/summary
markup but now materializes body content only while open, matching mounted state
ownership across targets. Public disclosure and navigation control keys remain
stable where their source identity is unambiguous.

## Continuation: item browsers

Item overviews and opened details now use shared allocation. The overview retains
its keyed item anchors and summaries, and the opened detail repeats its heading
and back control. Overview, detail and nested content cursors remain independent;
opening another item retains the hidden cursor without publishing a new position.
Controlled callbacks and initial opened displays preserve their existing ownership.
Local item and destination pagers retain default navigation when a mount supplies
no factory. All targets now compose select-page chrome as part of the selected page.
Progress text retains both exact quantities and the percentage bars used by tallies.

Both complete UI package suites pass after these changes (3,132 tests with
`--no-cov`). Whole-project Pyrefly reports zero errors.

## Continuation: optional content

Optional content now participates in shared search, including its authored
importance. Omission removes live bindings and reports the exact affected sources;
strict mode refuses it. Lossless page families precede omission. All five document
targets share these rules, while unsupported children still identify the remaining
compatibility route. The core UI suite passes (1,266 tests with `--no-cov`), and
whole-project Pyrefly reports zero errors.

## Continuation: prose truncation

Semantic truncation and native Discord `Truncate` text now participate in shared
search across the five document targets. Shortening preserves the requested end,
balanced styling and authored importance; it reports source-specific effects.
Lossless page families remain preferable, strict mode rejects shortening, and
zero-character grants remove prose without leaving formatting behind. Control
labels and behavior remain unchanged. Rich inline shortening still identifies
the unsupported feature rather than flattening it.

Both UI suites pass (3,226 tests with `--no-cov`), with three additional zero-grant
regressions passing afterward. Whole-project Pyrefly reports zero errors.

## Continuation: native V2 layouts

Native sections, panels, thumbnails and separators now have shared offers and
independently validated costs. Exact section boundaries survive checked
alternatives, while panel continuations retain their metadata and only publish
visible accessory bindings. Invalid native structure is refused, including
nested containers and pagination inside a section's text slots.

Both UI suites were exercised; the section-pagination regression found there was
fixed and its focused suite passes. Build-browser and search-help application
tests pass, and whole-project Pyrefly reports zero errors.

## Continuation: search reference migration

Search help now declares its structured fields and compact prose as equivalent
presentations of the same entries. The compact form works in strict mode under
component pressure. Production checked alternatives enter shared planning
directly, including inside the search screen's native section header. Application
tests assert shared allocation for help beside a selected build and crowded
catalogue/search results, without refetching application-owned pages.

Both UI suites pass (3,245 tests), as do the build-browser and search-help tests.

## Continuation: HTML character pages

HTML `Paged` now uses the shared visible-text resource contract. Authored character
caps exclude navigation chrome; rich prose can split without losing styling, and
unbreakable groups remain intact. Minimum-fill preferences and routed page
ownership use the same allocation rules as the other document targets. Exact cuts
may differ from the former HTML balancer. The HTML, shared-search and target
composition tests pass, and whole-project Pyrefly reports zero errors.

## Continuation: static line collections

Native static lines now compose through complete-entry fragments or declared
loss effects. Tests cover separator-aware truncation, original source identity
through checked presentations, spill notices, whole-collection drops, strict
mode, and reconstruction across pages. Both UI suites pass (3,267 tests), with
the additional source-mapping regression passing afterward. Whole-project Pyrefly
reports zero errors.
