# API changes the UI needs

The UI is designed for what users need; these are the API changes that design requires. Screen IDs refer to [screens.md](screens.md). Build a screen only after its items land, rather than bending the screen to the current API.

Grouped by area. Each entry names the screen that needs it and why.

## Identity, session and permissions

Items 1–4 are implemented in the API.

1. **`GET /v1/session`** supplies one snapshot for the server render and header island. The island reuses the snapshot on hydration.
   - Return `{user: {id, creator_id, display_name, avatar_url} | null, consent_pending: bool, permissions: string[], counts: {unread_notifications: int, drafts: int, drafts_needing_attention: int, review_queue: int | null}}`. Permissions are effective global node names.
   - A missing or expired cookie returns 200 with a null user, false consent flag, empty permissions, zero personal counts and a null review queue. The queue count is null whenever the viewer lacks access and zero when a visible queue is empty.
   - A local PostgreSQL benchmark of the signed-in staff endpoint measured 6.2 ms median and 7.6 ms p95 over 25 warm requests, with 10,000 builds (1,000 pending) and 10,000 drafts (10 owned).
2. **Detail responses include a nullable `viewer`** so one request supplies the controls for the resource. Anonymous callers get null. Keep personalized responses out of shared caches and enforce each permission on the action itself.
   - `BuildDetail.viewer` includes `can_edit`, `can_review`, `can_download_private_schematics`, a map of followed creator IDs to subscription IDs, and the caller’s nullable `ballot`. The vote session belongs to the build (item 6).
   - `PublicRecordDetail.viewer` includes a nullable `subscription_id`. `CreatorProfileDetail.viewer` includes a nullable `subscription_id`, `is_self` and `can_claim`. `TagDetail.viewer` includes a nullable `filter_subscription_id`.
3. **`GET /v1/auth/providers`** returns configured providers as `[{id}]` in display order. A1 uses the stable provider slug for its localized sign-in button. A3 combines discovery with `/me` identities and calls `POST /v1/auth/{provider}/link` with the browser CSRF token. Navigate to the returned `authorization_url`. The callback requires the account that started linking and reports `identity_in_use` if a different account owns the identity.
4. **Callback failures return to A1** with a stable `auth_error` query parameter. Use `cancelled` for provider denial, `invalid_state` for missing, expired or mismatched state, and `provider_unavailable` for provider failures. Consume and validate state even on denial. Only a validated attempt can carry its saved return destination into a retry. Successful sign-in retains the existing return redirect.

56. **Consent before the account.** The OAuth callback creates the account and its identity before the visitor sees the privacy notice, yet the notice says "Cancelling stores nothing". A1's decline signs the visitor out, but the account row stays. Either create the account on acceptance (hold the verified identity in the session until then) or reword the notice. The callback's error redirect also goes to the unprefixed `/sign-in`, so a zh-CN visitor sees English errors; carry the locale in the saved state.
57. **List Minecraft grants.** A4 lists grants with origin, server and expiry, but `/v1/minecraft` has only `DELETE /grants/{id}`. Add `GET /v1/users/me/minecraft/grants`.

## Builds

5. **Build summaries (implemented).** Cards receive the subtitle, typed category and status, creator names with nullable public IDs, and current and former record counts. External preview dimensions can be unknown.
6. **Build detail (implemented).** P5 receives current and former records, a typed gallery, public schematic count and primary download, submission and approval metadata, and the public history count. Missing historical approval dates are null. An unpublished primary schematic contributes neither metadata nor a download link.
7. **Public history (implemented).** `GET /v1/builds/{id}/history` pages published revisions newest first. Changes cover public dimensions, names, description, version specification, timings, orientation, and creator names. The first published revision has no public predecessor.
8. **Owner and staff access to unpublished detail (deferred).** P5, A5, and M2 need a visibility decision in the service layer. The detail route still serves approved builds.
9. **Catalogue filters and sorts (implemented).** `GET /v1/builds` accepts category, version ID, repeated tag IDs or slugs (all must match), creator ID, and `submitted_by=me`. Search accepts the same filters and moderation status. Sorts are `newest`, `oldest`, `updated`, `fastest_open`, `smallest_volume`, and `title`, and every sort accepts ID anchors (item 55).
10. **Edit through drafts (implemented).** A web draft with `source_build_id` starts with the approved build's public editable answers and pins its build revision. Finalization applies authorized changes or returns a review vote ID. A stale revision returns 412 with field differences and preserves the draft. Moderators can read the full edit form at `GET /v1/builds/{id}/form`.

## Records

