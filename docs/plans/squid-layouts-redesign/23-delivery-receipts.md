# 23 — Delivery receipts: authority comes from the operation

## Problem

Plan 15's `Destination` returns `discord.Message | None`, and the mount reconstructs
editing authority from the object: `handle_for(message)` decides `permanent = not
_is_ephemeral(message)` (`discord/delivery.py`). But non-ephemeral does not imply
permanent credentials. Verified against discord.py 2.7.1 (external audit 2026-08-21):

- `respond_to(wait=True)` on the fresh-response path returns
  `interaction.original_response()` — an `InteractionMessage` whose `.edit` calls
  `edit_original_response`, the interaction token, dead in 15 minutes. The followup path
  returns a `WebhookMessage`: same token, same lifetime.
- A *public* interaction response therefore yields a `_MessageHandle` with
  `permanent=True` on dying credentials — and `Mount._renew` short-circuits on
  `permanent` (`discord/mount.py:662`), so the mount refuses the fresher credentials it
  is offered on every click.

Blast radius, measured rather than assumed: clicks survive, because `_deliver` tries the
interaction's `through` handle before the standing one. The casualty is **out-of-band
refresh** (`Reactor.schedule`, `Mount.refresh`): after 15 minutes those writes raise
`StaleHandleError`, the handle is dropped, and renders sit in `pending` until the next
click. One live consumer is on the bad path: `squid/bot/submission/search.py:290`, a
public panel sent `ephemeral=False, wait=True`.

Secondary limitation, same root: `respond_to(wait=False)` returns `None`, so the mount
has no standing handle until the first click — even though the interaction token can
edit `@original` without ever fetching the message.

## Design

> A destination returns a receipt: the message it can show, and the authority it
> actually created. Nothing downstream guesses.

1. **`DeliveryReceipt(message: Message | None, handle: EditHandle | None)`**, returned
   by `Destination`. `Mount.send` keeps the handle; the message feeds address and
   snapshot only. `DeliveryAbandoned` semantics are unchanged. Amends plan 15 §1.
2. **Adapters say what they know:**
   - `reply_to` → message + permanent `_MessageHandle` (bot token; today's truth).
   - `respond_to`, fresh path, `wait=False` → no message, plus a new
     `_OriginalHandle(interaction)` writing via `edit_original_response` — no fetch,
     `expires_at` known. Closes the no-standing-handle gap for free.
   - fresh path, `wait=True` → message + the same `_OriginalHandle`. The fetched
     `InteractionMessage` is evidence of *where*, not of permanence.
   - followup, `wait=True` → message + a webhook-token handle. `wait=False` → neither:
     an unwaited followup has no id and is not `@original`; the receipt makes that
     honest instead of implicit.
3. **Handles are named by the endpoint they perform** — bot-token channel edit,
   webhook `@original`, webhook message edit — so the protocol operation is the
   contract and discord.py stays transport (see the architecture doc's
   library-binding section).
4. **The heuristic permanence read is deleted.** `handle_for(message)` survives only
   where the message really was sent with bot credentials; `handle_from(interaction)`
   is unchanged.
5. **Optional upgrade path, verify at implementation time**: whether a public original
   response can be re-fetched through the channel as a plain `Message` and edited with
   the bot token. If so, a mount holding an expiring handle on a public message may
   trade up in the background. Not load-bearing; the receipt is correct without it.

## Verification

- Public `respond_to(wait=True)`: the committed handle is interaction-bound; a click
  renews it (`permanent` no longer lies); after simulated expiry, out-of-band refresh
  raises, drops the handle, and the next click renews and delivers the pending render.
- `respond_to(wait=False)`: a standing handle exists at commit; out-of-band refresh
  works inside the 15-minute window with zero fetch round trips.
- Followup `wait=False`: no handle; the first click mints one (existing pending
  semantics).
- `reply_to`: unchanged — permanent, background edits indefinitely.
- Host: `search.py:290` gains background-refresh coverage across the expiry boundary.

## Status

Agreed 2026-08-21 (external audit, verified in-repo and against discord.py 2.7.1); not
started.
