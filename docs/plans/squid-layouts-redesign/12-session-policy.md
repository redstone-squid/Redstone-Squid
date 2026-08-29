# 12 — Session policy

**Status: shipped.** Prerequisite [15](15-send-ownership.md) landed first: the registry takes
a `Destination` and owns the send, which is what makes replace-only-on-successful-delivery an
enforceable ordering rather than a convention at each call site.

## Problem

There is no dedup anywhere in the bot. `Mount.lock_to` answers "who may click this mount";
nothing answers "how many of these may exist".

- Two `/settings` runs give two live panels writing the same settings service.
- One `/poll` plus two Edit clicks gives **three** live 900 s wizards, each with a working
  Publish button: `PollModal.on_submit` mints a fresh mount every time, and the wizard's own
  Edit button re-opens that modal from inside a live wizard.
- The build-log consent banner is a public sticky message whose button has nothing guarding a
  double click.

Separately, a mount spawned from inside a handler leaks. `prompt_for_consent` and
`BuildEditComponent.send` each mint a second mount on a second message, and no reference
escapes; closing the parent leaves the child clickable until its own timer expires. Narrower
than it sounds — the parent's handler blocks on the child holding `_action_lock`, so the
parent can only die by timeout or by being replaced — but being replaced is exactly what this
plan adds.

## Framework API (`squid_layouts/discord/mount.py`)

1. **`Mount.finished: bool`** — a read-only view of `_finished`. Nothing outside could ask.
2. **`Mount.on_finish(hook)`**, with `FinishHook` beside `ErrorHook`. Fires once, after
   `_teardown`, in registration order, from `finish` and `finish_via` — and so from
   `handle_timeout`, which delegates to `finish`. Exceptions are logged and swallowed: a
   broken observer must not abort another's cleanup, or teardown itself. Multiple observers
   are expected (this registry now, [13](13-devtools.md)'s devtools next). Calling `finish`
   from inside a hook is a no-op via the existing guard, so the cascade below cannot loop.
   The mount is taken positionally, as `Destination` takes its view — a named parameter makes
   the protocol demand that every observer spell the argument `mount`.
3. **`dispatch` gains a `_finished` early-out**, mirroring `flush`'s. Today a handler on a
   finished mount still runs and mutates state; `view.stop()` hides it in production, and the
   existing test asserts only that no edit is issued. REPLACE makes a
   deliberately-superseded-yet-visible message routine, so it answers with a new
   `Chrome.session_ended` rather than a bare defer.
4. **`lock_to` accepts `int | AbstractSet[int]`**, normalized to `frozenset | None`. The nine
   bare-int call sites are unchanged, and no consumer needs the set form today; it was widened
   as a cheap generalization while dispatch was open. Participant *tracking* stays deferred.

Not added: a spawn-child helper on `ActionEvent`. `sl.discord.responder(event).mount` already
hands a handler its own mount, which is all `parent=` takes.

**Both terminal paths put teardown and hooks in a `finally`.** `finish_via` re-raises past its
own block, and `finish` anticipated `HTTPException` from its disable-edit and nothing else —
so on exactly the paths that most need an observer notified, the mount was left marked
finished but never unmounted and never announced. This was found by a test that hung for a
full 120 s prompt timeout rather than failing.

## Host API (`squid/bot/utils/mount_registry.py`)

Host-side because session policy is operational, not presentational. Deliberately distinct
from `squid_layouts`' durability `MountManager`/`ComponentRegistry` (zero production
consumers) and from `squid/bot/voting/sessions.py`. Owned as `self.mounts` on
`RedstoneSquid`, beside `background_tasks` and `account_ids`; reached from a handler as
`interaction.client.mounts`.

