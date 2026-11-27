# Screens

Every screen the site needs to expose squid/core. IDs (P public, A account, S submission, L link flows, M moderation, X admin) are referenced from [components.md](components.md) and [api-gaps.md](api-gaps.md).

Rules that apply to every screen:

- The screen works without JavaScript for reading and navigating.
- Listing state lives in the URL.
- Actions that need a signed-in user link to sign-in when the visitor is anonymous.
- Staff-only actions are never shown to people who cannot perform them.

## Public catalogue

**P1. Home `/`**
- *Goal:* learn what Squid is and get to something interesting in one click.
- *Needs:*
  - Hero with a site-wide search box (`SearchBox`) and scope shortcuts.
  - Counts: builds, records, creators.
  - "Recently broken records": the record, the new holder and the previous holder, with time.
  - "Newly approved builds" as cards.
  - Entry points to Records by category (door, entrance, extender, utility), the door range explorer, Submit, and the Discord community.
- *Actions:* search, browse, submit, sign in.

**P2. Search `/search?q=&scope=&sort=&view=&offset=&hide=&open=`**
- *Goal:* find builds or records matching precise criteria, such as "flush 3x3 doors under 10 ticks".
- *Needs:*
  - Query box with field-aware autocomplete and inline query errors (unknown field, bad value) at the error's position.
  - Scope tabs (all, builds, records, metadata) with a count on each tab.
  - Active-filter chips parsed from the query, each removable.
  - A structured filter panel that edits the query string. Fields come from `/v1/search/fields`: range fields get min/max inputs, enum fields get choices.
  - Sort, total results, and a cards/table toggle (builds scope).
  - Records scope: a table whose metric columns come from the fields, with expandable rows grouped by build (`group_by_build`).
  - Each result shows its matched text highlighted.
- *Actions:* refine, sort, switch view, show or hide columns, page, open a result, and "Notify me about new records matching this" (`record_filter` subscription), plus copy link.
- *Empty state:* echo the query, list broadening suggestions (remove a filter, switch scope), and link to help.

**P3. Search help `/search/help`**
- *Goal:* learn the query language.
- *Needs:* syntax overview (text, phrases, `field:value`, ranges, `and`/`or`/`not`, units), and a field reference generated from the fields endpoint: name, aliases, type, unit, scopes, whether it can be sorted, and example values. Each example is a clickable search.

**P4. Builds `/builds`**
- *Goal:* browse the catalogue without writing a query.
- This is the same component as P2 with `scope=builds` preset and a curated filter panel: category, version, size, timings, restriction and pattern tags, creator.
- Cards by default, table toggle.
- Sorts: newest, recently updated, fastest opening, smallest.

**P5. Build detail `/builds/[id]`**
- *Goal:* understand a build and decide whether to use or download it, or who made it.
- *Needs:*
  - Door-rules title and subtitle, with category and status badges.
  - Media gallery: images, videos, and the schematic render.
  - Key stats: dimensions, door/opening size, opening and closing times (visible and true), extension length and time for extenders.
  - Credits grouped by role, linking to creators.
  - Versions it works in, shown as a range or a list, with an "up to date" badge.
  - Tags grouped by kind (restrictions, patterns, showcase), each linking to its tag page.
  - **Records this build holds, now and previously.**
  - For expandable doors, the door expansion (parameters, branches, timings) inside a disclosure.
  - Description, world downloads, and the sponsor server.
  - Schematics: download, format, size, block count, license, and block composition.
  - Render and video generation with job status.
  - "Last edited" with a link to history.
- *Actions:*
  - Everyone: download a schematic, copy link, share.
  - Signed-in users: follow the creators, and suggest a correction (opens a draft prefilled from the build).
  - Owners and staff: edit the build.
  - Staff on a pending build: vote, approve, reject.
- Pending and rejected builds are visible to their owner and to staff, with a status banner.
- *Related links:* records held, versions, history (edits).
- *Primary action:* staff on a pending build: vote; owners and staff: edit; everyone else: download.

**P6. Build history `/builds/[id]/history`**
- *Goal:* see what changed, when, and by whom.
- *Needs:* a timeline of edits with actor, time, operation kind, and per-field before/after diffs (labels, not column names). Paginated.

**P7. Records `/records`**
- *Goal:* see the current record holders in a category.
- *Needs:* filters for build kind, criterion, version scope, tags, and status (resolved, unresolved, no candidate). Default view is a table: title, criterion, holder or holders, primary metric, last changed, freshness. A card toggle exists. Sort by recently changed or by title.

**P8. Record detail `/records/[id]`**
- *Goal:* who holds this record, by how much, since when, and how did it get here.
- *Needs:*
  - Title and subtitle, criterion, kind, version scope, and resolution status, with a plain-language explanation of unresolved or no-candidate.
  - Holders and co-holders as cards with their metric values.
  - Freshness (computed or last checked, refresh pending).
  - Holder history: a timeline plus a progression chart.
  - Sibling records: the same category with a different criterion or version scope, shown as a switcher.
  - Door evidence, in a disclosure.
