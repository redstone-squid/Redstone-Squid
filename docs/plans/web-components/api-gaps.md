# API changes the UI needs

The UI is designed for what users need; these are the API changes that design requires. Screen IDs refer to [screens.md](screens.md). Build a screen only after its items land, rather than bending the screen to the current API.

Grouped by area. Each entry names the screen that needs it and why.

## Identity, session and permissions

Items 1–4 are implemented in the API.

1. **`GET /v1/session`** supplies one snapshot for the server render and header island. The island reuses the snapshot on hydration.
   - Return `{user: {id, display_name, avatar_url} | null, consent_pending: bool, permissions: string[], counts: {unread_notifications: int, drafts: int, drafts_needing_attention: int, review_queue: int | null}}`. Permissions are effective global node names.
   - A missing or expired cookie returns 200 with a null user, false consent flag, empty permissions, zero personal counts and a null review queue. The queue count is null whenever the viewer lacks access and zero when a visible queue is empty.
   - A local PostgreSQL benchmark of the signed-in staff endpoint measured 6.2 ms median and 7.6 ms p95 over 25 warm requests, with 10,000 builds (1,000 pending) and 10,000 drafts (10 owned).
2. **Detail responses include a nullable `viewer`** so one request supplies the controls for the resource. Anonymous callers get null. Keep personalized responses out of shared caches and enforce each permission on the action itself.
   - `BuildDetail.viewer` includes `can_edit`, `can_review`, `can_download_private_schematics`, a map of followed creator IDs to subscription IDs, and the caller’s nullable `ballot`. The vote session belongs to the build (item 6).
   - `PublicRecordDetail.viewer` includes a nullable `subscription_id`. `CreatorProfileDetail.viewer` includes a nullable `subscription_id`, `is_self` and `can_claim`. `TagDetail.viewer` includes a nullable `filter_subscription_id`.
3. **`GET /v1/auth/providers`** returns configured providers as `[{id}]` in display order. A1 uses the stable provider slug for its localized sign-in button. A3 combines discovery with `/me` identities and calls `POST /v1/auth/{provider}/link` with the browser CSRF token. Navigate to the returned `authorization_url`. The callback requires the account that started linking and reports `identity_in_use` if a different account owns the identity.
4. **Callback failures return to A1** with a stable `auth_error` query parameter. Use `cancelled` for provider denial, `invalid_state` for missing, expired or mismatched state, and `provider_unavailable` for provider failures. Consume and validate state even on denial. Only a validated attempt can carry its saved return destination into a retry. Successful sign-in retains the existing return redirect.

56. **Consent before the account.** The OAuth callback creates the account and its identity before the visitor sees the privacy notice, yet the notice says "Cancelling stores nothing". A1's decline signs the visitor out, but the account row stays. Either create the account on acceptance (hold the verified identity in the session until then) or reword the notice. The callback's error redirect also goes to the unprefixed `/sign-in`, so a zh-CN visitor sees English errors; carry the locale in the saved state.
57. **List Minecraft grants.** A4 lists grants with origin, server and expiry, but `/v1/minecraft` has only `DELETE /grants/{id}`. Add `GET /v1/accounts/me/minecraft/grants`.

## Builds

5. **Build summaries (implemented).** Cards receive the subtitle, typed category and status, creator names with nullable public IDs, and current and former record counts. External preview dimensions can be unknown.
6. **Build detail (implemented).** P5 receives current and former records, a typed gallery, public schematic count and primary download, submission and approval metadata, and the public history count. Missing historical approval dates are null. An unpublished primary schematic contributes neither metadata nor a download link.
7. **Public history (implemented).** `GET /v1/builds/{id}/history` pages published revisions newest first. Changes cover public dimensions, names, description, version specification, timings, orientation, and creator names. The first published revision has no public predecessor.
8. **Owner and staff access to unpublished detail (deferred).** P5, A5, and M2 need a visibility decision in the service layer. The detail route still serves approved builds.
9. **Catalogue filters and sorts (implemented).** `GET /v1/builds` takes `category`, `version_id`, repeated `tag` IDs or slugs, `creator_id`, `status` and `submitted_by=me`, which lists the caller's builds in every status unless `status` narrows it. Sorts are `id`, `newest`, `oldest`, `updated`, `fastest_open`, `smallest_volume` and `title`. Text search is `/v1/search`, where the same filters are query clauses (`tag:`, `creator:`, `version:`).
10. **Edit through drafts (implemented).** A web draft with `source_build_id` starts with the approved build's public editable answers and pins its build revision. Finalization applies authorized changes or returns a review vote ID. A stale revision returns 412 with field differences and preserves the draft. Moderators can read the full edit form at `GET /v1/builds/{id}/form`.

## Records

