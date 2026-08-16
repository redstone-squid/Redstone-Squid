# BUGS

## `/build submit` — retrying after a failed submission does nothing

After pressing "Submit for review" in the guided `/build submit` form and hitting an error on
the first attempt, pressing the button again does not work: the form stops responding to
interactions instead of retrying the submission.

**Root cause**

- `BuildSubmissionForm.submit` (`squid/bot/submission/ui/views.py:404-417`) defers the
  interaction, sets `self.value = True`, and calls `self.stop()` as soon as the required fields
  are present — *before* the submission is actually attempted.
- Control then returns to `submit_form` (`squid/bot/submission/submit.py:154-261`), which calls
  `await view.wait()` and, once it resumes, runs `draft.finalize()` and
  `await self.builds.submit(...)` (`submit.py:241-244`) with no `try`/`except` around it.
- Because `view.stop()` already ran, discord.py has deregistered the view's component listener
  by the time `self.builds.submit(...)` can raise. If it does raise, the exception propagates up
  to the global app-command error handler, which reports the error — but the original
  `workspace_message` is left showing the same "Submit for review" button with a view that no
  longer routes interactions anywhere. Clicking it again produces no response (or Discord's
  generic "This interaction failed").
- The user has no way to retry from the existing message; they must rerun `/build submit` from
  scratch and redo any edits made via the modals.

**Suggested direction**

Don't call `self.stop()` until the submission actually succeeds — e.g. keep the view alive and
loop on submit attempts, only calling `stop()`/setting `self.value` after
`self.builds.submit(...)` returns without error, and re-rendering the form with an inline error
message (similar to `self.validation_error`) on failure so the same message stays usable for a
retry.

## `/build submit` fails entirely when no vote channel is configured

If a guild has no "Vote" channel set, submitting a build still throws and the whole interaction
errors out, even though the build itself was already persisted successfully. It should degrade
gracefully instead of failing the submission.

```
RuntimeError: No configured Discord vote channel is available for build review.
  File "squid/bot/submission/submit.py", line 258, in submit_form
    await asyncio.gather(
  File "squid/bot/submission/build_handler.py", line 88, in post_for_voting
    await ensure_build_review(self.bot, build, await self.get_channels_to_post_to())
  File "squid/bot/voting/sessions.py", line 43, in ensure_build_review
    raise RuntimeError(msg)
```

**Root cause**

- `ensure_build_review` (`squid/bot/voting/sessions.py:22-43`) raises a bare `RuntimeError` when
  `get_channels_to_post_to()` (`squid/bot/submission/build_handler.py:47-70`) resolves no
  "Vote"-target channels for any of the bot's guilds.
- Both submission commands call this as a "nice to have" step alongside finalizing the response,
  but without isolating it: `submit_form` (`squid/bot/submission/submit.py:258-261`) and
  `submit_door` (`squid/bot/submission/submit.py:145-148`) both run
  `build_handler.post_for_voting()` inside `asyncio.gather(...)` with no
  `return_exceptions=True` and no surrounding `try`/`except`. By that point
  `self.builds.submit(...)`/`self.builds.submit_door(...)` has already committed the build, so
  the failure is purely cosmetic (no vote card posted) but it still propagates all the way to
  the app-command error handler and reports the whole submission as failed — leaving the
  workspace message stuck on the pre-submission form (compounding the first bug above) even
  though the build was actually saved.
- Unlike the schematic-analysis helpers in the same file (`_analyse_attachments`,
  `_record_analyses`, `_note_schematic_duplicates`), which explicitly catch `SquidError` and log
  a warning because they're enrichment rather than a prerequisite, nothing here treats
  "no vote channel configured" as non-fatal — and `RuntimeError` isn't a `SquidError` anyway, so
  it wouldn't be caught by that same pattern even if reused verbatim.

**Suggested direction**

Treat a missing vote channel as a recoverable configuration gap, not a submission failure: either
have `get_channels_to_post_to()` return an empty list without raising and let
`post_for_voting`/`ensure_build_review` log a warning and no-op when there's nowhere to post, or
wrap the `post_for_voting()` calls in `submit.py` the same way the schematic helpers are wrapped,
so a missing vote channel never prevents the success response (and, ideally, surfaces to staff
some other way that a build is stuck without a vote card).

## Edit build UI: filled dot is visually smaller than the unfilled dot

In the "Edit build" workspace, each field is prefixed with a dot indicating whether it has
unsaved changes. The filled dot renders smaller than the unfilled one, so changed fields read as
*less* prominent instead of more:

```
Fields in this section
○ Wiring Placement Restrictions:
• Component Restrictions: observerless
○ Miscellaneous Restrictions:
○ Normal Closing Time:
○ Normal Opening Time:
```

**Root cause**

- `BuildEditView.summary_text` (`squid/bot/submission/ui/views.py:584-592`) builds each line with
  `f"{'•' if item.modified else '○'} {item.summary}"`.
- `•` is U+2022 BULLET and `○` is U+25CB WHITE CIRCLE — different glyph families, not a
  filled/unfilled pair of the same shape. In Discord's rendering (and most fonts), BULLET is
  drawn noticeably smaller and lower than a WHITE CIRCLE of the same font size, so the "modified"
  marker looks like a faint dot next to the empty circle's clearly outlined ring, undercutting
  the intended emphasis.

**Suggested direction**

Use a matching filled/unfilled pair from the same glyph family so only the fill differs, e.g.
`●` (U+25CF BLACK CIRCLE) for modified and `○` (U+25CB WHITE CIRCLE) for unmodified.

## `on_command_error` crashes on any real command error, swallowing it

