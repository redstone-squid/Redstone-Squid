# Search results grouped by build

Implemented for #133. `/search` returns one result per build, with its matching records retained as
context. `/records` continues to browse titles and standings. The companion `search-build-grouping.html`
illustrates the interaction with invented data.

## Result presentation

Records finds builds through matching records, Builds finds builds directly, and Everything combines
those matches with independent metadata results. Records remains the default scope.

| Scope | Main title | Identity line | Record titles below |
| --- | --- | --- | --- |
| Records | Best matching record title | Build #ID, followed by the build title only when it differs from the heading | Remaining matching record titles |
| Builds | Build title | Build #ID | Records held by the build |
| Everything | Build title | Build #ID | Records held by the build |

Records chooses the strongest matching record, breaking score ties by record ID. The summary keeps full
record names, shows at most two supporting titles, and gives a remaining count. Builds and Everything
omit a lone supporting record title when it repeats the heading. The record remains accessible in details.
A build without records has no placeholder description.

Selecting a result opens its build card. “Matching records” in Records, or “Records” in the other scopes,
opens a paged list of record titles. Each record exposes its version scope, metrics, and other visible
holders. Returning from a holder preserves the selected record; returning to search preserves its page.

Each eligible tied holder receives its own build result. A matching record without a visible holder
produces no build result. Different version scopes remain separate records. Metadata remains independent
of builds even when its title overlaps a build or record title.

## Grouping and pagination

`SearchRequest.group_by_build` opts into grouping. `/search` enables it; `GET /v1/search` exposes the same
optional parameter, defaulting to false for existing record-oriented clients. The generated API client
includes the new fields. Existing web searches and the record standings browser retain their current mode.

`ResultPager` pages every search, grouped or not. When grouping, it joins indexed record-holder facets to
build documents and applies visibility to every holder; otherwise each matching document is its own
result. It groups before counts, offsets, and limits. Relevance uses each group's strongest document
score, so more record titles do not by themselves boost a build. Explicit sorts use the minimum matched
field value ascending and the maximum descending, putting missing values last. Ties are ordered by title
and then by the result's document ID.

Ranked totals describe groups represented in the bounded candidate pool. Sorted and filter-only totals
cover all matching groups. Record details are loaded separately for the selected builds, so the ranking
limit does not truncate their record lists. Records scope includes only matching records; Builds and
Everything also include held records as context without letting that enrichment change ranking.

Titles and eligibility reflect the search projections. Opening a build loads its current card and checks
that it is still confirmed. The API also hydrates build facts and drops highlights whose display title has
changed. Projection lag remains possible, as in the existing search index.

## Match information and highlighting

`SearchMatch` identifies a matching source document and the ranking branches that contributed when
relevance ranking was used. Branch membership explains a ranking contribution, not which words satisfied
the complete query. Additional matching records outside the candidate pool have no ranking branches.
Sorted and filter-only results likewise have no branch provenance.

`SearchText` carries the exact display string and optional best-effort spans using half-open Python
character offsets. Literal occurrences support case folding, accent normalization, and `2x2`/`2×2`.
Only unqualified text or title-qualified terms are considered. OR and NOT subtrees are omitted rather
than treating flattened ranking terms as proof that those branches matched. The UI applies bold through
structured text, preserving literal build titles instead of interpreting them as Markdown.

Trigram and semantic matches may have no highlighted words. PostgreSQL's
[`similarity`](https://www.postgresql.org/docs/current/pgtrgm.html) returns a score, not character offsets.
Full-text [`ts_headline`](https://www.postgresql.org/docs/current/textsearch-controls.html#TEXTSEARCH-HEADLINE)
is a possible later enhancement, but its word highlights do not prove that query restrictions matched and
its output must not be rendered as trusted HTML. Highlighting remains optional and does not affect ranking.

## Rollout and validation

Migration `a91c34e8b672` queues active record projections for refresh. The worker now indexes the record's
own title and keeps holder titles and metrics separately. Deploy the application and run migrations; the
new record headings become available as the projection queue drains. No new database columns are required.

Focused tests cover grouping across page boundaries, ties and visibility, sorting, bounded ranking pools,
record context, API compatibility, scope-specific headings, duplicate title suppression, escaped highlights,
and navigation through a tied holder. Discord layout checks include long record titles and thirty tied
holders. PostgreSQL tests also exercise the migration-backed projection queue.
