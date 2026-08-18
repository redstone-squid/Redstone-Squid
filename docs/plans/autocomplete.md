# Cross-surface suggestions

Status: implemented, except where noted under [Not done](#not-done).

## Why

Three surfaces needed to complete the same values and each had grown its own answer. Discord had
three ad-hoc `rapidfuzz` calls across roughly sixty commands and nothing else, so users typed
`build_id` on nine commands, `tag_id` on four, bare UUIDs into `/notifications follow-creator`, one
of nineteen undocumented keys into `/starboard edit`, and comma-separated numeric restriction IDs
into `/admin records-lookup`. The web catalogue called `/v1/search/suggest`, which returns bare
title strings with no id or kind and which parses the query before suggesting — so `restriction:`
raised a syntax error rather than listing restrictions. The Minecraft plugin's Brigadier tree had
no suggestion provider at all.

Adding autocomplete to one more parameter meant writing a fourth ranking function.

## The registry is the contract

A **suggestion source** is a stable snake_case id resolving to a provider
(`squid/suggestions/infrastructure/catalogue.py`). Ids extend the `option_source` namespace the
submission form manifest already publishes and keep its `^[a-z][a-z0-9_]{0,63}$` shape, so a form
field, a slash parameter, and a game command that mean the same thing name the same thing. Adding
a source here makes it completable from Discord, from `GET /v1/suggest/{source}`, and from a
Minecraft command at once.

`tests/unit/suggestions/test_catalogue.py` pins the properties below;
`tests/unit/bot/test_command_autocomplete_wiring.py` walks the real command tree and fails if a
command names a source that is not registered — the failure discord.py cannot catch, since it only
validates that the *parameter* exists.

| Source | Kind | Value | Requires | Context | List |
|---|---|---|---|---|---|
| `alias_claims_pending` | queried | integer | `account.claim.list` | — | — |
| `approved_patterns` | enumerable | string | — | — | `,` |
| `approved_restrictions` | enumerable | string | — | — | `,` |
| `approved_showcase_tags` | enumerable | string | — | — | `,` |
| `approved_source_versions` | enumerable | string | — | — | — |
| `build_kinds` | enumerable | string | — | — | — |
| `build_titles` | queried | string | — | — | — |
| `builds` | queried | integer | — | — | — |
| `builds_pending` | queried | integer | `build.submission.view_pending` | — | — |
| `competitions` | queried | string | — | — | — |
| `creator_profiles` | queried | string | — | — | — |
| `creators` | queried | string | — | — | `,` |
| `door_types` | enumerable | string | — | — | — |
| `locales` | enumerable | string | — | — | — |
| `notification_subscriptions` | queried | integer | signed-in viewer | — | — |
| `permission_nodes` | enumerable | string | — | — | — |
| `permission_patterns` | enumerable | string | — | — | — |
| `permission_roles` | queried | string | — | `guild_id` | — |
| `record_base_keys` | queried | string | — | — | — |
| `record_classes` | enumerable | string | — | — | — |
| `records` | queried | string | — | — | — |
| `restriction_ids` | enumerable | integer | — | — | `,` |
| `search_fields` | enumerable | string | — | — | — |
| `search_query` | queried | string | — | — | — |
| `search_sorts` | enumerable | string | — | — | — |
| `showcase_tag_ids` | enumerable | integer | — | — | — |
| `starboard_names` | queried | string | — | `guild_id` | — |
| `starboard_settings` | enumerable | string | — | — | — |
| `tags_pending` | queried | integer | `tag.proposal.list` | — | — |
| `version_ids` | enumerable | integer | — | — | — |
| `version_scopes` | enumerable | string | — | — | — |

`starboard_names`, `permission_roles` and `alias_claims_pending` are gateway-only; the API process
has no starboards, so those sources are absent there rather than registered against a service that
does not exist.

## Decisions worth keeping

**Visibility lives with the data, not the command.** discord.py does not run a command's checks
before its autocomplete callback, so gating on the command would leave the dropdown open. A source
that exposes unreviewed data declares its own node and is refused wherever it is read.

**Ids are completed by name.** Commands that persist a numeric identifier — `restriction_ids`,
`showcase_tag_ids`, `version_ids`, `creator_profiles`, `competitions` — complete by name and submit
the id. Nothing downstream changed; the step where a user had to look the number up elsewhere
disappeared.

**Enumerable sources cache behind a short TTL** rather than `alru_cache` with manual
`cache_clear()`. That pattern only invalidates the process that made the edit, so a restriction
alias added over the API stayed invisible to Discord until the bot restarted.

**Ordering is decided in Python, not by the database collation.** An enumerable source's order
decides its content revision, and drafts pin revisions. `tests/unit/submissions/
test_suggestion_options.py` asserts the registry produces byte-identical revisions to the catalogue
it replaced.

**Facet values match on prefix only.** Small in-memory taxonomies go through the full tiered matcher
(exact → prefix → word-prefix → substring → fuzzy). Facet values cannot: there is no bound on how
many a field has, so matching has to be something an index can serve.

## Per-surface notes

**Discord** (`squid/bot/utils/autocomplete.py`) — a three-second window with no deferral, so the
adapter bounds every call and degrades to an empty dropdown; it owns its own error boundary because
`SquidCommandTree._call` short-circuits autocomplete before error handling. Integer sources emit
`Choice[int]`; list-valued parameters complete one entry and re-emit the whole string.

**HTTP** (`GET /v1/suggest/{source}`) — enumerable sources carry an `ETag` from their content
revision. Typeahead has its own rate-limit bucket, since under the generic read quota one user
typing would exhaust their budget for everything else.

**Web** (`web/src/components/SuggestField.tsx`) — one combobox component taking a source id. It
applies the replacement span, so a completion splices into the query rather than clobbering it.

**Minecraft** (`SquidCommandTree.kt`) — suggestions are gathered on the server thread, so providers
answer only from state the client already holds. A dynamic `optionSource` is deliberately not
resolved there.

## Not done

- `/squid set <field> <value>` serves inline `options` only. Values behind an `optionSource` need a
  cache warmed off-thread when the manifest loads.
- `/v1/search/suggest` and the `/api/suggest` web proxies still exist with no callers, kept so a
  browser holding a stale bundle across a deploy does not get a 404.

Resolved since this section was written: the Kotlin module's dependency verification was fixed
(`minecraft/gradle/verification-metadata.xml` now pins `kotlinx-coroutines-bom-1.11.0`, and Loom's
locally-merged Minecraft jar is excluded from checksum verification rather than pinned), and CI
now runs `./gradlew build` for the module (`minecraft-quality` in `.github/workflows/ci.yml`), so
`SquidCommandTreeTest` runs on every change to `minecraft/**`.
