# TODO

## Record rules

- [ ] Populate restriction implications and incompatibilities.
  - Examples include extender `SEAMLESS -> FLUSH`, restriction tiers, and skydoor incompatibilities.
  - Apply the semantic closure when qualifying builds and generating category subsets.
- [ ] Store authoritative cumulative-volume measurements instead of deriving every volume from bounding dimensions.
  - Support hallway, frame, entity-hitbox, contained-volume, and other Door Rules exceptions.
- [ ] Implement expandable-volume records.
  - Store control-volume and per-layer expressions.
  - Store valid expandable domains.
  - Compare piecewise expressions and persist record-breaking ranges and co-holders.
- [x] Implement the remaining record classes.
  - `FIRST`
  - `FASTEST SMALLEST`
  - `SMALLEST FASTEST`
- [ ] Add calculators and typed categories for entrances and utilities.
- [ ] Add piston-extender create/update submission persistence; record computation and read/search mapping are supported.
- [x] Implement the fixed-size Door Rules title formatter.
  - Includes trapdoor rewrites, animated restrictions, named layouts, canonical aliases, conflict diagnostics, and
    current-version `[BROKEN]` presentation.
- [ ] Add algebraic and minimal expandable-size title forms after expandable-volume records are modeled.
- [x] Add an animated-restriction taxonomy type.
- [ ] Decide which general eligibility rules remain moderation responsibilities and which need structured evidence.

## Search

- [ ] Connect a semantic candidate provider to the cross-resource search backend.
- [ ] Process `search_embedding_queue` items and store embeddings for projected search documents.
- [ ] Consider dynamic `facet.<name>` filters so new projected facets do not require a field-registry code change.
- [ ] Add live PostgreSQL integration coverage for exact, full-text, trigram, facet, RRF, and cursor behavior.
- [ ] Add autocomplete for field names, taxonomy values, and canonical record base keys.
- [ ] Replace raw showcase qualifiers with parameterized taxonomy tags.
  - Store metric kind, value, unit, display order, and evidence.
  - Consider Pareto-frontier calculation for builds with multiple showcase metrics.

## Record operations

- [ ] Persist failed computation attempts for auditing instead of only rolling back and retrying.
- [ ] Improve co-holder history from a linear predecessor chain to an explicit set-based transition model.
- [ ] Add moderation tooling for missing completion dates, timing evidence, volume evidence, and version support.
- [ ] Add a friendly category selector for `/records lookup` instead of requiring a canonical base key and numeric IDs.
- [ ] Add integration tests for record recomputation triggers, queue retries, advisory locking, and atomic activation.

## Migration and cleanup

- [ ] Remove legacy smallest-door tables, columns, functions, and triggers after the new projections have run in production.
- [ ] Remove compatibility reads from legacy description and timing fields after backfill verification.
- [ ] Snapshot managed function and trigger SQL inside historical migrations so later registry edits cannot alter migration replay.
- [ ] Migrate primary keys from sequential integers to UUIDv7 and add human-readable slugs. **Do this before the next
      alpha test** — once external users hold IDs and URLs, the migration stops being a schema change and becomes a
      data-compatibility problem.
  - UUIDv7 keeps index locality while removing the enumerable, guessable IDs currently exposed through the REST API.
  - Slugs give stable, shareable URLs that survive re-keying; `permission_roles.slug` is the pattern to follow.
