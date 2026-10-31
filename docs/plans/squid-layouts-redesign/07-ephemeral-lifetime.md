# 07 — Ephemeral mount lifetime policy

## Problem

Ephemeral messages are editable only through the originating interaction's webhook
token, which Discord expires after ~15 minutes. The bot mounts ephemeral components
today: `squid/bot/settings.py` sends the settings panel with `ephemeral=personal(ctx)`,
and `squid/bot/ui.py`'s `send_component` exposes `ephemeral=`.

- `Mount`'s own default timeout is 900s (`discord/mount.py:135`) — exactly the token
  lifetime, so timeout-driven "disable controls" edits race token expiry.
- `create_mount` (`squid/bot/ui.py`) uses 180s, which usually stays under the window,
  but nothing enforces the relationship.
- Any `Reactor`/`refresh_now` edit of an ephemeral mount after expiry fails with an
  HTTP error and, pre-plan-01, also desynced the mount state.

CascadeUI handles this with a scheduled handoff (install a refresh control before the
deadline, reconstruct the session from the fresh interaction). We take the cheap version
first.

## Design

1. **Detect**: on `bind()`, record `self._ephemeral = message.flags.ephemeral` and the
   bind time.
2. **Cap**: for ephemeral mounts, clamp the effective view timeout to
   `min(timeout, TOKEN_LIFETIME - margin)` (margin ~60s) so the disable-controls edit
   always lands while the token is valid. Emit a debug log when clamping.
3. **Degrade gracefully**: in `refresh_now`/`finish`, treat the expired-token HTTP
   failure (401/`50027 Invalid Webhook Token`) on an ephemeral mount as terminal — mark
   finished, stop the view, log at debug, do not raise into the Reactor's error log.
4. **Host guidance**: document in the package README that long-lived interactive
   sessions should not be ephemeral; `squid/bot/ui.py.send_component` gains a docstring
   note.

Deferred (see `90-deferred.md`): the full Cascade-style handoff — a refresh control
armed near expiry that rebuilds the session from the fresh interaction. Only worth it if
a real ephemeral view needs >14 minutes of life.

## Verification

- `test_mount.py`: ephemeral bind clamps timeout; expired-token failure on refresh
  finishes the mount without raising; non-ephemeral mounts unaffected.
- `tests/unit/bot` settings-panel module for the host-side path, `--no-cov`.
- `just typecheck`.
