# API changes the UI needs

The UI is designed for what users need; these are the API changes that design requires. Screen IDs refer to [screens.md](screens.md). Build a screen only after its items land, rather than bending the screen to the current API.

Unfinished items come first, then finished ones, each grouped by area. Numbers are stable, so items can refer to each other. Each entry names the screen that needs it and why.

## Not finished

### Identity, session and permissions

56. **OAuth error locale.** Carry the sign-in locale through OAuth state so callback errors return to the localized sign-in page.
57. **List Minecraft grants.** A4 lists grants with origin, server and expiry, but `/v1/minecraft` has only `DELETE /grants/{id}`. Add `GET /v1/accounts/me/minecraft/grants`.

### Builds

8. **Owner and staff access to unpublished detail (deferred).** P5, A5, and M2 need a visibility decision in the service layer. The detail route still serves approved builds.

### Search

19. **Search response additions (implemented except distributions).**
    - `filters` lists the parsed query's clauses, each `{kind, field, operator, value, negated, text, without}`. `without` is the normalized query with that clause removed, so a chip removes itself by navigating there, even inside an OR group, and the client never edits query text.
    - Syntax errors stay a 400 `INVALID_QUERY` with `context.position` and `context.suggestions`. The parser stops at the first error, so the query box shows one error at a time.
    - `?counts=true` adds `counts: {all, builds, records, metadata}` for the scope tabs, each null when the query cannot run in that scope. Counts include every match before grouping, while a ranked page's `total` stops at the ranked candidates. Clients request counts with the first page only.
    - Records-table columns come from item 18's `metric` fields rather than the page, so they stay put while paging.
    - Phase 2: `?distributions=width,opening_time,category` returns `{field: {kind: "buckets", buckets: [{value, count}]} | {kind: "histogram", bins: [{from, to, count}]}}` for chart 6 and filter counts.

### Tags

21. **`GET /v1/tags` filters:** `kind`, `authority`, and `q` as a name and alias prefix match for pickers. `TagDetail` gains `description`, `aliases`, `build_count`, `record_count`, and `rules_anchor` (a link into `/rules`). Needed by P11 and P12.
23. **Tag proposals:** `GET /v1/tags/proposals?status&sort=oldest` (a queue), `POST /v1/tags/proposals` (users propose showcase tags), `POST /v1/tags/proposals/{id}/{approve|reject|archive}`, `POST /v1/tags/{id}/aliases`. Needed by X3.

### Creators and accounts

24. **Creator lookup (partial: the route returns only the alias).** `GET /v1/creator-aliases/lookup?name=` should return `{alias: {id, name, claimed}, account: PublicAccountDetail | null, stats: {build_count, records_held, records_ever, builds_by_year: [{year, count}]}, records_held: [record summary]}`. This removes the alias-then-account waterfall on P13.
25. **Creator builds.** `GET /v1/builds?account_id=` (item 9) serves claimed creators. Unclaimed names have no account, so add `creator_alias_id`. Needed by P13.
26. **Alias claims:**
    - `POST /v1/creator-aliases/{id}/claims` and `GET /v1/accounts/me/claims` (P13, A3).
    - `GET /v1/claims?status&sort=oldest` (a queue), `POST /v1/claims/{id}/{approve|reject}` (X4).
27. **Account lookup.** `GET /v1/accounts?q=&limit=` returns `{id, display_name, identities}` for staff. It serves pickers, so it's a completion with `limit`, not a paged list. Needed by X5 and X6.
28. **Product decision: account deletion and data export.** `DELETE /v1/accounts/me` and `GET /v1/accounts/me/export`. Needed for A2 if the privacy notice promises them.
53. **Merge code normalization.** `POST /v1/accounts/me/merge` and `/merge/preview` compare the code exactly, while CLI and Minecraft codes go through `normalize_user_code`. Normalize merge codes the same way (case, spaces, hyphens), since `CodeEntry` submits the grouped text. Needed by A3's merge flow.

### Submissions

