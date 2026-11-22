# API changes the UI needs

The UI is designed for what users need; these are the API changes that design requires. Screen IDs refer to [screens.md](screens.md). Build a screen only after its items land, rather than bending the screen to the current API.

Grouped by area. Each entry names the screen that needs it and why.

## Identity, session and permissions

1. **`GET /v1/session`.** It is cheap and called on every server render that has a session cookie, and in the header island.
   - *Response:* `{signed_in: bool, user: {id, creator_id, display_name, avatar_url} | null, consent_pending: bool, permissions: string[] (effective global node names), counts: {unread_notifications: int, drafts: int, drafts_needing_attention: int, review_queue: int | null}}`.
   - *Why:* the header badge, menu, staff links, consent redirect, and every permission-gated button. Today `UserMe` has no permissions and no counts.
2. **`viewer` block on detail responses.** Avoids N extra calls for per-user state.
   - `BuildDetail.viewer`: `{can_edit, can_review, can_download_private_schematics, followed_creator_subscription_ids: {creator_id: subscription_id}, vote: VoteSessionSummary | null}`.
   - `PublicRecordDetail.viewer`: `{subscription_id: int | null}`.
   - `CreatorProfileDetail.viewer`: `{subscription_id, is_self, can_claim}`.
   - `TagDetail.viewer`: `{filter_subscription_id}`.
   - Null for anonymous users.
3. **`GET /v1/auth/providers`** returns `[{id, label_key}]`. Needed by the A1 sign-in buttons, and to link additional providers on A3.
4. **Callback errors.** `GET /v1/auth/{provider}/callback` should redirect to `redirect_to` with `?auth_error=<code>` on failure, so A1 can show an inline error.

## Builds

5. **New fields on `BuildDetail` and `PublicBuildSummary`:**
   - `subtitle: string | null` (the door-rules subtitle: component and miscellaneous restrictions).
   - `status` as an enum `BuildStatus`, and `category` as the `BuildCategory` enum instead of free strings.
   - `creators: [{name, creator_id: uuid | null}]` and `credits[].creator_id`, so P5 and cards link to creators.
   - `preview: {kind, url, width, height, srcset?: [{url, width}]}`. Needed by `Image` to avoid layout shift.
   - `record_summary: {held_count: int, former_count: int}` on summaries, for the "Record holder" badge on cards and tables.
6. **More on `BuildDetail`:**
   - `records_held: [{record_id, title, criterion, version_scope, status, since: datetime}]` and `records_former: [...]` (P5).
   - `media: [{kind: image | video | render, url, thumbnail_url, width, height, alt: string | null}]`, replacing untyped `links.images` and `links.videos` (P5 gallery).
   - `schematics: {count, primary: SchematicSummary | null}`. This removes the second request on P5.
   - `approved_at`, `submitted_at`, `submitted_by: {display_name, creator_id} | null`, `last_edited: {at, by} | null`, `history_count`.
   - `vote_session_id: int | null` for pending builds (M2, A5).
7. **`GET /v1/builds/{id}/history?offset&page_size`** returns `Page[{revision, at, actor: {display_name, creator_id} | null, kind, changes: [{field, label, before, after}]}]`. The data exists in `build_edit_history`. Needed by P6, and labels are needed for `Diff`.
8. **`GET /v1/builds/{id}` for owners and staff.** It currently returns 404 for pending and rejected builds even to the owner. Return them when the caller is the submitter or has `build.submission.view_pending`, adding `review: {status, reason, vote_session_id}`. Needed by P5 (owner banner), A5, M2.
9. **Filters and sorts on `GET /v1/builds`:**
   - `category`, `version` (id), `tag` (repeatable id or slug), `creator_id`, `submitted_by=me`, `status` combined with `q` for staff.
   - `sort` documented as an enum `newest | oldest | updated | fastest_open | smallest_volume | title`.
   - Needed by P4, P12, P13, A5, M1.
10. **Edit through drafts.** `POST /v1/submissions/drafts` gains `source_build_id`, creating a draft prefilled from the build. Finalizing that draft applies an edit with the expected revision, and is rejected on conflict with a diff. M4 and "Suggest a correction" on P5 then reuse the S2 renderer, so there are not two form systems. Until then, at least `GET /v1/builds/{id}/form` (manifest plus current answers) is required for M4.

## Records

11. **Filters on `GET /v1/records`:** `build_kind`, `criterion`, `version_scope`, `status`, `tag`, `q`. `sort` as an enum `recently_changed | title | criterion`. Needed by P7.
12. **New fields on `PublishedRecordResult`:**
    - `holders: [{build_id, title, creators: [{name, creator_id}], preview}]`, summaries rather than bare ids.
    - `metrics: {name: {value, unit, display}}`, the same shape as search.
    - `category_key` (base key), `holder_since: datetime`.
    - Needed by P7 table rows, P1 and RecordCard without N+1 requests.