Any command that fails with a normal error can trigger an `AssertionError` inside an unrelated
listener, so `on_command_error` never gets to report the actual failure — discord.py just logs
"Ignoring exception in on_command_error" instead.

```
[ERROR] discord.client: Ignoring exception in on_command_error
Traceback (most recent call last):
  File ".../discord/client.py", line 508, in _run_event
    await coro(*args, **kwargs)
  File "/app/squid/bot/submission/search.py", line 322, in mention_fallback_search
    assert ctx.command is None, "This listener should only handle non-commands."
AssertionError: This listener should only handle non-commands.
```

**Root cause**

- `mention_fallback_search` (`squid/bot/submission/search.py:318-351`) is registered via
  `@Cog.listener("on_command_error")`, which discord.py dispatches for *every* command error
  across the whole bot — not only for the "bot was mentioned but no command matched" case this
  listener is meant to handle.
- The checks are in the wrong order: line 322 asserts `ctx.command is None` unconditionally,
  and only *after* that (line 325) does it check
  `if not isinstance(exception, commands.CommandNotFound): return` to bail out for unrelated
  errors. But `ctx.command` is only `None` for the `CommandNotFound` case this function actually
  wants; any other command's real error arrives here with `ctx.command` set, so the assertion
  fires first and raises before the `isinstance` guard ever gets a chance to return early.
- Because this happens inside the `on_command_error` handler itself, the `AssertionError`
  isn't routed anywhere — discord.py's own event dispatcher catches it and just logs "Ignoring
  exception in on_command_error", so the *original* command error that triggered the event is
  never surfaced to the user or anywhere else.

**Suggested direction**

Swap the order: check `isinstance(exception, commands.CommandNotFound)` and return early first,
*then* assert `ctx.command is None` (or drop the assert entirely, since the isinstance check
already scopes this to the intended case).

## Confirming an edit in `/build edit` 404s trying to update the workspace message

After confirming changes in the "Edit build" flow, the bot fails to show the "Changes saved"
result because it tries to edit the (ephemeral) workspace message through the wrong API path.

```
discord.errors.NotFound: 404 Not Found (error code: 10008): Unknown Message
  File "squid/bot/submission/ui/views.py", line 669, in submit
    await edit_layout(interaction.message, success, allowed_mentions=no_mentions())
  File "squid/bot/utils/components.py", line 177, in edit_layout
    return await message.edit(view=layout, allowed_mentions=allowed_mentions)
  File ".../discord/message.py", line 2988, in edit
    data = await self._state.http.edit_message(self.channel.id, self.id, params=params)
  File ".../discord/http.py", line 774, in request
    raise NotFound(response, data)
```

**Root cause**

- `BuildEditView.send` (`squid/bot/submission/ui/views.py:550-572`) always opens the workspace as
  an ephemeral message (`ephemeral: bool = True`, and every caller —
  `squid/bot/submission/edit.py:213`, `squid/bot/submission/ui/components.py:278,293` — either
  relies on that default or passes `True` explicitly).
- In `BuildEditView.submit` (`squid/bot/submission/ui/views.py:629-669`), after the user confirms,
  the handler already spent this interaction's one allowed initial response on
  `interaction.response.send_message(view=confirmation, ...)` (line 648) to show the confirmation
  prompt. Once that response is used, there's no interaction-response slot left to
  `edit_message` the original workspace message through.
- The code then falls back to `edit_layout(interaction.message, success, ...)` (line 669), which
  goes through `edit_layout` → `discord.Message.edit()` (`squid/bot/utils/components.py:163-177`)
  — a plain bot-token `PATCH /channels/{channel_id}/messages/{message_id}` call. Ephemeral
  messages aren't retrievable/editable through that channel-message endpoint at all (they only
  exist within the interaction/webhook context), so Discord returns 404 Unknown Message, and the
  "Changes saved" confirmation never gets shown even though the edit itself (`self.builds.edit`
  at line 660-661) already committed successfully.
- This is the same distinction `edit_interaction_layout` (`squid/bot/utils/components.py:180-189`)
  exists to handle correctly, by calling `interaction.response.edit_message(...)` instead of
  `message.edit(...)` — but `submit()` can't use that helper here either, since (as above) this
  interaction's response was already consumed by the confirmation prompt.

**Suggested direction**

Don't spend the button interaction's initial response on the confirmation dialog. Defer the
component interaction as an update (`interaction.response.defer()`, which acks as
`DEFERRED_UPDATE_MESSAGE` for a component interaction and keeps `interaction.message` editable
later), send the confirmation view as a followup instead of via `response.send_message`, and
replace the final `edit_layout(interaction.message, ...)` with
`interaction.edit_original_response(view=success, ...)`, which edits the original (possibly
ephemeral) message through the interaction webhook instead of the bot-token channel endpoint.

## `api` service's host port in `compose.yml` is not changeable

Every other service that publishes a port lets it be overridden via an env var (`db` uses
`${SQUID_DB_PORT:-5432}`, `pgweb` uses `${SQUID_PGWEB_PORT:-8081}`), but the `api` service's port
mapping is hardcoded.

**Root cause**

- `api.ports` (`compose.yml:33-34`) is `- "8000:8000"` — a literal, not an interpolated variable —
  so there's no way to change the host port without editing `compose.yml` directly, which breaks
  down as soon as port 8000 is already taken on the host.

**Suggested direction**

Follow the same pattern as `db`/`pgweb`: introduce a `SQUID_API_PORT` env var (default `8000`) and
use it for the host side of the mapping, e.g. `"${SQUID_API_PORT:-8000}:8000"`.
