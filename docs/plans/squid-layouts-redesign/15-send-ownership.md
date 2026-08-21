# 15 — `SendTarget`: the mount owns its initial send

## Problem

Plan 01 §6 chose host-owned initial delivery: `build_view()` stages, the host sends,
`bind()` commits. Thirteen call sites later the evidence is against that split:

- The ritual is uniform; the defects live in repeating it. Nothing enforces `bind` — a
  missed one silently never commits, and `diagnostics.py:91` binding only when
  `deliver_privately` returned a message reads as correct only to an author who knows
  the rule. `files=mount.attachment_files()` is coupled by convention: `verify.py:80`
  passes it, `settings.py:54` does not, and telling the bug from the no-assets case
  requires knowing the panel.
- Plan 07 collapsed the response-vs-followup branch for *edits* (`handle_from`), but the
  same branch is still hand-rolled for *sends* in `notifications.py:67-81` and
  `consent.py:332-343`, with `delivery.respond` as a third copy.
- The host already grew the missing abstraction itself: `send_component`
  (`squid/bot/ui.py:178`) mounts, builds, sends, binds — but covers only the ctx-reply
  path, so five other delivery shapes still hand-roll the ritual around it.
- There is no async seam before first delivery. Plan 09 needs one, and plan 12's sketch
  reaches for `send_component` as if it were framework surface.

## Design

Split the initial delivery the way plan 07 split edits: **the framework owns
sequencing, the host owns destination.**

> A `SendTarget` is a way to create the message: `async (view, files) -> Message |
> None`. Every discord.py kwarg lives on the target. `Mount.send(target)` owns
> everything around the call.

1. `delivery.SendTarget` protocol plus two adapters, mirroring `handle_for` /
   `handle_from`:
   - `reply_to(ctx, *, ephemeral=False)` — `ctx.send` with `no_mentions()`.
   - `respond_to(interaction, *, ephemeral=True, wait=False)` — response or followup by
     `is_done()`, subsuming `delivery.respond`. With `wait=False` it returns `None`
     instead of paying the `original_response()` round trip (`notifications.py:80` pays
     it today); the mount then has no standing handle until the first click mints one,
     which plan 07's pending semantics already define.

   Anything rarer stays a plain async function host-side: `deliver_privately`
   (`squid/bot/utils/visibility.py:46`) keeps its DM-or-ephemeral policy and its
   closed-DM UX, and is passed in as the target.
2. `await Mount.send(target) -> discord.Message | None`: stage → `target(view,
   attachment_files)` → `handle_for` on a returned message mints the standing handle →
   commit. `None` commits with no standing handle. A raising target discards the
   candidate exactly as plan 01 specifies — `_dirty` stays set, the mount is cleanly
   re-sendable — which is precisely what the closed-DM path needs before explaining
   itself to the channel.
3. `Mount.send` takes no discord kwargs, ever. `ephemeral`, `wait`, `silent`,
   `delete_after` are the adapter's business; the package does not chase discord.py's
   send surface. (The cost accepted here: targets see `LayoutView` and
   `list[discord.File]`. Two shared adapters plus the rare custom function is the whole
   exposure.)
4. `build_view()` / `bind()` are demoted to the escape hatch, not deleted:
   `account_view.py:499-502` (edit-in-place into an interaction's message) and
   `settings_view.py:760` (`to_components()` compat shim) legitimately want stage-only.
   Plan 01 §6's "bind is the commit point" documentation becomes: **send is the commit
   point; bind is the manual fallback** for a delivery the mount cannot perform.
5. Host migration — mechanical and net-negative in lines:
   - `send_component` (`ui.py:178`) becomes thin sugar over
     `mount.send(reply_to(ctx, ephemeral=...))` and stays the host's one-liner.
   - `settings.py:53`, `verify.py:79,380`, `search.py:218,296` → `send_component` or
     `reply_to` directly.
   - `notifications.py:66`, `consent_banner.py:83` → `respond_to` (the 14-line branch
     becomes one call).
   - `consent.py:332` `_send` → deleted; `_default_ephemeral` feeds the adapter choice
     between `reply_to` and `respond_to`.
   - `diagnostics.py:83` → `deliver_privately` as the target; the `message is not None`
     guard around `bind` disappears.
6. Sequencing: plans 01 and 07 are landed prerequisites. Lands **before 09**, which
   awaits `on_load` inside this seam. Plan 12's sketch becomes
   `mount.send(reply_to(ctx))`.

## Verification

- `packages/squid-layouts/tests/test_mount.py`: a successful send commits and mints a
  permanent handle; a target returning `None` commits with no standing handle and the
  next interaction's handle takes over (reuse `TestEditHandles` machinery); a raising
  target leaves generation unchanged and `_dirty` set, and a second `send` succeeds
  fully (the send-path mirror of plan 01's failed-flush test); `attachment_files` reach
  the target.
- Migrated host unit modules under `tests/unit/bot`, `--no-cov`.
- `just typecheck`.