- *Actions:* follow this record (subscription), open the holder, open the range explorer for this category.
- *Related links:* holders, past holders, related records (same category, other criteria or versions).
- *Primary action:* follow.

**P9. Door range explorer `/records/ranges`, `/records/ranges/[queryId]`, `/records/ranges/[queryId]/at/[width]`**
- *Goal:* for a door category (orientation, types, restrictions, height, depth, version), see the smallest record at every width.
- *Needs:*
  - A category form using named tags, not IDs.
  - A range chart: width against record size, segmented by holder region.
  - A table of the same data.
  - Analysis status (exact, unsupported, limit exceeded, timed out) and freshness.
  - The per-width point page shows the holder configurations.
- *Actions:* change category, select a width, open a holder, share the link. Staff can refresh.

**P11. Tags `/tags`**
- *Goal:* look up what a restriction or pattern means.
- *Needs:* a glossary grouped by kind, with a filter box. Each entry shows name, aliases, unit, and build and record counts, and links to P12.

**P12. Tag `/tags/[slug]`**
- *Goal:* understand a tag and see where it is used.
- *Needs:* name, kind, authority (official or user), description, aliases, unit or value type, and a link to the door-rules section. Below that, builds with the tag (cards or table, paginated) and records that involve it.
- *Actions:* search with this tag, and follow new records with this tag.
- *Related links:* builds, records.
- *Primary action:* search with this tag.

**P13. Creator `/creators/[name]`**
- *Goal:* see a creator's body of work.
- *Needs:* display name, avatar, pronouns, bio, links, public identities, joined date and aliases. Stats: builds, records currently held, records ever held. Records held, and builds as cards or table.
- *Actions:*
  - Signed-in users: follow the creator.
  - Unclaimed alias: "Is this you? Claim this name", with an explanation of how claiming works.
  - The owner viewing their own page: edit profile.
- A hidden profile renders the alias's builds only. A merged creator redirects.
- *Related links:* builds, records held, past records.
- *Primary action:* owner: edit profile; unclaimed alias: claim; everyone else: follow.

**P14. Content pages: `/about`, `/rules` (door rules), `/privacy` (consent notice)**
- *Needs:* long-form prose with a table of contents and anchored headings.

**P15. System pages: 404, 503, `/offline`**
- *Needs:* explanation, a way back, and search.

**P16. Sponsors `/sponsors`** (optional)
- *Goal:* find servers that host the catalogue plugin.
- *Needs:* server name, address with a copy button, description, website.

## Account (signed in)

**A1. Sign in `/sign-in?redirect_to=` and consent `/consent`**
- *Goal:* authenticate, then return to what the user was doing.
- *Needs:* a provider button (Discord) and a one-line explanation of what is stored. If consent is pending, the privacy notice is shown with accept or decline before continuing. Errors returned from the OAuth callback are shown inline.

**A2. Profile `/account`**
- *Goal:* control the public profile.
- *Needs:* display name, pronouns, bio with a character count, links as a repeatable label/URL list, an avatar chosen from linked identities, and a "Hide my profile" switch. A preview link to P13.

**A3. Connections `/account/connections`**
- *Goal:* manage linked Discord, Java and Bedrock identities.
- *Needs:*
  - Identity list with provider, name, verified date, and a public/private switch.
  - Unlink, with a confirm.
  - "Link Minecraft", which explains the in-game flow and links to L2.
  - Minecraft name refresh, with its result: renamed, claimed alias, contested alias, or pending claim.
  - **Merge accounts**: generate a code on one account, enter it on the other, preview what moves (aliases, identities, build count), then confirm.

**A4. Devices and servers `/account/devices`**
- *Goal:* see and revoke everything that can act as the user.
- *Needs:*
  - CLI devices: label, fingerprint, created, last used, revoke.
  - Minecraft grants: origin, server, expiry, revoke.
  - Paper server installations: label, sponsor profile, created and rotated dates, with create, rotate secret, edit sponsor profile, and revoke. The secret is shown once.
  - Sign out.

**A5. My builds `/account/builds`**
- *Goal:* track submissions.
- *Needs:* tabs for pending, approved and rejected, with counts. Pending rows show vote progress against thresholds. Rejected rows show the reason when there is one. Each row offers edit (pending) and view.

**A6. Notifications `/notifications`**
- *Goal:* catch up on what happened.
- *Needs:* inbox with an unread filter and unread count. Each item has a typed summary ("Your door *X* now holds 2 records"), a time, and a link. Mark read or unread, and mark all read. The empty state explains how to follow things.