36. **Finalization push (phase 2).** S3 polls `/drafts/{id}/status` with `If-None-Match`. Add a long-poll or SSE stream only if polling proves costly.
38. **Draft sharing.** `submission_draft_access` already exists. Add `GET/POST/DELETE /v1/submissions/drafts/{id}/editors`, needed if S2 adds collaborators (todo.md). This is a product decision, so leave it as an optional phase.
60. **Record preview.** `GET /v1/submissions/drafts/{id}/record-preview` turns the draft into a record candidate and runs record publication against current approved builds without publishing. It returns each category the build would enter: the record (`record_id` null for a new one), an `outcome` of `holds`, `shares`, `ties_later`, `loses` or `undecided`, the missing facts and their fields when undecided, current holder summaries, and per-metric deltas. Other pending builds, approvals and rule changes can change the outcome, so the screen calls it a prediction. Phase 1 serves pending builds (`GET /v1/builds/{id}/record-preview`), which already load as candidates, for S3 and M2. Phase 2 needs a draft-to-candidate mapping. Needed by S2's submit panel, S3 and M2.

### Cross-cutting

51. **ETags.** `ETag` is already exposed through CORS, and accounts, drafts, builds, settings and notification preferences send it. Include `revision` in every editable body, as builds already do, and send `ETag` from the remaining editors.
55. **One pagination system (implemented).** Every list returns `{items, total, next, prev}`. `next` and `prev` are the query parameters for the neighbouring page, so clients merge them into the next request and never build addresses themselves. A request uses at most one of:
    - **`offset`**, up to 10,000, for numbered pages. The website turns `?page=N` into `offset=(N-1)*page_size`. A row inserted or deleted mid-browse can repeat or skip one item, which is fine for browsing.
    - **`cursor`**, an opaque token from `next` or `prev`. It holds the boundary row's sort value and ID, so following it still works after that row is deleted, filtered out or edited. A malformed cursor, or one sent with a different `sort`, answers 400 `INVALID_PAGE_CURSOR` and the client starts again from the first page. Following cursors on `sort=id` sees each surviving row once. Ranked search has no seekable order and stays offset-only.
    - Lists take `page_size` (1–50). Items in this document name only their filters and sorts (item 58).
    - Queues, inboxes and logs, where rows arrive or leave while someone works through them, should be read by cursor: the review queue, tag proposals, alias claims, correction proposals, the notification inbox, diagnostics and the permission audit log. Completions are not lists: `suggest` keeps `limit`, and its `cursor` is the caret position.
    - **Planned: `changed_since=<sequence>`** for sync clients, the only feed that guarantees nothing is lost. A cursor on a sort value that can change (`updated`) can still miss a row that moves across the page boundary.
    - ETags on lists serve `If-None-Match` caching only.
58. **Filters and sorts (builds and search implemented; records has only the `id` sort).** Every list follows these rules; items above name only their own filters and sorts.
    - **Sorts are named** (`newest`, `fastest_open`), declared as a `StrEnum` in core beside the list's query, and each maps to one `KeysetOrder`, so every sort takes cursors. `id` is on every list: newest IDs first, the order for complete traversal. Direction is part of the name; offer `largest_volume` rather than a `-` prefix.
    - **Search alone sorts by field**, because its sortable fields come from the field catalogue: `field` or `-field`.
    - **Filters** are one frozen `XListFilters` dataclass per list in core, as `BuildListFilters` is. Parameters use the field name for enums and `<thing>_id` for IDs. Repeating a parameter means all must match; OR, when needed, gets its own parameter. `me` is the only sentinel.
    - **`q` means text search** and belongs to `/v1/search`, where filters are query clauses. Repository lists have no `q` until one needs prefix matching for a picker.
    - **Errors.** An unknown value carries a field issue on `/query/<param>`. Enum parameters fail in request decoding with 422; search checks its sort against the catalogue and answers 400 `INVALID_QUERY` with the `allowed` sorts.
    - A personalized list, such as `submitted_by=me` or an unpublished status, answers `Cache-Control: no-store`.

## Finished

### Identity, session and permissions

1. **`GET /v1/session`** supplies one snapshot for the server render and header island. The island reuses the snapshot on hydration.
   - Return `{user: {id, display_name, avatar_url} | null, permissions: string[], counts: {unread_notifications: int, drafts: int, drafts_needing_attention: int, review_queue: int | null}}`. Permissions are effective global node names.
   - A missing or expired cookie returns 200 with a null user, empty permissions, zero personal counts and a null review queue. The queue count is null whenever the viewer lacks access and zero when a visible queue is empty.
   - A local PostgreSQL benchmark of the signed-in staff endpoint measured 6.2 ms median and 7.6 ms p95 over 25 warm requests, with 10,000 builds (1,000 pending) and 10,000 drafts (10 owned).
