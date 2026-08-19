# BUGS

The `admin records-lookup` unmatched-category report (`b03322f1d85e`) was fixed as part of the
systemic structured-error migration in `docs/plans/structured-errors.md`; the application/domain
architecture rule now rejects new bare builtin raises.

## `verification_codes.id` exhausts after 32,767 codes

`VerificationCode.id` is a `SmallInteger` autoincrement primary key
(`squid/accounts/infrastructure/models.py:227`), and no path ever deletes a row:
`replace_verification_code` (`squid/accounts/infrastructure/repository.py:629-652`) only flips
`valid = False` on prior codes and inserts a replacement. The identity sequence therefore climbs
monotonically with every in-game `/link` and stops at 32,767, after which issuing a code fails
outright and Minecraft accounts can no longer be linked at all. Nothing rewinds or reuses the range.

Found while auditing the table for
`docs/plans/pr-183-review/01-consent-verification-ux.md` §1; not a UX matter, so that plan records it
rather than fixing it. Unbounded growth of expired rows is the same root cause and wants the same
answer: widen the key and reap consumed codes.

## `mention_fallback_search`'s own guard clause is unreachable

`SearchCog.mention_fallback_search` (`squid/bot/submission/search.py:318-353`) is an
`on_command_error` listener meant to fall back to a search when a mention isn't a real command. It
asserts `ctx.command is None` at line 328 to scope itself to non-commands, then *later* (line 341)
checks `ctx.invoked_parents or ctx.invoked_subcommand` and logs "A CommandNotFound is being raised
despite a subcommand being invoked" before returning cleanly — the author's own comment above it
says "this should never happen, but just in case." It does happen: discord.py raises
`CommandNotFound` with `ctx.command` set to the parent group when a mention names a real group but
an unknown subcommand, and that hits the assert first, so the guard clause below it is dead code.
The assert then blows up inside `on_command_error` itself, so the original `CommandNotFound` is
never actually handled — discord.py just logs "Ignoring exception in on_command_error" instead.
Confirmed still present in the current tree; caught 4 times in production error reports on
2026-08-17 between 04:36 and 06:35 UTC (references `c6f9a82d6436`, `29887f2aef04`, `31f47752a5c2`,
`b573f90c414b`). Move the assert after the `invoked_parents`/`invoked_subcommand` check, or drop
it — the check it guards against is already handled two lines below.

## `on_message` build inference has no error boundary

`SubmissionCog.infer_build_from_message` (`squid/bot/submission/submit.py:320-341`) is a raw
`on_message` listener that calls `ingest_message_bundle` → `BuildInferenceService.infer` →
`VersionService.resolve_spec` with nothing catching exceptions along the way. When the AI-inferred
`version_spec` names both editions, `VersionService._edition_from_spec`
(`squid/versions/application/services.py:66-73`) raises `InvalidVersionError`, which propagates
straight out of the listener; discord.py's default handling just logs "Ignoring exception in
on_message" and drops it. The user who posted the build gets no card, no error message, and no
indication anything went wrong — the message is silently never turned into a submission. Caught in
production on 2026-08-17 11:34:53 UTC (reference `a3f96a34997f`) via a message in a build-log
channel using mixed Java/Bedrock language. The same gap swallows any other exception from inference or
version resolution, not just this one; `infer_build_from_message` needs the same kind of error
boundary the app-command surfaces already have.

Still firing: five more in the 2026-08-18 error reports, between 04:10 and 11:09 UTC (references
`d9b016be8c19`, `c0b62ea509e8`, `5887677059b6`, `d8c413752442`, `c5e9d013946d`), and two more
on 2026-08-18 20:13 UTC and 2026-08-19 11:34 UTC (`23c7639b6fe5`, `b7f54dc478c8`). All seven came
through the bare-`ValueError` hole below rather than `_edition_from_spec`, which makes that the
dominant trigger in practice — but the missing boundary is what turns any of them into a silent
drop, so it is still the fix that matters.

## `VersionService.resolve_spec` leaks bare `ValueError` on non-numeric version text

`resolve_spec` parses version numbers with `tuple(map(int, value.split(".")))` at three sites —
inline at `squid/versions/application/services.py:56`, and in `_parse_version_numbers` (line 85)
and `_parse_range_end` (line 99). None of them validate first, so any token that is not an integer
escapes as a raw `ValueError` from `int()`. Each of those functions raises `InvalidVersionError`
two lines later for the *wrong arity* case, so the module already knows what the right error is;
it just never reaches it, because `int()` throws before the length check runs.

