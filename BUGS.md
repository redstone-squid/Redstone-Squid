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

## `verification_codes.code` has no index

The model declares no `__table_args__`, so every redemption's lookup on the peppered digest
(`repository.py:429-437`) is a sequential scan. Harmless at the table's current size and masked by
the ten-minute expiry, but it compounds with the unbounded growth above, and
`01-consent-verification-ux.md` §1 adds a second lookup per link.
