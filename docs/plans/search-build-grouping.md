# Search results grouped by build

Status: UX proposal for #133; runtime behavior is unchanged.

`/search` should help users find builds. A build that holds several matching records should occupy one
result, with those records available as reasons it matched. `/records` remains the place to browse titles
and their standings. The companion `search-build-grouping.html` is an interactive mockup with invented data.

## Proposed interaction

Keep the existing scope choices. Records finds builds through matching records, Builds finds builds
directly, and Everything combines those matches with independent metadata results. Changing the default
scope or renaming the choices is a separate decision; this proposal retains the current Records default.

Each build result shows its title, ID, and a short summary of matching records. Show at most two record
labels and a remaining count. Selecting the result opens build details, with a separate Matching records
action. That action shows the complete matching titles, category, version scope, metrics, and tied holders.
Returning to results preserves the query, scope, and page. Metadata keeps its own result and detail view.

Use the authoritative build title for the heading. The current record projection uses the first holder's
title, so it cannot supply the correct heading for every tied holder.

| Situation | Proposed behavior |
| --- | --- |
| One build holds several matching records | One build result; retain every distinct matching record in its details. |
| A record has tied holders | Include each eligible holder once; show the tie and links to the other holders. |
| A build matches directly and through records | One result; show its matching records alongside its build details. |
| A matching record has no holder | No build result; the title remains discoverable in `/records`. |
| The same title exists in several version scopes | Keep the scopes distinct in matching-record details. |
| Everything also matches a pattern or restriction | Keep the metadata result separate from the build results. |

Counts and pagination describe grouped results. Records and Builds say “builds”; Everything says “results”
and distinguishes builds from metadata. Opening another holder must not discard the original result-page
position. Only records matching the query appear under Matching records; do not imply that this lists every
record the build holds.

## Implementation sequence after UX review

1. Define a grouped search result containing a build identity and its matching record identities, plus a
   separate metadata result. Retain all holder identities and their display data rather than taking only
   `holder_build_ids[0]`. Apply build visibility rules to every holder.
2. Implement grouping before totals and pagination in the shared search backend. Handle relevance,
   filter-only, and explicit-sort paths together. Deduplicating one fetched page cannot provide correct
   totals or prevent repetition on later pages.
3. Order relevance groups by their strongest matching document, with a stable build-ID tie breaker, so
   holding more records does not itself boost a build. For explicit sorts, propose the minimum matched
   value ascending and maximum descending, with missing values last. Confirm that policy for record-only
   fields before implementation. Preserve the ranked candidate limit and describe any bounded totals
   accurately; collect complete matching-record details separately if the candidate pool truncates them.
4. Adapt the Discord result browser and its back navigation. Review the API and web consumers explicitly:
   the shared service currently exposes record results, so changing its return type requires a coordinated
   contract change or an opt-in grouped mode.
5. Verify duplicate records across page boundaries, tied holders, direct-plus-record matches, metadata,
   holderless titles, visibility, version scopes, stable ordering, and long summaries. Exercise the actual
   Discord layout at narrow widths and with enough records to overflow the detail view.

## Review gate

Review the mockup's result summaries, matching-record details, and tied-holder navigation first. The next
implementation decision is how grouped results enter the shared API without unexpectedly changing existing
record-oriented clients. The mockup illustrates product behavior, not Discord component feasibility or a
finished API contract.