2. **Detail responses include a nullable `viewer`** so one request supplies the controls for the resource. Anonymous callers get null. Keep personalized responses out of shared caches and enforce each permission on the action itself.
   - `BuildDetail.viewer` includes `can_edit`, `can_review`, `can_download_private_schematics`, a map of followed creator IDs to subscription IDs, and the caller’s nullable `ballot`. The vote session belongs to the build (item 6).
   - `PublicRecordDetail.viewer` includes a nullable `subscription_id`. `CreatorProfileDetail.viewer` includes a nullable `subscription_id`, `is_self` and `can_claim`. `TagDetail.viewer` includes a nullable `filter_subscription_id`.
3. **`GET /v1/auth/providers`** returns configured providers as `[{id}]` in display order. A1 uses the stable provider slug for its localized sign-in button. A3 combines discovery with `/me` identities and calls `POST /v1/auth/{provider}/link` with the browser CSRF token. Navigate to the returned `authorization_url`. The callback requires the account that started linking and reports `identity_in_use` if a different account owns the identity.
4. **Callback failures return to A1** with a stable `auth_error` query parameter. Use `cancelled` for provider denial, `invalid_state` for missing, expired or mismatched state, and `provider_unavailable` for provider failures. Consume and validate state even on denial. Only a validated attempt can carry its saved return destination into a retry. Successful sign-in retains the existing return redirect.

### Builds

5. **Build summaries.** Cards receive the subtitle, typed category and status, creator names with nullable public IDs, and current and former record counts. External preview dimensions can be unknown.
6. **Build detail.** P5 receives current and former records, a typed gallery, public schematic count and primary download, submission and approval metadata, and the public history count. Missing historical approval dates are null. An unpublished primary schematic contributes neither metadata nor a download link.
7. **Public history.** `GET /v1/builds/{id}/history` pages published revisions newest first. Changes cover public dimensions, names, description, version specification, timings, orientation, and creator names. The first published revision has no public predecessor.
9. **Catalogue filters and sorts.** `GET /v1/builds` takes `category`, `version_id`, repeated `tag_id` and `tag_slug`, `account_id`, `status` and `submitted_by=me`, which lists the caller's builds in every status unless `status` narrows it. Sorts are `id`, `newest`, `oldest`, `updated`, `fastest_open`, `smallest_volume` and `title`. Text search is `/v1/search`, where the same filters are query clauses (`tag:`, `creator:`, `version:`).
10. **Edit through drafts.** A web draft with `source_build_id` starts with the approved build's public editable answers and pins its build revision. Finalization applies authorized changes or returns a review vote ID. A stale revision returns 412 with field differences and preserves the draft. Moderators can read the full edit form at `GET /v1/builds/{id}/form`.

### Records

Records, search and ranges share one **record holder summary**, `{build_id, title, creators: [{name, account_id | null}], preview}`, built from the holder's current public build summary; non-public holders are left out. **Metrics** everywhere are `{name: {value, unit, display}}`, with `display` rendered in the request locale. Record metrics are the first holder's; co-holders match it on the criterion.

11. **Record list filters and sorts.** `GET /v1/records` takes `build_kind`, `criterion`, `version_scope`, `status` (`resolved`, or `unresolved` when the result has gaps) and repeated `tag_id` and `tag_slug`, which match a record's restrictions or the types in its category. The list holds current results only; search finds retired records with `record_state:retired`. Sorts are `id`, `recently_changed` (`computed_at`, which moves only when the published result changes) and `title`.
12. **Holders on record lists.** Each item carries `holders`, `metrics`, `category_key`, `status` and `holder_since`, the start of the open history period, which comes from completion dates. Door evidence is only on the detail.
13. **Richer detail history.** Each `PublicRecordDetail.history` period has holder summaries and `metrics`. Each publish rewrites history from current builds, so metrics are current values and chart 2 must not present them as past measurements.
14. **`PublicRecordDetail.siblings`** lists `{record_id, title, subtitle, criterion, version_scope, retired}` for the other records of the category.
15. **Range discovery and labels.** `GET /v1/records/ranges/door?orientation&height&depth` pages registered queries newest first with `published_at`. Registrations, publications and live results carry `labels: {types: [{id, name, slug}], restrictions: [{id, name, slug, operator, value}], version}`; `operator` and `value` are null for plain restrictions. Publications and live results carry `holders` for every build that holds a region, so the chart looks them up by ID.
16. **Door options come from the form option sources.** `GET /v1/submissions/form/options/{source}` is anonymous: `approved_patterns` for types, `approved_restrictions` and `approved_source_versions`; orientations come from the form manifest. P9 uses these, so no new endpoint is needed.