Items 12, 13, 15 and 17 share one **record holder summary**: `{build_id, title, creators: [{name, account_id | null}], preview}`. It extends search's `RecordSearchHolder` rather than adding another holder type. **Metrics** everywhere use `{name: {value, unit, display}}`; search's flat `metrics` map changes to match, so the server owns units and formatting.

11. **Record list filters and sorts.** `GET /v1/records` takes `build_kind`, `criterion`, `version_scope`, `status` and repeated `tag` IDs or slugs, as a `RecordListFilters`. Sorts are `recently_changed` and `title` beside `id`. Text search stays on `/v1/search`, whose records scope takes the same filters as query clauses. Needed by P7.
12. **Holders on record lists.** The `/v1/records` list response gains `holders` (holder summaries), `metrics`, `category_key` and `holder_since`. Add them to the list's response type, not to the core `PublishedRecordResult`. `holder_since` is the `held_from` of the open history period, not `computed_at`, which changes on every recompute. Needed by P1 and RecordCard without N+1 requests.
13. **Richer detail history.** `PublicRecordDetail.history` already returns stored periods with `build_ids`. Each entry gains holder summaries, because former holders are not always among the detail's builds, and `metrics`. Metrics are the holders' current values, so past measurements are not recoverable and chart 2 must not imply they are. History is short enough not to need paging. Needed by P8 Timeline and chart 2.
14. **`PublicRecordDetail.siblings`:** `[{record_id, title, subtitle, criterion, version_scope, status}]` for the same category. Needed by the P8 switcher.
15. **Range discovery and labels:**
    - `GET /v1/records/ranges/door?orientation&height&depth` lists registered range queries with labels.
    - `RangePublication` and `DoorRangeResult` gain `labels: {types: [{id, name, slug}], restrictions: [...], version: string | null}`. `DoorRangeQuery` is also a request body, so it stays unchanged. Without labels, P9 looks tags up by id.
    - Awarded regions gain holder summaries. The chart turns periodic regions into segments itself; if expansion is non-trivial, core provides it, not the API layer.
16. **Door options come from the form option sources.** `GET /v1/submissions/form/options/{source}` is anonymous: `approved_patterns` for types, `approved_restrictions` and `approved_source_versions`; orientations come from the form manifest. P9 and P10 use these, so no new endpoint is needed.
17. **`records_lookup_door` returns holder summaries and a nullable `comparison`** (`beats_current: bool`, `delta` per metric) against the published record for the same category. `comparison` is null when no record is published for that category. P10's result needs it.

## Search

18. **Field reference (implemented).** Each `/v1/search/fields` entry has a `group` for laying out FieldFilters and P3, the canonical query `unit`, its `scopes`, `choices: [{value, scopes}] | null` for enumerated fields, `examples` (query strings P3 makes clickable) and `metric`, true for the measurements the P2 records table shows as columns. The response is the same in every language. The web labels built-in fields from the `search_field` and `search_field_description` vocabulary groups, and choices from the group the field's `vocabulary` names. Data-tag fields carry their tag's `label` instead. Result metrics carry their own formatted values (see Records), so there is no display unit.
19. **Search response additions (implemented except distributions).**
    - `filters` lists the parsed query's clauses, each `{kind, field, operator, value, negated, text, without}`. `without` is the normalized query with that clause removed, so a chip removes itself by navigating there, even inside an OR group, and the client never edits query text.
    - Syntax errors stay a 400 `INVALID_QUERY` with `context.position` and `context.suggestions`. The parser stops at the first error, so the query box shows one error at a time.
    - `?counts=true` adds `counts: {all, builds, records, metadata}` for the scope tabs, each null when the query cannot run in that scope. Counts include every match before grouping, while a ranked page's `total` stops at the ranked candidates. Clients request counts with the first page only.
    - Records-table columns come from item 18's `metric` fields rather than the page, so they stay put while paging.
    - Phase 2: `?distributions=width,opening_time,category` returns `{field: {kind: "buckets", buckets: [{value, count}]} | {kind: "histogram", bins: [{from, to, count}]}}` for chart 6 and filter counts.
20. **Search sort (implemented).** `sort` is a sortable field of the scope, `-` prefixed for descending (item 58). P2 offers the item 18 fields with `supports_sort`, named by their label and direction. The `search_sorts` suggestion source spells these out in the request language, the same way the bot does.

## Tags

21. **`GET /v1/tags` filters:** `kind`, `authority`, and `q` as a name and alias prefix match for pickers. `TagDetail` gains `description`, `aliases`, `build_count`, `record_count`, and `rules_anchor` (a link into `/rules`). Needed by P11 and P12.
22. **Tag page builds.** `GET /v1/builds?tag_slug=` (items 9 and 59) avoids the waterfall of tag, then query name, then search on P12.
23. **Tag proposals:** `GET /v1/tags/proposals?status&sort=oldest` (a queue), `POST /v1/tags/proposals` (users propose showcase tags), `POST /v1/tags/proposals/{id}/{approve|reject|archive}`, `POST /v1/tags/{id}/aliases`. Needed by X3.

