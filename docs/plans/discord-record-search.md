# Restore and clarify Discord record search

Restore record coverage in Discord `/search`, then make results explain what matched.
Use existing catalogue data to verify coverage, ranking, highlighting, and how the
list behaves when a build holds many variants of the same title.

## Findings from November 22, 2026

- **The Records tab and typed search diverged.** The live database had 46,447
  active published results (11,940 resolved, 33,841 without a candidate, and
  666 unresolved) and zero record search documents. The shared projection queue
  held 173,181 record jobs, including 126,734 for inactive results. Across two
  observations, the build queue fell from 2,937 to 2,587 while the record count
  stayed flat. At the second observation the first 50 available jobs were all
  builds, and no build or record jobs were dead-lettered. Every active holder
  already had an approved build document. The catalogue can browse published
  results directly, but typed record queries use the search index and show the
  ordinary empty state. [Discord catalogue](../../squid/bot/catalogue/browse.py) ·
  [Queue ordering](../../squid/core/persistence/queue.py)
- **Free text blends category and holder facts.** In a replay of the deployed
  projection loader and PostgreSQL text matching, record `result:586375`,
  **Fastest 2x2 Glass Door · No Entities**, matched `no furnaces` through holder
  `#8329`'s build title. The category has no such restriction. The same record
  projected `Entityless` as a structured restriction alias, but the bare alias
  did not match its heading or holder text. The unrestricted
  `result:583060` shares the title and has a different holder, `#9047`.
  [Projection loader and builder](../../squid/core/search/infrastructure/projection.py)
- **Highlights cover only part of the match.** For `result:586375`,
  `fastest 2x2 glass door` bolds **2x2 Glass Door** because `fastest` becomes
  `criterion:fastest`. The visible `No Entities` subtitle and the holder line
  receive no highlights. `fastest 2x2 (glass OR door)` bolds only **2x2** because
  the highlighter skips OR branches. The heading is rendered without the match
  evidence from the subtitle, holder title, creator, or structured alias.
  [Title highlighter](../../squid/core/search/application/highlighting.py) ·
  [Discord result rows](../../squid/bot/catalogue/search/results.py)
- **Quotes and relevance counts can mislead.** With Discord's grouped-build
  settings, `title:"glass door"` admitted 7,345 build documents before ranking,
  including `#3292`, **5x5 Door* · Locational**, and returned a relevance total
  of 297. `2x2 door` admitted 692 and returned a relevance total of 333.
  The title predicate allows trigram matches for quoted terms, and each ranking
  branch contributes at most 200 candidates. Pagination and totals operate on
  the resulting candidate pool. [Query compiler](../../squid/core/search/infrastructure/compiler.py) ·
  [Ranking](../../squid/core/search/infrastructure/repository.py)
- **Record rows can repeat and include unawarded categories.** Discord record
  search returns individual achievements. Build `#9316` holds 248 active
  records across four distinct titles and 62 distinct nonempty subtitles.
  The loader also projects `no_candidate` results such as `result:628220`
  with no holders. Of 11,940 active holder snapshots, 739 lacked a numeric
  volume, so some held records will also have no value for the volume filter
  even after indexing. [Catalogue request](../../squid/bot/catalogue/browse.py) ·
  [Holder projection](../../squid/core/search/infrastructure/projection.py)

The evidence comes from read-only queries against the live catalogue and replays
using the deployed code with Discord request settings. The empty index prevented
a live comparison of record rankings. Verify those rankings and the rendered
slash-command flow in Discord once representative records are indexed.

## Implementation order

1. **Restore active record coverage.** Compare active result IDs with indexed
   IDs and classify queued work by whether the result is active and whether a
   stale document needs deletion. Prioritize active projections and preserve
   deletion of stale documents. Review run activation and build refresh fan-out
   so subsequent publications do not bury current results behind obsolete work.
   Preserve retry and concurrent enqueue semantics. Track queue age, active
   coverage, and failures by resource kind. Give Discord an index-lag state
   when missing coverage makes an empty result unreliable.
2. **Keep the source of each match.** Index record headings, category
   restrictions and aliases, and holder text with enough provenance to
   distinguish them in search. Give category matches priority for record
   queries. Keep structured restrictions on the category and measurements on
   the qualifying holder. Make free-text alias behavior deliberate and verify
   that a match through a holder's restriction cannot be read as a category
   restriction.
3. **Align matching, ranking, and pagination.** Enforce phrase semantics for
   quoted text and keep fuzzy matching available for unquoted searches. Test
   whether the general category appears ahead of restricted variants for a
   general query. Make the result count and navigation describe the actual
   pageable set, including when ranking is bounded.
4. **Render the evidence in Discord.** Carry verified match spans or a
   structured match reason into the result. Show it where the user can see it,
   whether it came from the title, subtitle, criterion, category alias, or a
   holder. Mark only satisfied OR branches and keep negated terms plain. Show
   the qualifying holder alongside its measurements. Keep achievements
   identifiable by category and version when several share a display title.
5. **Reduce repetition in broad record searches.** Evaluate grouping related
   achievements while preserving individual categories, holders, version scopes,
   and detail links. Make the default visibility of unawarded and unresolved
   results explicit, and verify how they appear when requested.

## Verification with existing catalogue data

- Compare active published, indexed, and queued counts by status before and
  after catch-up. Check that a newly published result becomes searchable while
  older work is pending, and verify that a superseded result leaves search.
- In Discord, browse to **Fastest 2x2 Glass Door**, then search for it with
  explicit Records scope and again using the leading `fastest` shorthand.
  Check the unrestricted and `No Entities` variants. Try `no entities`,
  `no furnaces`, `entityless`, `restriction:entityless`, and
  `fastest 2x2 (glass OR door)` and check the reason displayed for each hit.
- Search Builds for `title:"glass door"` and `2x2 door`. Verify the
  quoted query excludes `#3292`, paging agrees with the displayed count,
  and literal matches are highlighted in the result rows.
- Browse results involving `#9316` and the unawarded extender category
  `result:628220`. Check that grouping and status treatment let users find
  the intended achievement, and that a missing volume stays distinguishable
  from a measured value.
