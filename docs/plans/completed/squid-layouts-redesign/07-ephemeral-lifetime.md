# 07 — `EditHandle`: how a mount writes to its message

**Landed.** Kept for the reasoning, not as a task list.

## Problem

A mount edits its message two ways, and only one of them was modelled.

| Channel | Used by | Ephemeral lifetime |
|---|---|---|
| The click's own credentials | `flush`, `finish_via` | Fresh every click — effectively unbounded |
| The bound `self.message` | `refresh_now`, `finish` | Dies ~15 min after the send |

`Mount` held a `discord.Message` that was really standing in for a credential, so every
caller had to know when the stand-in was lying — and none of them did.

**Corrections to the first draft.** The original plan proposed detecting ephemerality at
`bind()`, clamping the view timeout to `TOKEN_LIFETIME - 60s`, treating an expired-token
HTTP failure as terminal, and documenting that long sessions should not be ephemeral. Read
against the code, four of its five claims did not hold.

1. *"clamping the timeout makes the disable-edit land while the token is valid"* — it does
   not. `View.timeout` is an **idle** timer that discord.py resets on every interaction
   (`discord/ui/view.py:595`), and the mount builds a fresh `MountedView` per render,
   restarting it again. A panel clicked every 60s lives for hours and its timeout-driven
   disable-edit still fires long after the credentials died. The clamp bounds nothing while
   force-closing sessions a user is actively using.
2. *"`Mount`'s 900s default races token expiry"* — only for the message channel. Interactive
   use of an ephemeral mount never expires, because each click carries its own credentials.
3. *"treat the expired-token failure as terminal — mark finished"* — wrong direction. Such a
   mount is still fully usable through the next click, so finishing it kills a panel that
   works.
4. *"detect 401 / `50027 Invalid Webhook Token`"* — reading status codes in `mount.py` was
   the leak the redesign exists to close, and it misses `10015`/`10062`.

The one claim that held: nothing enforced any relationship, and `refresh_now` raised into
the Reactor's error log. In practice that was unobservable — `Reactor` has no consumers in
`squid/` and `finish` already swallowed `HTTPException` — so the original plan would have
shipped a UX regression to fix nothing.

**What was actually broken.** `apply_interaction` fell back to `edit_original_response()`
once the response was consumed. For a component interaction that only still means *the
message the component is on* if the response was update-shaped; `event.notice()` answers
with `send_message`, which moves it onto the notice. `poll_wizard`'s Cancel button
(`_cancel`: notice, then `event.finish()`, with no prior defer) therefore replaced the
"Poll cancelled." reply with a disabled wizard and left the real wizard clickable.

An earlier draft blamed `claims_view._decide` for this. It does not hit it: `_decide` calls
`interaction.response.defer()` before deciding, which is update-shaped, so its
`event.notice` goes out as a followup and leaves the original response on the panel. Every
other `event.notice` call site either defers first or writes no reactive state, so the
wizard's Cancel was the only live reproducer.

## Design

> A mount holds an **`EditHandle`**: a way to write to one already-sent message, and how
> long it is good for. Handles go stale. Every interaction carries a fresh one.

An expired ephemeral token and a response spent on another message are one condition seen
twice — *these credentials no longer address this message* — so `delivery.py` names it once
and everything else asks.

- `StaleHandleError` replaces reading HTTP codes. `EditHandle.permanent` says whether the
  credentials are the bot's own; `expires_at` is the deadline Discord stated, when it stated
  one. **No token-lifetime constant appears anywhere** — deadlines come from
  `Interaction.expires_at`, and where Discord states none we say so rather than guessing.
- `handle_for(message)` writes with `message.edit`. `handle_from(interaction)` writes with
  `response.edit_message` while the response is unspent and `followup.edit_message` after,
  and returns `None` when the response has been spent on something that is not this message
  — decided by `InteractionResponse.type`, so it is exact rather than heuristic.
- `Mount._deliver` tries the click's handle, then the standing one, dropping a handle that
  reports itself stale. `Mount._renew` keeps the longest-lived credentials the mount has
  seen: the bot's own are never traded away, and anything else is replaced on every click.
  An ephemeral panel in use therefore stays writable well past its send — the outcome the
  Cascade-style handoff buys with an armed refresh control, for free.
- `Mount.message` is gone. Its only external readers passed it straight back to `bind`,
  which already documents keeping what it holds. `Mount.handle` and `Mount.pending` replace
  it as facts a host can use.

`mount.py` now says `EditHandle`, `StaleHandleError`, `expired`, `write`, `permanent`. It
says nothing about webhooks, tokens, ephemerality, `@original`, HTTP codes, or minutes.

## Degrading is the existing contract, said precisely

When no handle is live the render stays pending: `refresh_now` rolls the candidate back —
which already leaves the mount dirty — logs at debug, and returns. The next interaction
flushes it.

This needed no new promise. `Reactor` already coalesces, so `refresh()` has always meant
*show the newest state at the next opportunity*, not *now*. A stale handle widens that
window from "next loop tick" to "next click". Real HTTP errors still raise into the
Reactor's log; only "nothing to write through" is silent, and it is visible in
`Mount.pending`.

## What is still out of reach

An ephemeral message nobody has clicked for over 15 minutes cannot be edited in the
background at all. That is Discord's rule, not a policy of ours, and `Mount.pending` says
when we are sitting on a render because of it.

## Verification

- `packages/squid-layouts/tests/test_mount.py::TestEditHandles` — handle selection, renewal,
  ephemeral refresh through the latest interaction, and the deferred-render path.
- `tests/unit/bot/test_poll_wizard_panel.py` — the Cancel-button reproducer. Both it and the
  notice test in `TestEditHandles` were confirmed to fail against the old behaviour.
- `uv run pytest packages/squid-layouts/tests/ tests/unit/bot/ tests/unit/voting/ --no-cov`
  — 1021 passed. `just typecheck` at 0 errors, unchanged from before.