```python
@dataclass(frozen=True, slots=True)
class SessionKey:
    name: str
    user_id: int
    scope: int | None = None      # guild, build, channel -- None for user-global

class WhenOpen(Enum):
    REPLACE = auto()              # finish the incumbent, open the new one
    REJECT = auto()               # leave the incumbent, deliver nothing

class MountRegistry:
    async def open(self, mount, destination, *, key=None,
                   policy=WhenOpen.REPLACE, parent=None) -> Mount | None: ...
    def get(self, key) -> Mount | None: ...
    async def close(self, key, *, disable=True) -> None: ...
    async def close_all(self, *, disable=True) -> None: ...
    def active(self) -> Iterator[tuple[SessionKey | None, Mount]]: ...
```

Deltas from the original sketch, each with a reason:

- **`Coexist` is dropped.** With `key` optional it has no job: `key=None` registers for parent
  cascade with no instance limit, and "no key, no parent" is simply not using the registry.
  `policy` applies only when `key` is given.
- **`Reject` carries no notice.** `open` returns `None` and the registry never touches
  Discord, so it stays unit-testable against stub mounts. Each call site writes its own
  wording, which it needs anyway; `get(key)` is there to reference the incumbent.
- **`open` takes the mount and a `Destination`**, not an `open=` closure — that is what makes
  the ordering below enforceable. Cost: a REJECTed open has already paid the panel's `load()`.
  That is by definition the rare path, and `get(key)` is the cheap pre-check.
- **`finish_children_of` is not public.** Cascade is automatic on `parent=`; making the host
  remember to call it is the footgun this exists to remove.

## Semantics and hazards

Each is a real failure mode found while designing or building.

**Replace ordering.** Send the newcomer *first*; finish the incumbent only on success.
Finishing first loses both panels on a Discord hiccup, and plans 01/15 already leave a failed
mount cleanly re-sendable.

**Detecting "nothing was sent" is not `send() is None`.** `Mount.send` returns `None` for two
different outcomes: delivered without a handle (an unwaited interaction response, where the
first click mints one) *and* abandoned without delivering, because it swallows
`DeliveryAbandoned` on the way out. Reading the wrong one finishes the incumbent on behalf of
a message that was never sent, leaving the user with zero panels and no explanation — and
`ui.destination` really does abandon, on the closed-DM path under `Private`. The registry
wraps the destination it was handed and records delivery after that call returns, so the
abandon propagates out before the flag flips. No framework change needed.

**Identity-checked cleanup.** REPLACE registers the newcomer under the key *before* awaiting
the incumbent's `finish()`, so the incumbent's own hook fires against a key that now holds
someone else. Removal is `if entry.mount is mount`, never `del by key`.

**Racing opens.** Two `/settings` invocations in flight both see no incumbent. A per-key
`asyncio.Lock` is held across the whole `open`, including the send and the incumbent's finish.
Prior art is `sticky_message._lock_for`, plus a waiter count so the lock is dropped when the
key goes idle — `sticky_message` keeps one per channel forever, which is bounded, while
session keys are per user and are not. The test for this fails with two survivors when the
lock is removed.

**The finish hook must never take the key lock.** It runs inside `incumbent.finish()`, which
runs inside `open()` already holding it. Cleanup is lock-free dict mutation plus an awaited
child cascade.

**A finish hook is not inside a transaction.** It runs after teardown, outside the transaction
a handler gets, so a hook that writes reactive state raises — and the hook runner swallows it,
producing a silent no-op. `ConsentPrompt.abandon` sets only its `anyio.Event` for this reason.

**Cascade.** `parent=` attaches a hook to the parent (registered or not — a panel not yet
migrated is still a good parent) and records the child. Parent finish → `await child.finish()`
each, depth-first; grandchildren follow by induction; `_finished` guards terminate it. If
`parent.finished` is already true when the hook is attached, the child is finished at once,
since a hook registered then would never fire. One unreachable child does not strand its
siblings.