11. **Filters on `GET /v1/records`:** `build_kind`, `criterion`, `version_scope`, `status`, `tag`, `q`. `sort` as an enum `recently_changed | title | criterion`, replacing the raw `-result_id` order, with ID anchors on every sort. Needed by P7.
12. **New fields on `PublishedRecordResult`:**
    - `holders: [{build_id, title, creators: [{name, creator_id}], preview}]`, summaries rather than bare ids.
    - `metrics: {name: {value, unit, display}}`, the same shape as search.
    - `category_key` (base key), `holder_since: datetime`.
    - Needed by P7 table rows, P1 and RecordCard without N+1 requests.
13. **`GET /v1/records/{id}/history`** pages holder periods newest first, each `{holders: [build summary], from: datetime, to: datetime | null, metrics}`. `records/application/history.py` already reconstructs this. Needed by P8 Timeline and chart 2.
14. **`PublicRecordDetail.siblings`:** `[{record_id, criterion, version_scope, status}]` for the same category. Needed by the P8 switcher.
15. **Range discovery and labels:**
    - `GET /v1/records/ranges/door?orientation&height&depth` lists registered range queries with labels.
    - `DoorRangeQuery` responses gain `labels: {types: [{id, name, slug}], restrictions: [...], version: string | null}`.
    - Without this, P9 has to look tags up by id.
    - Range publication results gain `segments` ready for the chart: `[{from, to, period, residue, value, holder_builds: [summary]}]`.
16. **`GET /v1/records/door/options`** returns `{orientations, types: [tag], restrictions: [tag], versions}` for P9 and P10, or document that the form option sources serve this.
17. **`records_lookup_door` should return holder build summaries and a `comparison` block** (`beats_current: bool`, `delta` per metric). P10's result needs it.

## Search

18. **New fields on `SearchField`:** `label` (localized), `description`, `group`, `unit` and `display_unit`, `scopes: SearchScope[]`, `value_choices: [{value, label}] | null`, `examples: string[]`, `metric: bool` (whether it is a record metric column). Needed by P3 reference, FieldFilters, and the column headers in the P2 records table.
19. **Additions to the search response:**
    - `counts: {all, builds, records, metadata}` for the scope tab counts (P2).
    - `parsed: {text: string[], clauses: [{field, operator, value, span: [start, end]}], errors: [{span, code, message}]}`. Needed for chips, removable filters and inline query errors on P2.
    - `metric_columns: string[]` listing the metrics present on this page (P2 records table).
    - `sort_options: [{value, label}]` per scope.
    - Phase 2: `distributions` (`?distributions=width,opening_time,category`) returning `{field: {buckets: [{value, count}] | histogram: [{from, to, count}]}}`, for chart 6 and filter counts.
20. **Typed sort.** Document `sort` per scope (field name, `-` prefix for descending) and validate it, returning a ProblemDetail code the UI can map.

## Tags

21. **`GET /v1/tags` filters:** `kind`, `authority`, `q`. `TagDetail` gains `description`, `aliases`, `build_count`, `record_count`, and `rules_anchor` (a link into `/rules`). Needed by P11 and P12.
22. **Tag page builds.** `GET /v1/builds?tag=slug` (item 9) avoids the waterfall of tag, then query name, then search on P12.
23. **Tag proposals:** `GET /v1/tags/proposals?status` (a queue, oldest first), `POST /v1/tags/proposals` (users propose showcase tags), `POST /v1/tags/proposals/{id}/{approve|reject|archive}`, `POST /v1/tags/{id}/aliases`. Needed by X3.

## Creators and accounts

24. **`GET /v1/creators/by-name/{name}`** returns `{alias: {name, claimed}, profile: CreatorProfileDetail | null, stats: {build_count, records_held, records_ever, builds_by_year: [{year, count}]}, records_held: [record summary]}`. This removes the alias-then-profile waterfall on P13.
25. **Creator builds.** `GET /v1/builds?creator_id=` or `?alias=` for unclaimed names (item 9). Needed by P13.
26. **Alias claims:**
    - `POST /v1/creator-aliases/{name}/claims` and `GET /v1/users/me/claims` (P13, A3).
    - `GET /v1/claims?status` (a queue, oldest first), `POST /v1/claims/{id}/{approve|reject}` (X4).
27. **Account lookup.** `GET /v1/accounts?q=&limit=` returns `{id, display_name, identities}` for staff. It serves pickers, so it's a completion with `limit`, not a paged list. Needed by X5 and X6.
28. **Product decision: account deletion and data export.** `DELETE /v1/users/me` and `GET /v1/users/me/export`. Needed for A2 if the privacy notice promises them.
53. **Merge code normalization.** `POST /v1/users/me/merge` and `/merge/preview` compare the code exactly, while CLI and Minecraft codes go through `normalize_user_code`. Normalize merge codes the same way (case, spaces, hyphens), since `CodeEntry` submits the grouped text. Needed by A3's merge flow.

## Notifications

