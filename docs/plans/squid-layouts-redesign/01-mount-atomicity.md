# 01 — Mount delivery atomicity

## Problem

`Mount.build_view()` (`packages/squid-layouts/src/squid_layouts/discord/mount.py:165-253`)
commits every side effect of a render before the Discord edit is attempted:

- `self._handlers` is replaced inside `draw()` (mount.py:175) with the candidate
  generation's bindings;
- `self.runtime.commit(tree)` (mount.py:244) publishes the tree and fires
  `on_mount`/`on_unmount` for components the user may never see;
- `self._generation` advances (mount.py:243) and `self._dirty` clears (mount.py:252);
- presentation cursors are mutated mid-draw (anchor/reset, mount.py:214-242).

The delivery happens afterwards, in `flush()` (mount.py:385-395), `refresh_now()`
(mount.py:416-421), and `finish_via()` (mount.py:397-407), none of which restore
anything when `deliver.apply*` raises.

Failure mode after a failed edit: the visible message still shows generation N while the
mount believes N+1. Every EXCLUSIVE control on the visible message then fails the
`generation not in {None, self._generation}` check in `dispatch` (mount.py:330) and is
silently deferred — the panel is bricked for clicks. Because `_dirty` was already
cleared, interaction-driven `flush` takes the "nothing to do" branch and can never
repair it. An out-of-band `Reactor` refresh *would* heal the message
(`refresh_now` rebuilds unconditionally), but a mount without a scheduler stays wedged.

CascadeUI treats this class of failure as a first-order design concern (deferred source
teardown, `_rollback_navigation()`); we independently confirmed the hole here from the
implementation.

## Design

`ComponentRuntime` already has the `render()`/`commit()` split — `build_view` collapses
it too early. Restructure `Mount` around an explicit candidate:

1. `_stage()` renders and draws, returning a `_Candidate` dataclass:
   `view`, `composition`, `tree`, `handlers: dict[str, ActionBinding]`, `generation`,
   and the assets tuple. The `wire` callback collects bindings into the candidate's dict,
   never into `self._handlers`.
2. Presentation cursors: snapshot `dict(self.presentation.cursors)` before drawing and
   restore it if delivery fails. **Superseded by [plan 06](06-pagination.md).** Planning
   no longer writes to the session at all; it returns `PlanResult.session_updates` and
   the mount applies them in `_commit`, so the snapshot is gone and the guarantee now
   also covers the strategy hysteresis this workaround could never restore.
3. `_commit(candidate)` runs only after `deliver.apply*` returns: swap handlers, advance
   `_generation`, `runtime.commit(tree)`, store assets, clear `_dirty`, `_swap_view`.
4. On delivery failure: discard the candidate, `candidate.view.stop()`, restore the
   cursor snapshot, leave `_dirty` True, re-raise (the existing error funnel reports it).
   Post-06 there is nothing to restore — dropping the candidate drops its writes.
5. Apply the same stage→deliver→commit shape to `flush`, `refresh_now`, `finish_via`,
   and `finish(disable=True)`. `finish` may still mark `_finished` before delivery — a
   failed disable-edit should not resurrect the mount — but must not commit the disabled
   tree's unmount hooks twice.
6. The initial send path (`build_view()` used by hosts before `bind()`) keeps working:
   expose `build_view()` as stage-without-commit plus an explicit `bind(message, view)`
   that performs the commit, since the host owns that delivery. Document that `bind` is
   the commit point. **Amended by [plan 15](15-send-ownership.md):** the host no longer
   owns the initial delivery — `Mount.send(target)` runs this same stage→deliver→commit
   sequence framework-side, and `bind` remains only as the manual commit point for
   deliveries the mount cannot perform (edit-in-place, the `to_components()` compat
   shim).

Keep the double-draw fingerprint dance inside `_stage()` unchanged; it is
presentation-only and covered by the cursor snapshot. (Also superseded by plan 06:
reconciliation moved into `plan()`, so the mount draws once.)

## Verification

- `packages/squid-layouts/tests/test_mount.py`: monkeypatch `deliver.handle_from` to return
  a handle whose `write` raises `discord.HTTPException` (this was `deliver.apply_interaction`
  until plan 07 replaced it); assert generation unchanged, old handlers still
  dispatch, `_dirty` still True, `on_mount` not fired for the candidate tree, cursors
  restored; then let a second flush succeed and assert full recovery.
- Existing mount/navigation/durability suites unchanged:
  `cd packages/squid-layouts && uv run pytest tests/test_mount.py tests/test_navigation.py tests/test_durability.py --no-cov`.
- `just typecheck`.
