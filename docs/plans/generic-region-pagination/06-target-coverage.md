# Target coverage and migration

Status: complete. Shared document pagination covers all five document targets, including native Discord ladders, pagers, and contracted extensions. The capability matrix records target restrictions.

## Evidence and decisions

Phases 2–5 supplied the target-neutral solver. Phase 6 extended production selection
to classic messages and Slack Home, with direct composition rules for each. Slack modals are
form submission surfaces: their existing contract requires one top-level form
trigger, rather than an independently navigable document. Preserve that contract
and report it explicitly when region pagination is requested.

Extend the existing realization boundary, keeping target assembly outside search.
Share Slack block evaluation between message and Home surfaces. Classic Discord
must account separately for content, embed text, rows, controls, and attachments.
Do not convert a completed V2 scene into classic output or a completed HTML scene
into Slack output.

Expand shared vocabulary where semantics and target resources can be preserved.
Native extensions and authored adaptation ladders now supply explicit composition
contracts; equivalence is not guessed and hard structure requirements remain enforced. Unsupported regions must report the missing capability,
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
| Discord V2 | Regions, root pages, lists, character-based `Paged`, and primitive `Paginate`; component, text, and attachment capacities. | Explicit nested blocks cannot become nested V2 containers. Nested semantic sections retain headings inside the enclosing panel. |
| Classic Discord | Regions, root pages, lists, count-based primitive `Paginate`; independent content, embed text, embed count, row, control, and attachment capacities. Blocks become embeds; loose prose uses message content. | Character grants have no single text pool. Inline download widgets and spoiler embed images have no classic representation; document attachments remain available. |
| HTML | Regions, root pages, lists and character-based `Paged`; visible text is measured for authored budgets, with no global message budget. | Native extensions need typed target rules. |
| Slack message | Regions, root pages, lists; block capacity, local text/control limits, and message fallback-text capacity. | No disabled buttons/selects, inline attachments, spoiler images, or accent-bearing panels. Images require public URLs. |
| Slack Home | Same direct block composition and navigation as messages, using Home's block capacity. | Same block vocabulary constraints; no message fallback-text budget. |
| Slack modal | Existing form planner and modal limits. | The surface requires one top-level form trigger. Region navigation is rejected with this reason; it cannot preserve a submitted form's ownership contract. |

Shared vocabulary now includes headings, paragraphs, notes, code, quotations,
fields, tables, lists, metrics, timestamps, images and figures, actions, choices, toggles, form triggers,
checked alternatives, regions, and the supported grouping/budget wrappers. Actual
realizations remain target-specific: for example, classic displays an image's
alternative description as embed text, and Slack requires plain text in headers
and native table cells. Slack rich inline text is encoded directly; combinations
that cannot preserve literal punctuation in mrkdwn are refused instead of silently
changing it. Section/article thumbnails repeat with their headings on every page.
V2 uses a heading accessory, classic uses the owning embed thumbnail, HTML keeps
the native section/article element, and Slack places an image after the heading.
Classic rejects thumbnails inside an enclosing embed; Slack rejects section accents.
Asides preserve their tone and palette colour across continuation pages on Discord
and HTML; Slack reports its missing accent capability. Nested Discord asides follow
the same flattening rule as semantic sections. HTML metrics retain definition-list
markup. Timestamps retain typed time metadata in HTML and V2, native date tokens
in classic Discord and Slack, and visible named zones for exact zoned times.
Native Discord `Time` and `ZonedTime` also compose directly.

Selectable grids now offer complete button rows and coordinate selectors through
shared allocation. Every cell retains its position, label, availability and callback;
selector pages include their neighbouring unavailable cells as context. HTML keeps
grid rows in table markup. Local grid pages retain the existing `.cells` cursor,
and only visible controls publish bindings. Checked alternatives preserve the cell
order and column count. Unchanged grids reuse their certified allocation; changing
their shape recomputes the presentation instead of retaining a legacy strategy ID.

Rosters retain slot counts, waitlists, member identities and join availability in
shared composition. Enclosing regions may split member lines; slot headings and
join controls repeat on continuations. Rosters do not introduce separate member
pagers. Existing spill permission reports omitted members, and strict mode requires
all displayed membership to survive. Full slots disable joining only when overflow
is rejected; a waitlisted slot remains joinable. Slack rejects disabled join
controls with its concrete capability reason.

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
publish bindings. Premium buttons retain their SKU in exact rows, automatic rows,
and section accessories; they require the target's premium-button capability and
introduce no application callback. A section text slot cannot host a pager's controls; paginate the
enclosing region instead. Opaque native extensions use the explicit composition contract described below.

Native budget and break wrappers also participate directly. Budgets enforce the
finite `preferred + stretch` ceiling; native `best_effort` only relaxes the minimum
reservation. Children still need their own permission to lose content. Break
annotations retain unbreakable groups and keep-with-next boundaries. Classic
Discord rejects native character reservations within shared regions because its
content and embed capacities are separate; use explicit resource budgets there.

Native V2 galleries and file widgets share the declared attachment pool. Exact
galleries remain indivisible; media collections pack images into target-sized
galleries and expose image boundaries to enclosing regions. Descriptions, spoiler
flags, and attachment identity survive page turns. Missing assets or mismatched
file metadata are infeasible rather than silently rewritten.

Classic native `Content` and `Card` now compose directly. Exact cards retain their
title, fields, author, footer, colour, media and timestamp; every text slot keeps
its overflow policy and local limit. Aggregate embed cost includes titles, authors
and footers. Cards remain indivisible, and message content repeats on keyed root
pages. Embed slots cannot host pager controls: paginate the enclosing cards.
Failures use shared constraint diagnostics rather than legacy fitter messages.