29. **Typed payloads.** `InboxNotificationDetail.payload` becomes a discriminated union per `NotificationKind`, with typed fields such as `{build: {id, title}, records: [{id, title}]}` and `href`. Without this, A6 can't render items safely.
30. **Inbox list improvements.** The page response gains `unread_count`; add the query param `unread_only=true` and `POST /v1/users/me/notifications/inbox/read-all`. Needed by A6 and the header bell.
31. **Readable subscriptions.** `NotificationSubscriptionDetail` gains `subject_label`, `subject_href`, and `filter_summary` (a localized string), so A7 can show readable items. Add `GET …/subscriptions?kind&subject_id` for toggle state on pages that don't carry a `viewer` block.

## Submissions

32. **`DraftSummaryResponse` additions:** `completeness: {required: int, filled: int}`, `issue_count`, `preview_url`, `build_id` (after submission), and `source_build_id`. Needed by the S1 list and A5.
33. **Live validation on every change.** `DraftChangeResponse.issues: [{field_id, reason, params: {minimum?, maximum?, min_length?, ...}}]`, computed after each change. `SubmissionAttentionIssueResponse` also gains `params`. S2 needs this to show "must be at least 3" at the field as the user types, instead of only after finalizing.
34. **Manifest presentation hints:** `FormFieldResponse` gains `placeholder`, `unit`, `width: full | half | narrow`, and `description` on sections, plus confirmation that labels and help text are localized to the request locale.
35. **Media preview URLs.** `DraftMediaArtifactResponse` gains `url` (signed and short-lived), `width` and `height`. Today S2 cannot show what was uploaded.
36. **Finalization long-poll or events.** `GET /v1/submissions/drafts/{id}/status?wait=25` (long-poll) or an SSE stream. Removes polling loops on S3.
37. **Correction proposals list.** `GET /v1/submissions/revisions?status=pending&build_id=` (today only lookup by source message), a queue, oldest first. `before` and `after` become `changes: [{field, label, before, after}]`. Needed by M1 and M3.
38. **Draft sharing.** `submission_draft_access` already exists. Add `GET/POST/DELETE /v1/submissions/drafts/{id}/editors`, needed if S2 adds collaborators (todo.md). This is a product decision, so leave it as an optional phase.

## Voting and review

39. **Review queue endpoint.** `GET /v1/review/builds?sort=deadline|submitted` is a queue that returns pending builds with `{build summary, submitted_by, vote: {id, status, tallies, pass_threshold, fail_threshold, deadline, own_selection}}`, plus `GET /v1/review/counts`. Needed by M1.
40. **Web voting without a guild.** `POST /v1/vote-sessions/{id}/votes` requires `guild_id`, which a website voter doesn't have. Make it optional: the server resolves the caller's weight (for example, highest across guilds, or a network weight), and the session's `viewer` exposes `eligible: bool, weight`. This is a product decision.
41. **`VoteSessionDetail` additions:** `build: summary`, `deadline`, and `voters: [{display_name, choice}]` when visibility is `visible_live`.

## Schematics and media

42. **`SchematicSummary` additions:** `filename`, `uploaded_at`, `render: {url, width, height} | null`, `block_counts_top: [{block, count}]` (chart 3). Add `GET /v1/builds/{id}/schematics/{sid}/videos` to list existing videos.

## Admin

43. **Runtime settings metadata and Discord names.** Needed by X1, where raw snowflake inputs are unusable.
    - Declare `group` and `discord_kind: channel | role | user | null` on the `RuntimeSettings` fields, so they reach OpenAPI and the SDK alongside the existing bounds. No separate schema endpoint. The web localizes labels and descriptions by field name.
    - `GET /v1/settings/runtime` resolves each snowflake to `{id, name} | null`, with null when Discord no longer knows it.
    - `PATCH` rejects an ID of the wrong kind, such as a role ID in a channel field, with a field error (item 50).
    - Pickers need the channels and roles of the guild these IDs belong to. Nothing records that guild yet. Decide whether to add a `home_guild_id` setting or derive it from the configured channels, then add `GET /v1/discord/home/channels` and `/roles`.
44. **Diagnostics.** Needed by X2.
    - The list ignores everything but `page_size` and `work_lost`. Add `surface`, `error_code` and an `occurred_at` range, and page it with item 55 anchors on `(occurred_at, id)`.
    - `GET /v1/diagnostics/errors/stats` returns `[{day, surface, count}]` for chart 7. Reports expire after 7 days, so the chart covers that window.
    - No per-report delete. Reports expire anyway, and deleting one just destroys evidence.