## Creators and accounts

24. **`GET /v1/creator-aliases/lookup?name=`** returns `{alias: {id, name, claimed}, account: PublicAccountDetail | null, stats: {build_count, records_held, records_ever, builds_by_year: [{year, count}]}, records_held: [record summary]}`. This removes the alias-then-account waterfall on P13.
25. **Creator builds.** `GET /v1/builds?account_id=` (item 9) serves claimed creators. Unclaimed names have no account, so add `creator_alias_id`. Needed by P13.
26. **Alias claims:**
    - `POST /v1/creator-aliases/{id}/claims` and `GET /v1/accounts/me/claims` (P13, A3).
    - `GET /v1/claims?status&sort=oldest` (a queue), `POST /v1/claims/{id}/{approve|reject}` (X4).
27. **Account lookup.** `GET /v1/accounts?q=&limit=` returns `{id, display_name, identities}` for staff. It serves pickers, so it's a completion with `limit`, not a paged list. Needed by X5 and X6.
28. **Product decision: account deletion and data export.** `DELETE /v1/accounts/me` and `GET /v1/accounts/me/export`. Needed for A2 if the privacy notice promises them.
53. **Merge code normalization.** `POST /v1/accounts/me/merge` and `/merge/preview` compare the code exactly, while CLI and Minecraft codes go through `normalize_user_code`. Normalize merge codes the same way (case, spaces, hyphens), since `CodeEntry` submits the grouped text. Needed by A3's merge flow.

## Notifications

29. **Typed payloads (implemented).** `InboxNotificationDetail.payload` is a union discriminated by `kind`. Build kinds carry `build: {id, title}`, record kinds carry `records: [{id, title}]`, and the item has an `href`. Builds link to `/builds/{id}` even while pending or rejected, so those links need item 8.
30. **Inbox list improvements (implemented).** The inbox page carries `unread_count`, `unread_only=true` filters it, and `POST /v1/accounts/me/notifications/inbox/read-all` marks everything read as one undoable edit. Its optional `through_id` leaves items that arrived after the page loaded unread.
31. **Readable subscriptions (implemented).** `NotificationSubscriptionDetail` carries `subject_label`, `subject_href`, and a localized `filter_summary`. `GET …/subscriptions?kind&subject_id` answers toggle state for pages without a `viewer` block.
    - *Open:* creator follows link to `/creators/{name}` using the alphabetically first alias, so they break when an alias is renamed or released. Follows store the account ID; `subject_href` should link by it (item 59).

## Submissions

32. **`DraftSummaryResponse` additions:** `source_build_id` (already on `StoredDraftResponse`), `issue_count` from `preparation_issues`, and `completeness: [{section_id, required, filled}]`. The server computes completeness because only it knows which required fields `visible_when` hides. Needed by S1 and S2's section navigation. Submitted drafts leave this list, and A5 reads `builds?submitted_by=me`, so the summary needs no `build_id`.
33. **Missing answers before finalizing.** Changes already reject out-of-bounds values with 422 field errors carrying `params` (item 50), and the manifest's `constraints` let S2 check bounds as the user types. Required fields are only checked at finalization, though. Add the complete-draft issues to `StoredDraftResponse` so S2's submit panel lists them in advance. Product decision: rejecting invalid values means a half-typed value that breaks a bound can't autosave. Saving it would require storing invalid answers and reporting them as issues.
34. **Manifest additions:** `unit` on duration and measurement fields, and `description` on sections. Layout (width, placeholders) stays with the client, which decides how fields are drawn. Labels and help text are already localized to the request locale.
35. **Media previews.** `DraftMediaArtifactResponse` already has `width` and `height`. Add a private route that streams each artifact, such as `GET /v1/submissions/drafts/{id}/media/{upload_id}/artifacts/{role}`. Islands are same-origin with cookie auth (item 52), so no signed URLs. S2 cannot show uploads without it.
36. **Finalization push (phase 2).** S3 polls `/drafts/{id}/status` with `If-None-Match`. Add a long-poll or SSE stream only if polling proves costly.
37. **Correction proposals list.** `/v1/submissions/revisions` looks proposals up only by source message. Add a list filtered by `status` and `build_id`, with the `id` and `oldest` sorts and UUID anchors (items 55 and 58). Keep `before` and `after`: M3 diffs them and takes labels from the manifest. Needed by M1 and M3.
38. **Draft sharing.** `submission_draft_access` already exists. Add `GET/POST/DELETE /v1/submissions/drafts/{id}/editors`, needed if S2 adds collaborators (todo.md). This is a product decision, so leave it as an optional phase.