## Compatibility and extension contracts

`regions.py` and its backend-probing frontier search are removed. Discovery lives
in `region_detection.py`; unsupported explicit regions raise `JointUnsupportedError`
with diagnostics. Shared search failures never retry through a target fitter.

Section headings now repeat on continuation pages, including character-based
`Paged` sections. Classic loose prose uses the content pool in shared composition;
authored blocks use embeds. Page counts, exact cuts, and historical adaptation
event names may change. Reports use shared search metrics and fidelity effects.

Native text, headings, code and footers now expose their own pagination
contracts, including rich inline content. Fragments preserve styles and link
destinations, keep entity leaves intact, and rejoin adjacent styled fragments
before serialization. Count boundaries never split an entity identifier. The
synthetic `TextPages` migration bridge is removed. Native extensions still use
explicit typed composition rules; no equivalence is inferred from replacement text.

Item browsers and disclosures now expose only their open content to shared
allocation, retaining hidden layout cursors until that content reopens. Navigation now uses shared
offers with destination ownership separate from layout page coordinates. Palette overrides,
status/progress content and section thumbnails now use shared composition. The retained
fitters still serve ordinary layouts outside shared routing and unsupported compatibility
vocabulary. Native ladders, contracted opaque extensions, and unkeyed primitive pagers
now have shared routes; a failed shared search never selects a fitter.

Explicit prose truncation and native `Truncate` text now offer shared shortening
effects. Head/tail retention and importance survive migration, while strict mode
requires lossless output. Styling remains balanced and controls are never shortened.
Shortened links retain their destinations; entity leaves are retained or removed
whole. Checked text fragments report their actual retained range when a requested
cut falls inside an entity. Block-markup shortening still reports its unsupported
structure; use the corresponding semantic code or grouping node.

Native Discord text `Alts` and `Drop` policies also compose inside shared layouts.
Fallback rungs report replacement, shortening the final rung adds its own effect,
and whole-text drops report omission. An enclosing truncation wrapper retains its
policy precedence. Strict planning still requires the primary content to fit.
For single text nodes without entry ladders, `Condense` permits head shortening
and `Spill` permits whole-text omission, matching their existing behavior.

Static native `Lines` now expose complete-entry fragments and explicit spill,
truncation and drop offers. Lossless pagination precedes spilling; an omitted-entry
notice consumes real capacity. Omission and shortening reports identify the affected
lines, including when the retained text crosses a joiner. Per-line `Alt` ladders
step the largest eligible entry first. Spill removes lower-priority entries only
after those ladders are exhausted; condensation then shortens the joined text.
Native `Paginate` uses the same explicit line ladders, preserving keyed anchors,
count cuts, footers and page ownership. Lossless pages precede replacements. Checked
alternatives cannot remove the pager, and independent cursor fingerprints keep
changes to one line collection from resetting another.

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
coverage, visible bindings, shared attachments, and concrete capability refusals.
The final combined core, Discord, Slack and widget suites passed 3,668 tests with
`--no-cov`, including native adapter validation and component-pressure regressions.
Build-browser, schematic-access, search-help and command-help regressions passed
108 tests. Whole-project Pyrefly reports zero errors, installed Ruff/format hooks
pass, and `git diff --check` is clean. Async delivery tests run outside the sandbox
because its thread wakeups stall file delivery tests.

The complete HTML document conformance case now exercises shared planning. It
retains figure/caption markup and multiple-select names, accessible labels and
form metadata. Checked alternatives filter unsupported target presentations
individually; canonical content supplies identity and need not itself render on
the selected target. V2 panels, classic cards and portable presentations can share
one checked group without losing their source correspondence.

## Completed integration contracts

Native `Variants` now converts to checked alternatives. Authors can provide canonical
`content`, per-rung `correspondence`, and a `contract` naming losses or interaction
changes. Without explicit content, the first rung supplies the source, including its
hard structural boundaries. Capability requirements filter candidates before search.
Exact presentations precede reformatted ones, and both precede declared loss. Strict
mode permits preservation through reformatting. Role panels and regression fixtures
now declare their actual source and control contracts instead of relying on fidelity
flags to hide missing content or changed behavior.

Component-pressure search caches discovery per immutable occurrence and tries a
structural repair chain before broad compact probes. Ordinary candidates stay in the
frontier, and bounded exhaustion retains its existing meaning. Priority determines
which eligible ladder changes first; the planner no longer compacts every ladder
before considering a sufficient partial repair.

V2 `RawItem` supports `NativeComposition`: an atomic resource contract, visible text,
shared attachments, legal slots, and an independent native measurement callback.
`Extension` adapters can implement `ComposableExtension.compose` to supply it. The
Discord `NativeItem` adapter now does so: it prepares items once, validates detached
native trees, rejects nested containers and undeclared attachment references, and
shares attachment costs across visible widgets. Native callbacks remain native;
Squid-managed bindings require typed control offers. Other targets use an extension's
portable fallback or explicit target rules, since a Discord widget is not a portable
representation.

Unkeyed primitive pagers use scoped positional identity. Explicit keys remain the
choice for ownership that must survive reordering. Whole-collection `Lines` alternatives
are atomic source units: replacement and final-rung shortening report collection loss,
while per-entry ladders retain individual line identities. Source text references can
copy the collection's primary text without inventing per-line correspondence.

The superseded region solver and `TextPages` bridge are removed. Remaining target
fitters are active compatibility code, not abandoned region implementations. Public
architecture documentation now describes shared work accounting, certification, native
contracts, and positional pager ownership. No implementation steps remain in phase 6;
Slack modal ownership and the target restrictions above are deliberate capabilities.