### Search

18. **Field reference.** Each `/v1/search/fields` entry has a `group` for laying out FieldFilters and P3, the canonical query `unit`, its `scopes`, `choices: [{value, scopes}] | null` for enumerated fields, `examples` (query strings P3 makes clickable) and `metric`, true for the measurements the P2 records table shows as columns. The response is the same in every language. The web labels built-in fields from the `search_field` and `search_field_description` vocabulary groups, and choices from the group the field's `vocabulary` names. Data-tag fields carry their tag's `label` instead. Result metrics carry their own formatted values (see Records), so there is no display unit.
20. **Search sort.** `sort` is a sortable field of the scope, `-` prefixed for descending (item 58). P2 offers the item 18 fields with `supports_sort`, named by their label and direction. The `search_sorts` suggestion source spells these out in the request language, the same way the bot does.

### Tags

22. **Tag page builds.** `GET /v1/builds?tag_slug=` (items 9 and 59) avoids the waterfall of tag, then query name, then search on P12.

### Notifications

29. **Typed payloads.** `InboxNotificationDetail.payload` is a union discriminated by `kind`. Build kinds carry `build: {id, title}`, record kinds carry `records: [{id, title}]`, and the item has an `href`. Builds link to `/builds/{id}` even while pending or rejected, so those links need item 8.
30. **Inbox list improvements.** The inbox page carries `unread_count`, `unread_only=true` filters it, and `POST /v1/accounts/me/notifications/inbox/read-all` marks everything read as one undoable edit. Its optional `through_id` leaves items that arrived after the page loaded unread.
31. **Readable subscriptions.** `NotificationSubscriptionDetail` carries `subject_label`, `subject_href`, and a localized `filter_summary`. Creator follows link to `/accounts/{id}`, which survives alias renames. `GET …/subscriptions?kind&subject_id` answers toggle state for pages without a `viewer` block.

### Submissions

32. **Draft progress.** Drafts and draft summaries carry `progress: {sections: [{section_id, required, filled}], missing: [field_id]}`. The server counts the visible required answers the way finalization checks them. Progress is null if the server no longer serves the draft's form revision. Summaries also carry `source_build_id` and `issue_count`. Submitted drafts leave the list, and A5 reads `builds?submitted_by=me`, so summaries have no `build_id`.
33. **Live validation.** Changes reject out-of-bounds values with 422 field errors carrying `params` (item 50). The manifest's `constraints` let S2 check bounds as the user types, and `progress.missing` lists unanswered required fields before finalizing. Drafts never store invalid answers: S2 keeps an out-of-bounds value in the form, shows the field error, and skips autosave until the value is valid.
34. **Field units.** Dimension fields have `unit: blocks`, and duration fields have `unit: game_ticks`. Layout (width, placeholders) stays with the client. Labels and help text are localized to the request locale. Add section descriptions once there's copy for them.
35. **Media previews.** Each artifact in a draft media response has a root-relative `url` for `GET /v1/submissions/drafts/{id}/media/{upload_id}/artifacts/{role}`. The route serves completed outputs and thumbnails to callers who can access the draft. Islands are same-origin with cookie auth (item 52), so no signed URLs.
37. **Correction proposals list.** `GET /v1/submissions/revisions` lists proposals for staff, filtered by `status` (`pending`, `applied`, `expired`) and `build_id`, sorted by `id` or `oldest`. Pending includes proposals that don't have a matched build yet. `before` and `after` are keyed by a fixed set of build attributes, not form fields, so M3 labels them itself. Needed by M1 and M3.

### Voting and review