**No sweeper task, and none is needed.** `on_finish` covers close, timeout and error — every
path funnels through `finish`, including the ones that raise. A mount whose message was
deleted cannot be clicked, so its idle timer expires and fires the hook. Nothing else strands
an entry, so `BackgroundTaskSupervisor` gains no job and `CRITICAL_BOT_JOBS` is untouched. The
registry keeps a defensive discard of a finished-but-still-registered entry anyway, because a
stale one under REJECT would lock a user out for the life of the process.

**Shutdown.** `await self.mounts.close_all()` in `RedstoneSquid.close`, before
`background_tasks.close()`, wrapped in `anyio.move_on_after(3.0)` so a slow gateway cannot
stall shutdown, and tolerant per mount so one unreachable message does not leave the rest
live.

## Consumers

| Site | Key | Policy / parent |
|---|---|---|
| `settings.py` | `("settings", author, guild)` | REPLACE |
| `poll_wizard.py` (`PollModal.on_submit`) | `("poll-wizard", user, guild)` | REPLACE |
| `views.py` (`BuildEditComponent.send`) | `("build-edit", user, build.id)` | REPLACE + `parent=` |
| `consent.py` (`prompt_for_consent`) | `("consent", user)` | REJECT + `parent=` |
| `consent_banner.py` (route) | `("consent", user)` | REJECT, no parent |

The poll wizard is **REPLACE, not the sketch's REJECT**: Edit re-submits the modal that mints
the wizard, so rejecting a second wizard would reject the edit.

The build editor's `open` lives inside `BuildEditComponent.send`, which all five of its call
sites already funnel through, so they are covered without touching any of them. Only
`build_info._edit` passes a parent, being the one that opens the editor from inside another
mount's handler.

**Consent is the awkward one, and it is where the design work was.** It takes REJECT because
it is *awaited*, and because the two prompts a user can trigger are rarely about the same
thing — replacing a `/verify` prompt with an `/account` one abandons the verification they
started. Two consequences the sketch did not anticipate:

- *REJECT needs somewhere to report itself.* `prompt_for_consent` returned
  `AccountConsent | None`, and **all five** of its callers (`ensure_consented_account`,
  `verify.py`, and three in `account_view.py`) render `None` as "cancelled, nothing was
  stored". Of a refused second prompt that is a lie: nothing was cancelled, because nothing
  was asked. Hence `NOT_ASKED` as a third outcome, with every caller taught to stay silent on
  it. It covers the abandoned delivery too, whose contract already says the user has been told.
- *Cascade has to end the wait, not just the buttons.* `wait()` blocks on an event only the
  Agree and Cancel handlers set, so a prompt finished by a closing parent left its caller —
  and the handler holding that caller's action lock — blocked for the remaining 120 s. That is
  the leak this plan set out to close, so the prompt ends its own wait from `on_finish`.

`consent_banner.py` shares the consent key, so the banner button and the account panel contend
for one prompt rather than each opening their own.

The two throwaway inspection mounts (`settings_view.to_components`, `search_view._compat_view`)
are deliberately not registered: they never send or bind.

**Wave 2, not done here:** `/account`, `/notifications`, `/account claims` and `/error show`
are all `lock_to`+300 s panels wanting REPLACE. Mechanical, and left as follow-up.

## Verification

```
uv run pytest packages/squid-layouts/tests/test_mount.py tests/unit/bot/test_mount_registry.py --no-cov
uv run pytest tests/unit/bot/test_settings_panel.py tests/unit/bot/test_poll_wizard_panel.py \
              tests/unit/bot/test_consent_gate.py tests/unit/bot/test_account_panel.py \
              tests/unit/bot/submission --no-cov
uv run pytest tests/architecture/test_boundaries.py --no-cov   # squid.bot* may import squid_layouts
just typecheck
```

Manual, via the `run` skill: `/settings` twice — the first panel's controls go dead and the
second is live; `/poll`, Edit, submit — one wizard survives with one Publish; open `/account`,
trigger the consent branch, close the parent panel — the consent prompt disables *and* its
caller returns immediately, rather than either waiting out 120 s.