45. **Permissions.** `PermissionAdministrationService` already implements all of this, so the work is mostly routing. Every route takes an optional `guild_id` scope, and subjects are accounts or Discord roles. Needed by X6.
    - `GET /v1/permissions/nodes` lists the catalogue by domain.
    - Grants are granted and revoked, never edited: `GET`, `POST` and `DELETE /v1/permissions/grants`.
    - Roles: `GET`, `POST` and `DELETE /v1/permissions/roles`, plus `PATCH` for rank and endpoints to add or remove patterns.
    - Role assignments: `GET`, `POST` and `DELETE /v1/permissions/assignments`.
    - `GET /v1/permissions/explain?subject&node` returns the resolver's `Decision` (reason, trace, and each rule's source and the role it came through) for one node, or for every node when `node` is omitted.
    - `GET /v1/permissions/audit` pages with item 55 anchors. It only takes `limit` today.
46. **Record operations.** Per-record recompute and inspection, the full recompute backstop, the recompute queue and drift reports. These depend on incremental computation; see [Admin operations](../incremental-records.md#admin-operations). Needed by X7.
47. **Version corrections.** `POST /v1/versions` wraps `VersionService.add`, including its edition override. The version announcement worker adds most versions, so X8 exists to fix mistakes and also needs `DELETE /v1/versions/{id}`, which core lacks. Needed by X8.

## Home and sponsors

48. **Home data.** P1 is built from parallel calls: `records?sort=recently_changed&page_size=6`, `builds?sort=newest&page_size=6`, and `GET /v1/stats` returning `{builds, records, creators, records_by_month?}`. No home-specific aggregate is needed once items 9, 11 and 12 exist.
49. **`GET /v1/sponsors`** returns public sponsor servers that opted in (P16).

## Cross-cutting

50. **ProblemDetail field errors (implemented).** Validation failures include `context.fields: [{field, code, params}]`. Core reports domain field IDs, including fields on saved drafts. Request decoding reports JSON Pointers rooted at the request location. Request-wide failures have an empty list. Use the SDK’s `ValidationIssue` union and `FieldErrorCode` for exhaustive formatters. Multiple issues can target one control.
51. **ETags.** `ETag` is already exposed through CORS, and accounts, drafts, builds, settings and notification preferences send it. Include `revision` in every editable body, as builds already do, and send `ETag` from the remaining editors.
52. **Cookie and origin strategy.** Decided: islands call the API on the site's own origin, where the gateway already routes `/v1`, and take the CSRF token from `GET /v1/auth/csrf`. No Astro proxy. See "Same-origin API" in [README.md](README.md). No API change needed.
54. **A stable revision-conflict code.** A stale `If-Match` or `base_revision` answers 412 with code `DOMAIN_ERROR` and no public context (`RevisionConflictError` keeps `expected` and `current` private); a draft whose row moved under a change answers 409, also `DOMAIN_ERROR`, with `actual_revision`. Give both one code, such as `REVISION_CONFLICT`, with `{expected, current}` in public context, so clients stop recognizing conflicts by status. Needed by `draft-sync.ts`, the conflict `Notice` and M4.

---
55. **One pagination system (anchors on every build sort implemented).** Every list returns `{items, total, next, prev}`. `next` and `prev` are the query parameters for the neighbouring page, so clients merge them into the next request and never build cursors themselves. There are three ways to address a page, and a request uses at most one:
    - **Numbered pages** use `offset` up to 10,000. The website turns `?page=N` into `offset=(N-1)*page_size`. A row inserted or deleted mid-browse can repeat or skip one item, which is acceptable for browsing.
    - **Anchored pages** use `after_id` or `before_id` with any sort that has a fixed order. The server seeks on the anchor's `(sort value, id)`. With a sort other than `id`, an anchor that is missing or outside the filtered list returns 400 `PAGE_ANCHOR_NOT_FOUND`, and the client starts again from the first page. Ranked search has no seekable order and stays offset-only.
    - **Complete traversal** uses `sort=id` with `after_id`. IDs never change and the anchor needs no lookup, so each surviving item is seen once even if the anchor was deleted. This is the transparent cursor.
    - Lists take `page_size` (1–50) and named sorts (`newest`, `fastest_open`). Items in this document name only their filters and sorts; paging is always this system.
    - Numbered pages are enough for browsing. Queues, inboxes and logs, where rows arrive or leave while someone works through them, also need anchors so a reader keeps their place: the review queue, tag proposals, alias claims, correction proposals, the notification inbox, diagnostics and the permission audit log. Completions are not lists: `suggest` keeps `limit`, and its `cursor` is the caret position.
    - Anchors carry the list's own ID type: integers, or UUIDs for the correction inbox and diagnostics. `records` still takes the raw `-result_id` sort until item 11.
    - **Planned: `changed_since=<sequence>`** for sync clients. Each resource kind keeps a change sequence; a client lists everything once, then asks for rows changed after the sequence it last saw, tombstones included. Keyset paging on a sort value that can change can still miss a row that moves across the page boundary, and only this feed guarantees that nothing is lost.
    - ETags on lists serve `If-None-Match` caching only. Detecting a changed collection between pages is `changed_since`'s job.