39. **Review queue.** `GET /v1/vote-sessions` pages build submission and edit votes for callers who can see pending builds. Repeated `kind` and `status` filter it, and `sort` is `newest` or `oldest`, so `status=open&sort=oldest` is the queue. Each item is a `VoteSessionDetail` with the caller's `own_selection`, plus the build summary and `submitted_by`. Tab badges come from each list's `total` (correction proposals are item 37). Build votes have no deadline. Needed by M1.
40. **Web voting.** `POST /v1/vote-sessions/{id}/votes` takes only an `option_id`, and `DELETE` takes nothing. Build reviews and network polls accept web ballots; log deletion votes and server polls are voted on their Discord cards only and refuse web ballots. A build review ballot is weighed from the voter's stored roles in the network's owner guild, a network poll ballot weighs 1, and each keeps its weight. `VoteSessionDetail.viewer` is `{eligible, weight}` for signed-in callers while the session is open, and each option carries its `multiplier`. See [the voting redesign](../voting-redesign.md). Needed by M2.
41. **Visible poll voters.** `VoteSessionDetail.voters` lists `{display_name, option_id}` for `visible_live` polls, the only kind that shows ballots in Discord, and is null otherwise. Anonymous readers see it too. A hidden profile shows a null name. Build reviews need nothing more: M2 already loads the build, and polls carry `poll.deadline`.

### Schematics and media

42. **Schematic summary and videos.** `SchematicSummary` has `attached_at` and `blocks: {top: [{block, count}], other}`, with the ten most common blocks by name; the slices sum to `block_count` (chart 3). `blocks` is null for files analyzed before counts were stored. Filenames stay private, as they can name players. There's no per-schematic `render`: only the primary attachment gets a preview, and build detail already lists it in `media` as `kind: "render"`. `GET /v1/accounts/me/schematic-videos?schematic_id=` pages the caller's own videos.

### Admin

43. **Runtime settings metadata and Discord names.** Needed by X1, where raw snowflake inputs are unusable.
    - `RuntimeSettings` fields are annotated with a `SettingGroup` and, for Discord IDs, a `DiscordKind`. Both reach every settings property in OpenAPI as `x-squid-group` and `x-squid-discord-kind`. The web localizes labels and descriptions by field name.
    - `GET /v1/settings/runtime` returns each Discord ID as `{id, name}`. `name` is null when Discord no longer knows the ID or the bot cannot see it, and `names_resolved` is false when Discord was unavailable.
    - `PATCH` rejects a newly set ID of the wrong kind with a `discord_channel`, `discord_role` or `discord_user` rule issue (item 50), whose reason is `unknown` or `category`. Roles must belong to the owner guild; channels may be in any guild the bot sees, since build logs are read from partner servers. While Discord is unavailable, IDs are stored unchecked.
    - The owner guild is `BotIdentityConfig.owner_server_id`. `GET /v1/settings/runtime/channels` and `/roles` list its channels and roles for pickers.
44. **Diagnostics.** Needed by X2.
    - The list filters by `surface`, `error_code`, `work_lost` and an `occurred_after`/`occurred_before` range (a time without an offset is UTC). Anchors already seek on `(occurred_at, id)`.
    - `GET /v1/diagnostics/errors/stats` returns `{retention_hours, days: [{day, surface, count}]}` for chart 7, counting unexpired reports per UTC day. Retention is configurable, so the chart takes its window from `retention_hours`.
    - No per-report delete. Reports expire anyway, and deleting one just destroys evidence.
45. **Permissions.** Routes over `PermissionAdministrationService`, whose authority and rank checks decide every write. Needed by X6.
    - Subjects are accounts, by public ID, or Discord roles with their guild. Stored rows are addressed by ID: `GET`, `POST /v1/permissions/grants` and `DELETE /grants/{id}`, the same for `/assignments`, and `/roles` with `PATCH` for rank, `POST`/`DELETE /roles/{id}/patterns` and `/roles/{id}/includes`. Grants are granted and revoked, never edited.
    - `GET /v1/permissions/nodes` lists the catalogue by domain. `GET /explain` returns each node's decision with its rule trace (origin, source and the role it came through). An account explained with `guild_id` counts its member roles there, which needs Discord; callers without global authority see only guild-scoped nodes, as `/access` shows them.
    - `GET /v1/permissions/audit` pages by cursor (item 55).
    - The website acts without a guild, so only global administrators can change permissions there; server admins keep using `/access`. Acting for a guild would need the caller's own membership resolved per request.