This is the most frequent error in the store: 5 of 40 reports, all from AI-inferred `version_spec`
strings that carry prose the parser was never meant to see — `'Edition'` (from a spec like
`Bedrock Edition 1.21`, where the `Bedrock`-stripping at line 41 leaves the word `Edition`
behind), `'16+'`, `'16+ (shown as 1'`, and `'MCBE 1'` twice. The last two are the interesting
ones: `MCBE` is not one of the two literals line 41 strips, so a Bedrock spec written the way
players actually write it is resolved against the *Java* catalogue and then dies on the prefix.

Being a builtin rather than a domain error, it also bypasses the structured-error handling the
rest of the stack relies on — the `except InvalidVersionError` a caller would reasonably write
does not catch it. Validate before `int()` in all three sites and raise `InvalidVersionError` with
the offending text in `context`, and consider widening line 41's edition stripping to the aliases
inference actually emits.

## `on_message_version_add` has no error boundary either

`VersionTracker.on_message_version_add` (`squid/bot/version_tracking.py:54-69`) is the second raw
`on_message` listener with nothing catching exceptions. Every message posted in the version-tracker
channel has its first line fed straight to `VersionService.add`, which calls `parse_version_string`
and raises `InvalidVersionError` for anything that is not a version — so ordinary chatter in that
channel raises out of the listener and discord.py logs "Ignoring exception in on_message". Caught
in production on 2026-08-17 17:48:21 UTC (reference `fac83e0a8fe3`) and again on 2026-08-18 16:53:43 UTC
(reference `6e8919db2951`). Unlike the build-inference
case nothing is silently lost, but the poster gets no feedback that their line was rejected, and
every non-version message files an error report, so the channel's normal traffic is indistinguishable
from real failures in the store.

Line 66 is a second, latent fault on the same path: `self.bot.get_channel(channel_id).send(...)` is
`# type: ignore`d over a `Channel | None`, so an uncached channel turns the success path into an
`AttributeError` with the version already written to the database.

## Autocomplete's response deadline cancels inside SQLAlchemy's greenlet bridge

`suggests` (`squid/bot/utils/autocomplete.py:80`) bounds a suggestion lookup with
`anyio.move_on_after(RESPONSE_BUDGET_SECONDS)` so a slow source degrades to an empty dropdown. The
budget works, but the cancellation lands inside an in-flight SQLAlchemy query: on 2026-08-18
05:58:55 UTC a `build submit` autocomplete was cut off in `VersionRepository.list`
(`squid/versions/infrastructure/repository.py:42`) and the `CancelledError` — "Cancelled via cancel
scope 74eb80be8730; reason: deadline exceeded" — propagated out through `greenlet_spawn` into
`AsyncAdaptedQueuePool`, which logged it (reference `eda0731f0cb6`). The `except Exception` at line
82 cannot see it, by design; the pool is where it surfaces.

Cancelling mid-query through the greenlet bridge is the hazard CLAUDE.md's concurrency rules exist
to avoid: the connection's state at the point of the throw is not well defined, so the pool
discards it. That means the failure mode is self-reinforcing — the slower the database, the more
autocompletes hit the deadline, and each one costs a pooled connection. Six `10062 Unknown
interaction` reports for the same command and user in the surrounding 2.3 seconds (`ccb6196da0af`,
`b4f0c1c3305f`, `88bdb1d37d3a`, +3) are the same incident seen from Discord's side. The budget
wants to bound the *response*, not kill the query: run the lookup as an owned task and abandon its
result on deadline, or give the query its own statement timeout instead of cancelling it.

## `verification_codes.code` has no index

The model declares no `__table_args__`, so every redemption's lookup on the peppered digest
(`repository.py:429-437`) is a sequential scan. Harmless at the table's current size and masked by
the ten-minute expiry, but it compounds with the unbounded growth above, and
`01-consent-verification-ux.md` §1 adds a second lookup per link.

## `_ServerSettingModelRepository` assumes default `id` attribute, crashing all `ServerSetting` updates

