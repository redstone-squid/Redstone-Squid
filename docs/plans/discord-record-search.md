# Restore and clarify Discord record search

Keep typed record search in Discord `/search` in step with the Records tab, then make
each result say why it matched. Decisions are recorded with the steps.

## Findings

Measured against the live catalogue on November 26, 2026, after incremental records
were deployed. The examples were checked again then.

- **Index lag hid current records.** The incremental records migration bulk-deleted
  superseded results. Its row triggers queued about 579,000 record requests, and
  only about 45,000 had anything to do. They sat at the back of a queue the worker
  drained at three per second, roughly 39 hours away. Until then, search served
  pre-deploy documents for current results, 222 current results had no document, and
  32,372 documents still pointed at deleted `no_candidate` results. The no-op requests
  were deleted by hand, and `1ebc0a9c7` and `58c1c8ec0` fix the causes.
  [Record trigger](../../squid/core/persistence/postgres_entities/functions/enqueue_computed_record_search_projection.sql) ·
  [Projection run](../../squid/core/search/application/projection.py)
- **Free text blends category and holder facts.** `no furnaces` matches
  `result:632822`, **Fastest 2x2 Glass Door · No Entities**, through holder `#8329`'s
  build title. Nothing in the result says so. The unrestricted variant is
  `result:629507`, held by `#9047`.
  [Projection](../../squid/core/search/infrastructure/projection.py)
- **Highlights cover only AND terms in the title.** The highlighter returns nothing
  for an OR, so `fastest 2x2 (glass OR door)` bolds only **2x2**. Subtitles, holders,
  and aliases are never highlighted.
  [Highlighter](../../squid/core/search/application/highlighting.py)
- **Quoted text still matches fuzzily.** Title and text predicates OR the phrase
  query with trigram `similarity > 0.1`. As a result, `title:"glass door"` admits
  **5x5 Door\* · Locational** (`#3292`).
  [Compiler](../../squid/core/search/infrastructure/compiler.py)
- **Totals count a capped pool.** Each ranking branch contributes at most 200
  candidates. The displayed total and the page count describe that pool, not the
  full set of matches. [Ranking](../../squid/core/search/infrastructure/repository.py)
- **Broad searches repeat.** Build `#9316` holds 509 current records across eight
  titles. 1,716 of 13,890 holders have a null volume.

## Steps

1. **Ship the queue fixes and watch coverage.** Deploy the two commits above. Add
   two figures to the `queue-health` job: current results without a document, and
   the age of the oldest pending record request. When those are above zero, Discord
   shows "Records are still being indexed" with an empty or short record result,
   instead of the plain empty state.
2. **Fix phrase matching and OR highlights.** These are independent of the other steps.
   - A quoted value compiles to `phraseto_tsquery` plus exact equality, with no
     trigram branch. Unquoted text keeps fuzzy matching.
   - The highlighter marks a term from an OR branch when that term occurs in the
     text. Negated terms stay plain.
3. **Keep the source of each match.** Index category text (title, subtitle,
   restriction names and aliases) separately from holder text (build title,
   creators).
   - Bare free text matches both. A bare alias such as `entityless` matches the
     category, so it finds **No Entities**.
   - Category matches rank above holder matches.
   - `restriction:` filters only on the category. A holder's build title never
     satisfies it.
4. **Show the match reason in Discord.** Carry match spans and their source into the
   hit. Highlight spans in the heading and subtitle. A holder-only match adds a line
   such as "Matched holder #8329: …". Show the qualifying holder with its
   measurements, and show a null volume as unmeasured rather than as zero.
5. **Report bounded totals honestly.** When any ranking branch reaches its cap,
   show the total as "200+" (or the pool size followed by "+"). Navigation pages
   only through the pool.

Grouping many records held by one build is a screen design question. Plan it
separately against [the bot screen guide](../bot-screens.md) once steps 3 and 4 show
what a row must carry.

## Verification

- After the deploy, the record request queue empties within minutes. Every current
  result has a document, and no record document points at a deleted result.
- In Discord, search Records for `fastest 2x2 glass door` and check that both
  `result:629507` and `result:632822` appear. Check the reason shown for
  `no entities`, `entityless`, `restriction:entityless`, `no furnaces`, and
  `fastest 2x2 (glass OR door)`.
- Search Builds for `title:"glass door"`, and check that `#3292` is gone and the
  literal phrase is highlighted. For `2x2 door`, check that paging agrees with the
  displayed total.