46. **Record operations.** `POST /v1/records/{id}/recompute`, `GET /v1/records/{id}/inspection`, `POST /v1/records/recomputes`, `GET /v1/records/recompute-queue` and `GET /v1/records/drift`; see [Admin operations](../completed/incremental-records.md#admin-operations). Needed by X7.
47. **Version corrections.** `POST /v1/versions` adds a release the announcement channel missed. `PATCH /v1/versions/{id}` corrects edition, release numbers or `data_version` in place, so builds and records that use the entry follow it; the change is recorded as a build edit of every build that uses it. `DELETE` only removes an entry nothing refers to and answers 409 `VERSION_IN_USE` otherwise. Needed by X8.

### Home and servers

48. **Home data.** P1 makes parallel calls: `records?sort=recently_changed&page_size=6`, `builds?sort=newest&page_size=6`, and `GET /v1/stats` for `{builds, records, creators}`. `builds` and `records` match the default lists' totals. `creators` counts the creators and designers on approved builds, one per public account. A per-month record chart can come later, since P1 doesn't show one.
49. **Server directory.** `GET /v1/minecraft/servers` pages Paper servers with a public profile, oldest first, for P16. Turning on the public profile is the opt-in to being listed. `sponsor_opt_in` only credits the server on builds submitted from it, and it shows here as `sponsor`.

### Cross-cutting

50. **ProblemDetail field errors.** Validation failures include `context.fields: [{field, code, params}]`. Core reports domain field IDs, including fields on saved drafts. Request decoding reports JSON Pointers rooted at the request location. Request-wide failures have an empty list. Use the SDK’s `ValidationIssue` union and `FieldErrorCode` for exhaustive formatters. Multiple issues can target one control.
52. **Cookie and origin strategy.** Decided: islands call the API on the site's own origin, where the gateway already routes `/v1`, and take the CSRF token from `GET /v1/auth/csrf`. No Astro proxy. See "Same-origin API" in [README.md](README.md). No API change needed.
54. **A stable revision-conflict code.** Every stale-revision failure answers 412 with code `REVISION_CONFLICT`. This covers a stale `If-Match` or `base_revision`, a draft or build whose row moved under a change, and a lost commit race. Public context carries `expected` and `current` when the server knows them, plus `build_id` for builds and `changes` for a stale build edit draft. An `If-Match` tag for another item is a malformed request: it answers 412 `DOMAIN_ERROR`.
59. **Resource addresses.** A resource lives at `/{collection}/{id}`, keyed by an ID that never changes. Names are found by lookup, never used as path keys.
    - **Lookups** are `GET /{collection}/lookup?{key}=`, returning one resource or 404 with the same body as the ID route. Query parameters carry any text, whereas creator names in a path would need percent-encoding, and `%2F` is decoded inconsistently by proxies. Tags and creator aliases have one.
    - **Stored links use IDs.** Notification hrefs, follows and payloads never embed a name. The website may keep readable routes such as `/creators/{name}` and `/tags/{slug}` that resolve through a lookup.
    - **Filters** follow item 58: `tag_id` and `tag_slug`, never one parameter that guesses, and `account_id` for accounts.
    - **Values defined in code** (`/auth/{provider}`, `/suggest/{source}`, `/media/{kind}`) and opaque references (`/diagnostics/errors/{reference}`, `/schematic-renders/{recipe_hash}`) may be path segments.
    - **Accounts** are one collection keyed by the public UUID, which survives merges; the integer ID never appears in the API. `/accounts/me` is the caller's private view and holds its self-only routes (Discord build indexing, identities, merges, notifications); `/accounts/{id}` is the public profile. Routes that owners and staff share, such as `/accounts/{id}/profile` and `/minecraft/refresh`, take an ID or `me`, and core decides which permission applies.
    - **Creators** are credited names: `/creator-aliases/{id}` by alias ID, with the account summary when claimed. "Creator" stays out of account routes because unclaimed creators have no account; account references in bodies are `account_id`, not `creator_id`.