13. **`GET /v1/records/{id}/history`** returns `[{holders: [build summary], from: datetime, to: datetime | null, metrics}]`. `records/application/history.py` already reconstructs this. Needed by P8 Timeline and chart 2.
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
23. **Tag proposals:** `GET /v1/tags/proposals?status`, `POST /v1/tags/proposals` (users propose showcase tags), `POST /v1/tags/proposals/{id}/{approve|reject|archive}`, `POST /v1/tags/{id}/aliases`. Needed by X3.

## Creators and accounts

24. **`GET /v1/creators/by-name/{name}`** returns `{alias: {name, claimed}, profile: CreatorProfileDetail | null, stats: {build_count, records_held, records_ever, builds_by_year: [{year, count}]}, records_held: [record summary]}`. This removes the alias-then-profile waterfall on P13.
25. **Creator builds.** `GET /v1/builds?creator_id=` or `?alias=` for unclaimed names (item 9). Needed by P13.
26. **Alias claims:**
    - `POST /v1/creator-aliases/{name}/claims` and `GET /v1/users/me/claims` (P13, A3).
    - `GET /v1/claims?status`, `POST /v1/claims/{id}/{approve|reject}` (X4).
27. **Account lookup.** `GET /v1/accounts?q=` returns `{id, display_name, identities}` for staff. Needed by X5 and X6.
28. **Product decision: account deletion and data export.** `DELETE /v1/users/me` and `GET /v1/users/me/export`. Needed for A2 if the privacy notice promises them.

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
37. **Correction proposals list.** `GET /v1/submissions/revisions?status=pending&build_id=` (today only lookup by source message). `before` and `after` become `changes: [{field, label, before, after}]`. Needed by M1 and M3.
38. **Draft sharing.** `submission_draft_access` already exists. Add `GET/POST/DELETE /v1/submissions/drafts/{id}/editors`, needed if S2 adds collaborators (todo.md). This is a product decision, so leave it as an optional phase.

## Voting and review

39. **Review queue endpoint.** `GET /v1/review/builds?sort=deadline|submitted&offset` returns pending builds with `{build summary, submitted_by, vote: {id, status, tallies, pass_threshold, fail_threshold, deadline, own_selection}}`, plus `GET /v1/review/counts`. Needed by M1.
40. **Web voting without a guild.** `POST /v1/vote-sessions/{id}/votes` requires `guild_id`, which a website voter doesn't have. Make it optional: the server resolves the caller's weight (for example, highest across guilds, or a network weight), and the session's `viewer` exposes `eligible: bool, weight`. This is a product decision.
41. **`VoteSessionDetail` additions:** `build: summary`, `deadline`, and `voters: [{display_name, choice}]` when visibility is `visible_live`.

## Schematics and media

42. **`SchematicSummary` additions:** `filename`, `uploaded_at`, `render: {url, width, height} | null`, `block_counts_top: [{block, count}]` (chart 3). Add `GET /v1/builds/{id}/schematics/{sid}/videos` to list existing videos.

## Admin

43. **Runtime settings schema.** `GET /v1/settings/runtime/schema` returns `[{key, group, label, description, type, minimum, maximum, discord_kind: channel | role | user | null}]`, plus `GET /v1/discord/guilds/{id}/channels|roles` for names. Needed by X1: snowflake inputs are unusable.
44. **Diagnostics:**
    - List filters: `surface`, `code`, `from`, `to`, `offset`.
    - `GET /v1/diagnostics/errors/stats?bucket=day` for chart 7.
    - `DELETE /v1/diagnostics/errors/{reference}`.
    - Needed by X2.
45. **Permissions:** `GET /v1/permissions/nodes`, `GET /v1/permissions/subjects/{id}` (effective permissions with source), `GET /v1/permissions/audit`, and CRUD for `/v1/permissions/grants` and `/v1/roles`. Needed by X6.
46. **Record operations:** `GET /v1/records/{id}/inspection`, `POST /v1/records/{id}/rebuild`, and `GET /v1/records/computations?status=failed` (todo: persist failed attempts). Needed by X7.
47. **`POST /v1/versions`.** Needed by X8.

## Home and sponsors

48. **Home data.** P1 is built from parallel calls: `records?sort=recently_changed&page_size=6`, `builds?sort=newest&page_size=6`, and `GET /v1/stats` returning `{builds, records, creators, records_by_month?}`. No home-specific aggregate is needed once items 9, 11 and 12 exist.
49. **`GET /v1/sponsors`** returns public sponsor servers that opted in (P16).

## Cross-cutting

50. **ProblemDetail field errors.** Every validation ProblemDetail should carry `context.fields: [{field, code, params}]` so `ErrorSummary` can map errors to fields. Codes must be stable enums so the client can use a `satisfies` map.
51. **ETags.** Expose `ETag` through CORS, or a body `etag`, on every editable resource. Include `revision` everywhere, as builds already do.
52. **Cookie and origin strategy.** Decide whether islands call the API directly (cross-site cookies, CORS, CSRF header) or through a same-origin `/api` proxy on the Astro server. Recommendation: the proxy, which keeps the session cookie first-party, uses one CSRF helper, and needs no CORS for credentials.

---
