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
- [ ] Add live PostgreSQL integration coverage for exact, full-text, trigram, and RRF behavior.
  - Pagination and facet sorting are covered by `tests/integration/search/test_repository_pagination.py`.
- [x] Add autocomplete for field names, taxonomy values, and canonical record base keys.
  - See `docs/plans/autocomplete.md` for the source registry the three surfaces share.
- [ ] Replace raw showcase qualifiers with parameterized taxonomy tags.
  - Store metric kind, value, unit, display order, and evidence.
  - Consider Pareto-frontier calculation for builds with multiple showcase metrics.

## Record operations

- [ ] Persist failed computation attempts for auditing instead of only rolling back and retrying.
- [ ] Improve co-holder history from a linear predecessor chain to an explicit set-based transition model.
- [ ] Add moderation tooling for missing completion dates, timing evidence, volume evidence, and version support.
- [x] Add a friendly category selector for `/records lookup` instead of requiring a canonical base key and numeric IDs.
  - Autocomplete completes the base key, and restrictions are picked by name while still submitting IDs.
- [ ] Add integration tests for record recomputation triggers, queue retries, advisory locking, and atomic activation.

## Observability

- [x] Add a Discord command and API endpoint to retrieve a logged error by its reference/correlation ID.
  - `squid/diagnostics/` stores every captured failure in `error_reports` with the traceback, the redacted
    context, and the log lines the process emitted under the same correlation ID. `/error <reference>`,
    `GET /v1/diagnostics/errors/{reference}`, and `squid errors show` all resolve it, gated on
    `diagnostics.error.read`.
  - The two paths no longer track separate reference schemes: `correlation_reference()` shortens to 12
    characters, which is already the width of the untraced fallback, and lookup accepts either width.
- [ ] Integrate Sentry (or wire up a real log/trace backend) instead of the current no-op `debug` exporter in
  `deploy/otel-collector.yaml`.
- [ ] Let the worker and API processes use the human-readable log formatter in dev mode like the bot does.
  `configure_service_worker_logging` and `configure_api_logging` (`squid/logging_config.py:286`,`:302`) never
  pass `development_mode` to `build_logging_config`, so it defaults to `False` and `worker.log` is always raw
  JSON, unlike `configure_bot_logging` which threads `dev_mode` through and gets the `"default"` formatter
  locally.

## Schematic rendering

- [ ] Decouple on-demand bot rendering from automatic preview publication.
  - `SQUID_SCHEMATIC_RENDER_ENABLED` should gate renderer availability and `/build schematic render` without
    requiring a public API URL.
  - Make automatic preview publication explicit and require `SQUID_SCHEMATIC_RENDER_PUBLIC_BASE_URL` only for
    that publication path.
  - Remove the worker's direct dependency on API-shaped preview URLs; publish through an explicitly owned,
    reachable storage or delivery boundary.
  - Do not allow a placeholder URL to make a durable render appear successful while leaving Discord with a broken
    preview link.

## Creator identity

- [ ] Give creator profiles a first-party surface outside the REST API. `GET /v1/creators/{creator_id}`
      (`squid/api/v1/users.py:28`) is already unauthenticated, but nothing in `squid/bot/` ever calls
      `get_creator_profile`, so a public creator identity is unreachable from Discord.
  - Treating "who holds this creator credit" as staff-only information was a mistake: the profile is public data,
    and `accounts.public_creator_id` exists to be shown.
  - Blocks the useful half of `docs/plans/pr-183-review/01-consent-verification-ux.md` §5 — naming the creator who
    holds a contested alias only helps if the reader has somewhere to look the name up.

## Migration and cleanup

- [ ] Remove legacy smallest-door tables, columns, functions, and triggers after the new projections have run in production.
- [ ] Remove compatibility reads from legacy description and timing fields after backfill verification.
- [ ] Snapshot managed function and trigger SQL inside historical migrations so later registry edits cannot alter migration replay.
- [ ] Migrate primary keys from sequential integers to UUIDv7 and add human-readable slugs. **Do this before the next
      alpha test** — once external users hold IDs and URLs, the migration stops being a schema change and becomes a
      data-compatibility problem.
  - UUIDv7 keeps index locality while removing the enumerable, guessable IDs currently exposed through the REST API.
  - Slugs give stable, shareable URLs that survive re-keying; `permission_roles.slug` is the pattern to follow.