`ServerSetting` (`squid/settings/infrastructure/models.py:18`) defines `server_id` as its primary key.
`_ServerSettingModelRepository` (`squid/settings/infrastructure/repository.py:13-15`) subclasses
`BaseAsyncRepository[ServerSetting]` (Advanced-Alchemy's `SQLAlchemyAsyncRepository`), which defaults
`id_attribute = "id"`. When `repository.update(row)` is called (`SettingsRepository.set`, `set_locale`,
or `on_guild_remove`), Advanced-Alchemy calls `self.get_id_attribute_value(data, id_attribute=id_attribute)`
and inspects `row.id`, raising `AttributeError: 'ServerSetting' object has no attribute 'id'`, which
Advanced-Alchemy wraps and re-raises as `advanced_alchemy.exceptions.RepositoryError: There was an error during data processing`.

Caught in production on 2026-08-19 04:30:30 UTC (reference `e6125caaa758`) when changing a setting channel
via `SettingChannelSelect` (`squid/bot/settings_view.py:363`). Any update to existing server settings or
locale configuration fails. Fix by setting `id_attribute = "server_id"` on `_ServerSettingModelRepository`.

## `/search query:...` autocomplete only suggests build search syntax and is unaware of `scope`

The `/search` command (`squid/bot/submission/search.py:175-191`) accepts a `scope` option
(`SearchTarget`: `records`, `builds`, `patterns`, `restrictions`, `everything`) alongside `query`, `sort`,
and `mode`. Autocomplete for `query` is wired with `@autocompletes(sort="search_sorts", query="search_query")`.

`SearchQueryProvider` (`squid/suggestions/infrastructure/providers/search_query.py:42-105`) completes query
tokens against `DEFAULT_FIELD_REGISTRY` (`squid/search/application/fields.py:97-132`) and queries `build`
facet values. It receives no `scope` context from the Discord interaction options, nor does `SearchQueryProvider`
or `FieldRegistry` filter fields and facet completions by `SearchScope`.

When a user selects `scope: restrictions` or `scope: patterns` (which query `SearchScope.METADATA`), or
`scope: records`, autocomplete still offers build-only fields (such as `width:`, `height:`, `volume:`,
`closing_time:`) and build facet values instead of metadata-specific fields (`kind:`, `category:`, etc.).
`suggests` in `squid/bot/utils/autocomplete.py` needs to extract `scope` from the interaction options, and
`SearchQueryProvider` / `FieldRegistry` need to scope available fields and facet values to the target `SearchScope`.

## Transient gateway DNS reconnect errors flood `error_reports` and dead-letter sync jobs

During network or DNS hiccups, `discord.client` logs gateway reconnection attempts with attached
`aiohttp.client_exceptions.ClientConnectorDNSError` exceptions at `ERROR` level ("Attempting a reconnect in Xs").
Because `ErrorReportLogHandler` captures every logged exception at ERROR level (`surface = log`), normal
reconnection backoff floods the `error_reports` table — 47 reports filed between 2026-08-19 06:03 and 11:13 UTC
(references `fc256f2fa85b`, `4ee47ebb17d8`, ..., `ff0faad1da07`).

During the same incident, `squid.bot.sync.reconciler` (`squid/bot/sync/reconciler.py:54`) encountered a DNS timeout
connecting to `discord.com:443` and dead-lettered a post reconciliation job (reference `3ad3d5bd7b99` on 2026-08-19
09:59:35 UTC, `work_lost = true`). Transient gateway reconnect chatter should be ignored by the log-capture handler,
and reconciler jobs need backoff/retry tolerance for transient network outages before dead-lettering.

## Background worker pool checkout fails on transient DNS glitch without retry

On 2026-08-18 13:09:00 UTC, the `schematic-jobs` background job worker crashed during database pool checkout
when `asyncpg.connect` raised `socket.gaierror: [Errno -3] Temporary failure in name resolution`
(reference `1da5b8e2f1a8`, `surface = background_job`). The background supervisor logged and captured the failure
immediately rather than retrying connection acquisition with exponential backoff on transient socket/DNS errors.

## `/help` command does not defer, causing `10062 Unknown interaction` and cascade in error responder

`HelpCog.help` (`squid/bot/help.py:77-125`) performs async work (resolving guild locale overrides via
`resolve_locale` and inspecting command trees) before calling `interaction.response.send_message`, without
first calling `interaction.response.defer()`. If database queries or event loop latency push the response past
Discord's 3-second interaction token deadline, `send_message` fails with `NotFound: 404 Not Found (error code: 10062): Unknown interaction`
(reference `93240ad042f4` on 2026-08-19 12:56:08 UTC).

The failure then cascades: `squid.bot.errors._handle_discord_error` catches the exception and attempts to send
an error card via `interaction.response.send_message` (`squid/bot/errors.py:318,369`), which immediately throws
a second `10062 Unknown interaction` because the interaction is already dead, filing a second error report. `/help`
should defer immediately, and the error handler should recognize expired interaction tokens instead of attempting
an invalid response.
