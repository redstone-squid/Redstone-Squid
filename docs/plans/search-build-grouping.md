# Search results grouped by build

Status: UX proposal for #133; runtime behavior is unchanged.

`/search` should help users find builds. A build that holds several matching records should occupy one
result, with those records available as reasons it matched. `/records` remains the place to browse titles
and their standings. The companion `search-build-grouping.html` is an interactive mockup with invented data.

## Proposed interaction

Keep the existing scope choices. Records finds builds through matching records, Builds finds builds
directly, and Everything combines those matches with independent metadata results. Changing the default
scope or renaming the choices is a separate decision; this proposal retains the current Records default.

Use the scope to choose the result heading and supporting titles. Do not prefix the titles with “Matches:”.

| Scope | Main title | Identity line | Record titles below |
| --- | --- | --- | --- |
| Records | Best matching record title | Build #ID, followed by the build title only when it differs from the heading | Remaining matching record titles |
| Builds | Build title | Build #ID | Records held by the build |
| Everything | Build title | Build #ID | Records held by the build |

In Records, choose the heading from the strongest matching record, with a stable record-ID tie breaker.
Keep the remaining titles in a compact list, preserving their full names rather than shortening them to
“Smallest” or “Fastest”. Start with two supporting titles and a remaining count when more are available;
the detail action exposes the complete list. A build with no records needs no placeholder text.
In Everything, omit a lone supporting record title when it repeats the build heading. Keep a differing
record title, and retain the record and its details even when its repeated display text is omitted.

Bold the query words within displayed titles, including a differing build title on the identity line.
Keep the rest of each result heading at regular weight so the emphasis remains visible. The mockup
illustrates `2x2` matching the displayed `2×2`. Production highlighting must use actual match information,
where available, with conservative best-effort text matching as a fallback. Escape display text and support
normalization without inventing literal matches for semantic results.
Record titles shown as context in Builds or Everything should not receive emphasis unless they actually
match. Bold is the initial proposal; color is optional reinforcement where the rendering platform supports it.

Selecting a result opens build details, with a separate record-detail action showing titles, category,
version scope, metrics, and tied holders. Label this “Matching records” in Records and “Records” in the
other scopes. Returning to results preserves the query, scope, and page. Metadata keeps its own result
and detail view.

Load record titles independently of build titles. The current record projection uses the first holder's
build title, so it cannot supply either a general record heading or every tied holder's build title correctly.

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
position. Records scope includes only matching records. Builds and Everything also show held records as
context, even if those records did not cause the build to match; this enrichment does not affect ranking.

## Match information and highlighting

Extend the result contract alongside grouping; replacing the search algorithm is not a prerequisite.
`PostgresSearchBackend` already separates exact-title, full-text, trigram, and semantic candidates, but
`reciprocal_rank_fusion` returns only identity, title, and fused score. The compiler separately applies
the complete query predicate. Preserve contributing document IDs and ranking branches through grouping;
branch participation explains a ranking contribution, not which query clauses or displayed words matched.
Sorted and filter-only searches have no ranking branches and must still produce usable grouped results.

Keep source-match information separate from optional display highlights. Return the contributing build
and record identities, known matching fields or clauses where available, and text spans tied to the exact
display string. Distinguish verified spans from best-effort spans internally, and permit an empty set.
This lets Discord and the web share matching logic without passing rendered markup between them.

Start with normalized literal occurrences of positive query terms in eligible fields. Preserve field
constraints and boolean structure: do not highlight excluded terms or use the flattened ranking text as
an explanation of the full query. Held records added only as context must not be reported as contributing
matches. If displayed text differs from the indexed snapshot, recompute its highlights or omit them.

PostgreSQL's [`similarity`](https://www.postgresql.org/docs/current/pgtrgm.html) returns a score rather than
matched character positions. Keep trigram provenance, but leave word emphasis absent when there is no
confident textual correspondence. Semantic ranking likewise needs no invented word-level explanation.
Full-text [`ts_headline`](https://www.postgresql.org/docs/current/textsearch-controls.html#TEXTSEARCH-HEADLINE)
can help locate query words, but it highlights words even when their positions do not satisfy query
restrictions; its output is not proof that a clause matched and is not safe to render as raw HTML.
Evaluate richer full-text highlighting later, on returned results only. Grouping and pagination must work
even when highlighting cannot explain a result.

## Implementation sequence after UX review

1. Define a grouped search result containing a build identity, its matching record identities, and match
   information with optional best-effort highlighting, plus a separate metadata result. Load held-record
   context for Builds and Everything. Retain all holder identities and their display data rather than taking only
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
   holderless titles, visibility, version scopes, stable ordering, and long summaries. Check scope-specific
   headings, identical versus differing build titles, unmatched contextual titles, and normalized match
   highlighting. Cover omitted duplicate titles, fuzzy matches with no literal span, field-qualified and
   negated terms, and results without ranking provenance. Exercise the actual Discord layout at narrow
   widths and with enough records to overflow the detail view.

## Review gate

Review the mockup's result summaries, matching-record details, and tied-holder navigation first. The next
implementation decision is how grouped results enter the shared API without unexpectedly changing existing
record-oriented clients. The mockup illustrates product behavior, not Discord component feasibility or a
finished API contract.
