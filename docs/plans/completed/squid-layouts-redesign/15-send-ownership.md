# 15 — `Destination`: the mount owns its initial send

## Problem

Plan 01 §6 chose host-owned initial delivery: `build_view()` stages, the host sends,
`bind()` commits. Thirteen call sites later the evidence is against that split:

- The ritual is uniform; the defects live in repeating it. Nothing enforces `bind` — a
  missed one silently never commits, and `diagnostics.py` binding only when
  `deliver_privately` returned a message reads as correct only to an author who knows
  the rule. `files=mount.attachment_files()` is coupled by convention: `verify.py`
  passed it, `settings.py` did not, and telling the bug from the no-assets case requires
  knowing the panel.
- Plan 07 collapsed the response-vs-followup branch for *edits* (`handle_from`), but the
  same branch was still hand-rolled for *sends* in `notifications.py` and `consent.py`,
  with `delivery.respond` as a third copy.
- The host already grew the missing abstraction itself: `send_component`
  (`squid/bot/ui.py`) mounted, built, sent and bound — but covered only the ctx-reply
  path, so five other delivery shapes hand-rolled the ritual around it.
- There is no async seam before first delivery. Plan 09 needs one, and plan 12's sketch
  reaches for `send_component` as if it were framework surface.

## Design

Split the initial delivery the way plan 07 split edits: **the framework owns
sequencing, the host owns destination.**

> A `Destination` is a way to create the message: `async (view, files) ->
> DeliveryReceipt`. Every discord.py kwarg lives on the destination.
> `Mount.send(destination)` owns everything around the call.

Plan 23 amended the original `Message | None` return after proving that a message object
does not reveal whether its edit method uses the bot token or an expiring interaction token.
The receipt carries `message: Message | None` and `handle: EditHandle | None` separately.

Named `Destination` rather than `SendTarget` because `sl.discord.Target` — the render
target — already lives in this subpackage.

1. **`delivery.Destination` protocol plus two adapters**, mirroring `handle_for` /
   `handle_from`. The receipt says what the mount gets to keep, not whether delivery
   worked:
   - `message` records observable location and is returned to the caller;
   - `handle` records the exact edit authority the operation created, independently of
     whether a message object was requested or returned;
   - raise `DeliveryAbandoned` — nothing was delivered, deliberately, and the user
     already knows;
   - raise anything else — the delivery failed.

   `DeliveryAbandoned` still distinguishes a receipt with no message from the closed-DM
   path, which delivered nothing and must roll back.

   The adapters: `reply_to(ctx, *, ephemeral=False)` — `ctx.send` with `no_mentions()`;
   `respond_to(interaction, *, ephemeral=True, wait=False)` — response or followup by
   `is_done()`, subsuming and replacing `delivery.respond`. A fresh `wait=False` skips
   the `original_response()` round trip while retaining `@original` edit authority.
   Both carry the one `# pyrefly: ignore[no-matching-overload]` four host sites used to
   repeat.

2. **`await Mount.send(destination) -> discord.Message | None`**: supersede any
   stage-only candidate → stage → `destination(view, attachment_files)` → retain the
   receipt's handle and message address → commit. A raising destination discards the
   candidate exactly as plan 01 specifies — `_dirty` stays set, the mount is cleanly
   re-sendable — so a second `send` is a clean retry. `send` on a finished mount is a
   no-op; a repeat `send` is legal and replaces the handle. This is the seam plan 09
   awaits `on_load` inside.

3. **`Mount.send` takes no discord kwargs, ever.** `ephemeral`, `wait`, `silent`,
   `delete_after` are the adapter's business; the package does not chase discord.py's
   send surface. (The cost accepted here: destinations see `LayoutView` and
   `list[discord.File]`.)

4. **Host destination vocabulary.** The host already had the vocabulary this plan was
   about to reinvent as a bool: `squid/bot/ui.py`'s `Visibility` — `"public" |
   "personal" | Private(reason)` — and `reply()`, which had no production callers.
   `ui.destination(ctx, *, visibility, locale, files)` builds a `Destination` in those
   terms, so the audience rule stays in one place and
   `tests/architecture/test_ephemerality.py` keeps seeing `personal(ctx)` rather than a
   literal `True`. `send_component` and `PagedList.send` take `visibility=` in place of
   `ephemeral=`.

   `deliver_privately` and `ui.reply` take `files: Sequence[discord.File]` rather than a
   single `file`, because a destination has to merge the host's own attachment with the
   mount's rendered assets and `Messageable.send` accepts one or the other, not both.
   Their closed-DM return contract stays `None` for the two plain-layout callers;
   `ui.destination` translates it to `DeliveryAbandoned` at the mount boundary.

5. **`bind()` is deleted, not demoted.** The four surviving `bind(None, rendered)` sites
   were not stage-only cases: each was `build_view()` →
   `edit_interaction_layout(interaction, rendered)` → `bind(None, rendered)`, which is
   precisely `Mount.flush(interaction)` — stage, deliver through `handle_from`, commit —
   plus the standing-handle fallback the hand-rolled version lacked. All four became
   `await self._mount.flush(interaction)`, leaving `bind` with no callers.

   That fallback needed hardening first. `_deliver` returned a `bool`, which lost the one
   fact `flush` needs: *which* handle wrote. Only the interaction's own handle answers the
   click as a side effect of editing, so a delivery that fell back to the standing handle
   left the interaction unacknowledged and Discord reported a failure three seconds
   later. `_deliver` now returns the handle it used; `flush` and `finish_via`
   acknowledge unless it was the interaction's.

   `build_view()` stays as the stage-only escape hatch for the two compat shims that
   never bound anything — `settings_view.to_components()` and
   `search_view.build_view(disabled=…)`.

   Plan 01 §6's "bind is the commit point" becomes: **`send` is the commit point for an
   initial delivery, `flush` for an interaction-driven one.**

6. **Sequencing.** Plans 01 and 07 are landed prerequisites. Lands **before 09**, which
   awaits `on_load` inside this seam. Plan 12's sketch becomes
   `mount.send(destination(ctx))`.

## Accepted behaviour changes

- `poll_wizard`'s confirmation keeps `wait=True`. The plan first proposed `wait=False`
  to save a round trip, at the cost of a zero-click confirmation no longer disabling its
  own controls on expiry. The old code already paid for `original_response()`, so
  dropping it would have been a silent regression bought with nothing.
- A modal opened from a command, not from a component, carries no `interaction.message`,
  so a `flush` from it delivers through the standing handle. Before the `_deliver`
  change that click went unanswered. All four migrated modals are component-launched
  today, so this is hardening rather than a fix for a live bug.

## Verification

- `packages/squid-layouts/tests/test_mount.py`, `TestSend`: a successful send commits and
  retains the receipt's handle without reconstructing it; a handle-less receipt commits
  with no standing handle and the next interaction's `_renew` takes over; a destination raising
  `DeliveryAbandoned` leaves the generation unchanged, `_dirty` set and no handle;
  anything else rolls back *and propagates*, and a second `send` succeeds fully (the
  send-path mirror of plan 01's failed-flush test); `attachment_files` reach the
  destination; `send` supersedes an outstanding `build_view()` candidate.
- `TestEditHandles`: a flush whose delivery goes through the standing handle acknowledges
  the interaction.
- `testing.delivered_to(message)` gives host tests a mount delivered to a fake message;
  `testing.commit_render` no longer needs `bind`.
- Migrated host unit modules under `tests/unit/bot`, plus
  `tests/architecture/test_ephemerality.py`.
- `just typecheck` — 0 errors.

## Status

Shipped.
