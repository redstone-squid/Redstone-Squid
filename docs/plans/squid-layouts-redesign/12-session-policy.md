# 12 — Session policy wrapper

## Problem

The only session policy `Mount` offers is `lock_to=user_id`. Real patterns this bot
already has, or wants, need more: "one settings panel per user per guild, opening a new
one replaces the old", "this poll wizard is shared by its participants", "child views
die with their parent". CascadeUI models these as framework concepts (instance limits,
instance scope, replace policies, participants, attachment lifecycle) and they are
genuinely useful for Discord applications.

## Design

A host-facing manager *around* `Mount`, not inside the layout package's planner or
runtime — session policy is operational, not presentational.

1. New module (host-side first: `squid/bot/utils/mount_registry.py`; promote into
   `squid_layouts.discord` later only if it stays generic):

       registry = MountRegistry()
       await registry.open(
           key=("settings", ctx.guild.id, ctx.author.id),
           policy=Replace(),          # or Reject(notice=...), or Coexist()
           open=lambda: send_component(ctx, SettingsPanel(...)),
       )

   - `Replace()`: finish the existing mount (disable controls) before opening the new
     one — the settings-panel pattern.
   - `Reject(notice)`: refuse with an ephemeral notice — the "you already have one
     open" pattern.
   - `Coexist()`: no constraint (default, current behavior).
2. The registry tracks live mounts by key, hooks `Mount.finish` for cleanup (needs a
   small `on_finish` callback on `Mount` — the only framework change), and offers
   `finish_children_of(key)` for parent/child teardown.
3. Participants/shared sessions: not in v1. `lock_to` covers single-owner; a
   `allowed_users: set[int]` generalization on `Mount` is a two-line change worth doing
   while touching dispatch, but participant *tracking* waits for a real consumer.
4. First consumers: settings panel (`Replace`), poll wizard (`Reject` — two concurrent
   wizards for one poll is a real footgun today).

## Verification

- Unit tests for the registry policies with stub mounts; `test_mount.py` for the
  `on_finish` hook.
- Settings/poll-wizard unit modules under `tests/unit/bot`, `--no-cov`.
