# TODO

## Record rules

- [ ] Populate restriction implications and incompatibilities.
  - Examples include extender `SEAMLESS -> FLUSH`, restriction tiers, and skydoor incompatibilities.
  - Store them in `tag_relations` (`implies` / `incompatible`), which exists but is not yet read or written.
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
  - Then settle the door and extender timing representations (the deferred last phase of the
    schema audit): keep the `*_timing_variants` tables and write them, or drop them.
- [x] Implement the fixed-size Door Rules title formatter.
  - Includes trapdoor rewrites, animated restrictions, named layouts, canonical aliases, and conflict diagnostics.
- [ ] Add algebraic and minimal expandable-size title forms after expandable-volume records are modeled.
- [x] Add an animated restriction tag kind.
- [ ] Decide which general eligibility rules remain moderation responsibilities and which need structured evidence.

## Search

- [x] Connect a semantic candidate provider to the search backend, and process `search_embedding_queue` to embed
  projected documents.
  - See `docs/plans/completed/search-index-redesign.md`.
- [ ] Consider dynamic `tag:<name>` search fields so new projected filters do not require a field-registry code change.
- [ ] Add live PostgreSQL integration coverage for exact, full-text, close-spelling, and RRF behavior.
  - Pagination and sorting by indexed field values are covered by `tests/integration/search/test_repository_pagination.py`.
- [x] Add autocomplete for field names, tag values, and canonical record base keys.
- [ ] Replace raw showcase qualifiers with parameterized tags.
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
  - `squid/core/diagnostics/` stores every captured failure in `error_reports` with the traceback, the redacted
    context, and the log lines the process emitted under the same correlation ID. `/error <reference>`,
    `GET /v1/diagnostics/errors/{reference}`, and `squid errors show` all resolve it, gated on
    `diagnostics.error.read`.
  - The two paths no longer track separate reference schemes: `correlation_reference()` shortens to 12
    characters, which is already the width of the untraced fallback, and lookup accepts either width.
- [ ] Integrate Sentry (or wire up a real log/trace backend) instead of the current no-op `debug` exporter in
  `deploy/otel-collector.yaml`.
- [x] Let the worker and API processes use the human-readable log formatter in dev mode like the bot does.
  `development_mode` moved from `BotProcessConfig` to the shared `_ProcessSettings` base, and
  `configure_service_worker_logging`/`configure_api_logging` now take a `dev_mode` flag threaded from
  `resolved_config.development_mode`, matching `configure_bot_logging`.

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

- [ ] Give creator profiles a first-party surface outside the REST API. `GET /v1/accounts/{account_id}`
      is already unauthenticated, but nothing in `squid/bot/` ever calls
      `get_creator_profile`, so a public creator identity is unreachable from Discord.
  - Treating "who holds this creator credit" as staff-only information was a mistake: the profile is public data,
    and `accounts.public_account_id` exists to be shown.
  - Blocks naming the creator who holds a contested alias in the consent-verification UX: it only helps if the
    reader has somewhere to look the name up.

## Submissions

- [ ] Let a draft owner share a submission draft with editors.
  - `submission_draft_access` already models this (`owner` / `editor` roles; account merge keeps it consistent),
    but only the owner row is written, at creation, and nothing reads it.
  - Make draft authorization read the table instead of `submission_drafts.owner_account_id` alone.
  - Add inviting and removing editors in the bot and the API, and decide which actions stay owner-only,
    such as finalizing, discarding, and managing editors.

## Website

- [ ] Serve the catalogue's content-hashed assets (`/_astro/*`) with
  `Cache-Control: public, max-age=31536000, immutable`, so repeat visits skip revalidating them.
  - Decide whether the shared Caddy gateway or the Astro Node server sets it; check what Caddy sends today.
  - Leave HTML, `sw.js`, the manifest, and unhashed files in `public/` on short or revalidated caching.
- [ ] Check whether the shared Caddy gateway already sends `Strict-Transport-Security`, so it is not set twice
  with different values; the web server sends `max-age=31536000` since `3a42b8cc8`.

## Migration and cleanup

- [ ] Remove legacy smallest-door tables, columns, functions, and triggers after the new projections have run in production.
- [ ] Remove compatibility reads from legacy description and timing fields after backfill verification.
- [ ] Snapshot managed function and trigger SQL inside historical migrations so later registry edits cannot alter migration replay.
- [ ] Migrate primary keys from sequential integers to UUIDv7 and add human-readable slugs. This is an unowned,
      repository-wide backlog item, not part of an active implementation plan; assign an owner and migration plan
      before scheduling it. Once external users hold IDs and URLs, the work stops being only a schema change and
      becomes a data-compatibility project.
  - UUIDv7 keeps index locality while removing the enumerable, guessable IDs currently exposed through the REST API.
  - Slugs give stable, shareable URLs that survive re-keying; `permission_roles.slug` is the pattern to follow.