## Voting and review

39. **Review queue endpoint.** `GET /v1/review/builds?sort=soonest_deadline|oldest` is a queue that returns pending builds with `{build summary, submitted_by, vote: {id, status, tallies, pass_threshold, fail_threshold, deadline, own_selection}}`, plus `GET /v1/review/counts`. Needed by M1.
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
46. **Record operations (implemented).** `POST /v1/records/{id}/recompute`, `GET /v1/records/{id}/inspection`, `POST /v1/records/recomputes`, `GET /v1/records/recompute-queue` and `GET /v1/records/drift`; see [Admin operations](../completed/incremental-records.md#admin-operations). Needed by X7.
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
    - Lists take `page_size` (1–50). Items in this document name only their filters and sorts (item 58); paging is always this system.
    - Numbered pages are enough for browsing. Queues, inboxes and logs, where rows arrive or leave while someone works through them, also need anchors so a reader keeps their place: the review queue, tag proposals, alias claims, correction proposals, the notification inbox, diagnostics and the permission audit log. Completions are not lists: `suggest` keeps `limit`, and its `cursor` is the caret position.
    - Anchors carry the list's own ID type: integers, or UUIDs for the correction inbox and diagnostics. On `records` the anchor is the result ID.
    - **Planned: `changed_since=<sequence>`** for sync clients. Each resource kind keeps a change sequence; a client lists everything once, then asks for rows changed after the sequence it last saw, tombstones included. Keyset paging on a sort value that can change can still miss a row that moves across the page boundary, and only this feed guarantees that nothing is lost.
    - ETags on lists serve `If-None-Match` caching only. Detecting a changed collection between pages is `changed_since`'s job.
58. **Filters and sorts (builds and search implemented; records has only the `id` sort).** Every list follows these rules; items above name only their own filters and sorts.
    - **Sorts are named** (`newest`, `fastest_open`), declared as a `StrEnum` in core beside the list's query, and each maps to one `KeysetOrder`, so every sort takes ID anchors. `id` is on every list: newest IDs first, the order for complete traversal. Direction is part of the name; offer `largest_volume` rather than a `-` prefix.
    - **Search alone sorts by field**, because its sortable fields come from the field catalogue: `field` or `-field`.
    - **Filters** are one frozen `XListFilters` dataclass per list in core, as `BuildListFilters` is. Parameters use the field name for enums and `<thing>_id` for IDs. Repeating a parameter means all must match; OR, when needed, gets its own parameter. `me` is the only sentinel.
    - **`q` means text search** and belongs to `/v1/search`, where filters are query clauses. Repository lists have no `q` until one needs prefix matching for a picker.
    - **Errors.** An unknown value carries a field issue on `/query/<param>`. Enum parameters fail in request decoding with 422; search checks its sort against the catalogue and answers 400 `INVALID_QUERY` with the `allowed` sorts.
    - A personalized list, such as `submitted_by=me` or an unpublished status, answers `Cache-Control: no-store`.
59. **Resource addresses (accounts implemented).** A resource lives at `/{collection}/{id}`, keyed by an ID that never changes. Names are found by lookup, never used as path keys.
    - **Lookups** are `GET /{collection}/lookup?{key}=`, returning one resource or 404 with the same body as the ID route. Query parameters carry any text, whereas creator names in a path would need percent-encoding, and `%2F` is decoded inconsistently by proxies. `/tags/by-slug/{slug}` becomes `/tags/lookup?slug=`, and `/creator-aliases/{name}` becomes `/creator-aliases/lookup?name=`.
    - **Stored links use IDs.** Notification hrefs, follows and payloads never embed a name. The website may keep readable routes such as `/creators/{name}` and `/tags/{slug}` that resolve through a lookup.
    - **Filters** follow item 58: `tag_id` and `tag_slug` replace the `tag` parameter that accepts either, and `creator_id` becomes `account_id`.
    - **Values defined in code** (`/auth/{provider}`, `/suggest/{source}`, `/media/{kind}`) and opaque references (`/diagnostics/errors/{reference}`, `/schematic-renders/{recipe_hash}`) may be path segments.
    - **Accounts** are one collection keyed by the public UUID, which survives merges; the integer ID never appears in the API. `/accounts/me` is the caller's private view and holds its self-only routes (consent, identities, merges, notifications); `/accounts/{id}` is the public profile. Routes that owners and staff share, such as `/accounts/{id}/profile` and `/minecraft/refresh`, take an ID or `me`, and core decides which permission applies.
    - **Creators** are credited names: `/creator-aliases/{id}` by alias ID, with the account summary when claimed. "Creator" stays out of account routes because unclaimed creators have no account; account references in bodies are `account_id`, not `creator_id`.