**A7. Notification settings `/account/notifications`**
- *Goal:* choose channels and subscriptions.
- *Needs:*
  - Web inbox and Discord DM switches, with the consent prompt if consent is pending, and a "DMs suspended" warning with a fix.
  - The subscription list with readable subjects: creator name, record title, or a filter summary. Each can be removed.

## Submissions

**S1. Submit `/submit`**
- *Goal:* start or resume a submission.
- *Needs:*
  - Category picker as large choice cards with a short description each.
  - "Your drafts": title or "Untitled", category, status, last edited, expiry warning, completeness.
  - "Needs your review": drafts the bot inferred from Discord messages (correction inbox).
  - A CLI and plugin hint.
- *Actions:* start, resume, discard.

**S2. Draft editor `/submit/[draftId]`**
- *Goal:* complete a submission confidently, without losing work.
- *Needs:*
  - A schema-driven form: sections, conditional fields, repeatable fields, credits and showcase-tag editors, duration fields with units, and choice fields fed by option sources.
  - Autosave with a visible `SaveStatus`.
  - Field-level problems shown live, and section navigation with completeness per section.
  - Media uploads with processing status and limits.
  - Schematic upload; its measurements become one-click "Use these values" suggestions.
  - Supplied files.
  - When the manifest is upgraded, a notice with "Update form".
  - Expiry notice.
  - Submit panel: an error summary, the records this build would hold, share or miss (a prediction), then finalize.
- *Actions:* edit, upload, remove (with undo), choose the primary schematic, submit, discard the draft.

**S3. Submission status `/submit/[draftId]/status`**
- *Goal:* know what happens after submitting.
- *Needs:* attempt status as steps (queued, processing, needs attention, completed). Issues link back to their fields. When complete, a link to the build. Vote progress and the predicted records once the build is pending.

## Device link flows

**L1. CLI link `/cli/link?code=`**
- *Goal:* approve a CLI device safely.
- *Needs:* the code entry (prefilled from the URL), then a preview of device label, fingerprint and expiry with a warning to approve only devices the user started, then approve, then done.

**L2. Minecraft link `/minecraft/link?code=`**
- Same flow as L1. The preview shows the Java UUID or player name, the origin (Paper or Fabric), and the server.

## Moderation (permission-gated)

**M1. Review queue `/review`**
- *Goal:* process pending builds and correction proposals.
- *Needs:* a table of pending builds with title, category, submitter, submitted date, vote tallies against pass and fail thresholds, and my vote. A second tab holds correction proposals with source, build, expiry and change count. Filters and sort.

**M2. Review build `/review/builds/[id]`**
- *Goal:* decide on one build.
- *Needs:* the P5 content and the records approval would change, plus a vote panel (options, my vote, tallies, thresholds) and edit access. Approve and reject are shown only if the user has those permissions.

**M3. Correction proposal `/review/corrections/[id]`**
- *Goal:* accept or reject an inferred correction.
- *Needs:* the source message evidence, the matched build (with a change-match picker), and a before/after diff per field. Approve, and an expiry notice.

**M4. Edit build `/builds/[id]/edit`**
- *Goal:* fix a build.
- *Needs:* the same form renderer as S2, prefilled; a conflict warning if someone else edited; a reason or summary for the change. On save, go to P5 with a success notice.

## Admin (permission-gated)

**X1. Runtime settings `/admin/settings`**: grouped settings (welcome, redstoner, voting thresholds, retention, channels). Channel and role pickers show names, not snowflake IDs. Save per group, with revision conflict handling.

**X2. Error reports `/admin/errors`, `/admin/errors/[ref]`**: a filterable table (surface, code, work lost, date) and a lookup by reference. The detail page shows message, context, traceback (collapsible, copyable), log tail, and matching references. "Clear all" uses typed confirmation.

**X3. Tag proposals `/admin/tags`**: a queue of user-proposed tags with approve, reject or archive. Also restriction alias creation.

**X4. Alias claims `/admin/claims`**: the claimant, the alias, evidence, and the alias's build count. Approve or reject.

**X5. Profile moderation `/admin/profiles/[accountId]`**: look up an account, review its profile, clear it (with If-Match).

**X6. Permissions `/admin/permissions`**: browse nodes by domain, inspect a subject's effective permissions and where each came from, view the audit log, and manage grants and roles.

**X7. Record operations `/admin/records`**: inspect a record's computation, rebuild a record, refresh range queries, and see failed computations.

**X8. Versions `/admin/versions`**: list Minecraft versions and add one.

## Deferred: stays in Discord

Starboard, welcome relay, redstoner role, per-guild voting settings, posts, reactions and message sync are configured inside Discord and scoped to a guild. Exposing them on the web would need guild membership and permission plumbing (`settings.server.*`, `starboard.*`), so they are out of the first web scope. If they are added later, they reuse `SettingsList` and `DataTable` without new components.

---
