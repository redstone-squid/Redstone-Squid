# 39 — Ephemeral edit-authority handoff

## Problem

Plan 07 made expiring Discord credentials honest: a mount keeps an `EditHandle`, renews it
from accepted component interactions, and defers unattended renders when no live handle can
write. Plan 26 then made that pause visible before expiry by appending:

> Live updates paused — press any control to resume.

That is mechanically sufficient, but it is not the best user lifecycle:

- every existing control is also an application action, so resuming can page, submit, delete,
  or otherwise mutate state when the user only meant to keep the session alive;
- the warning is a general `Mount.status` value rather than a protected lifecycle state, so a
  host can overwrite it and a later render has no distinguished renewal control to preserve;
- a full document has to fit one more status node at the last writable moment;
- the expiry sweep only watches mounts followed through `Reactor.follow`, even though a
  long-lived ephemeral mount can need renewal without following a topic.

[CascadeUI addresses the same token cliff](https://github.com/HollowTheSilver/CascadeUI/blob/aee85ae319fcb703d3c6881d0bbb1ce3b6e27dcb/cascadeui/views/_interaction.py#L385-L604)
by replacing the old view with a single “Continue Session” control and using its click to send a
newly reconstructed ephemeral view. The useful idea is the explicit, non-mutating handoff.
Reconstructing the view and spawning another message are consequences of Cascade's view-level
state model, not requirements Squid should copy. Squid's mount already outlives every rendered
`MountedView`, and `handle_from(interaction)` can mint fresh authority for the message that was
clicked.

## Decision

> Arm a framework-owned renewal screen before an ephemeral handle expires. Its click restores
> the same mount on the same message through the fresh interaction handle.

No component class opts in and no component render can remove the control. The mount, its
runtime, presentation state, history, session identity, parent/child relationships, and message
address all remain the same. Only the replaceable `EditHandle` changes.

This is a mount lifecycle policy, not a destination policy. A `Destination` runs once and owns
send-time transport choices. Arming, suppressing later reactive renders, admitting the renewal
click, and returning to the application tree all happen after delivery and belong to `Mount`.
The delivery receipt still supplies the facts that activate the policy: whether the message is
ephemeral and the exact handle/deadline the send created.

## A. Public policy

```python
mount = sl.discord.Mount(
    component,
    access=...,
    scheduler=reactor,
    expiry=sl.discord.RenewEphemeral(
        warning=90,
        label="Continue Session",
    ),
)
```

`ExpiryPolicy` initially has two frozen values:

- `PauseUpdates(warning=60)` preserves plan 26's current status-only behaviour and is the
  default;
- `RenewEphemeral(warning=90, label=None)` enables the handoff. `label=None` reads the localized
  `Chrome.continue_session`; an explicit `TextLike` overrides it.

`warning` is a finite positive number of seconds. `Chrome.session_expiring` supplies the small
status text on the renewal screen. Both new chrome values go through `localize_chrome` like the
existing `updates_paused` string.

The policy is deliberately on `Mount`, beside access and timeout, rather than on the component
class. `expiry=None` disables pre-expiry UI entirely. A mount without a running `Reactor` still
works normally but cannot promise timed arming; constructing `RenewEphemeral` without a
Reactor-backed scheduler therefore fails fast instead of silently declining the feature.

The trigger remains fact-based: a non-permanent handle with a known `expires_at`. The renewal
variant additionally requires a receipt known to be ephemeral. A public interaction response
with temporary authority keeps the default paused-status behaviour; this plan does not infer
ephemerality from the handle class.

If the mount's remaining idle timeout ends no later than the handle, the sweep does nothing.
There is no reason to interrupt a session whose own declared lifetime ends first. `timeout=None`
is unbounded and always eligible.

## B. Reactor owns timing

The existing sweep is the right owner. No per-mount task and no `asyncio.create_task` are added.

1. `Reactor` maintains a weak watch set separate from topic subscriptions. A mount using the
   reactor as its scheduler registers after its first successful delivery and unregisters on
   finish; collection remains a backstop.
2. `follow()` continues to manage topic subscriptions, but following is no longer the accidental
   prerequisite for expiry observation.
3. The per-mount policy supplies its warning margin, replacing the reactor-wide
   `expiry_margin`. `sweep_interval` remains an engine tuning knob.
4. The sweep captures the current handle identity and asks the mount to arm for that handle.
   Arming re-checks identity, deadline, delivery visibility, timeout, and finished state under
   the render lock. A click that already renewed the mount makes the stale sweep a no-op.
5. One arm attempt is recorded per handle. A new interaction handle re-arms observation; moving
   its deadline back outside the margin clears the record exactly as today.

Arming is queued through the reactor rather than awaited in the sweep, so one slow Discord edit
cannot hold every watched mount past its deadline. The existing bounded workers and per-mount
coalescing remain the only refresh concurrency mechanism.

## C. The renewal screen is a lifecycle generation

`RenewEphemeral` does not append a button to the component document. At the warning boundary the
mount renders a small framework document containing `session_expiring` and one renewal action,
then edits it through the still-live handle while leaving attachments unchanged.

This is a distinct lifecycle generation:

- Discord, `Mount.generation`, the live handler table, `MountedView`, plan, and diagnostics advance
  to what is actually visible;
- the component runtime's last committed tree, presentation session, and attachment inventory do
  not advance or roll back;
- invalidations and topic refreshes while armed only leave the application render dirty. They do
  not redraw over the renewal screen or run component/resource loading;
- the compact lifecycle document has a fixed, measured cost and cannot fail merely because the
  previous application document had already consumed every component slot.

This is Squid's equivalent of Cascade's armed-tree freeze, but the model remains reactive: state
can continue changing behind the screen, and renewal renders the latest state rather than a
snapshot taken when the button appeared.

The internal renewal binding is distinguishable from application `ActionBinding`s. It is planned
and measured through the normal semantic pipeline, but dispatches through a dedicated mount path;
it does not enter application middleware, action history, cooldowns, or a component transaction.
It does use the mount's access policy. A denied user receives the ordinary private rejection and
cannot renew authority for the owner.

## D. In-place renewal

The renewal click follows this order:

1. learn the message address if the original unwaited response never exposed it;
2. run the mount access check and reject without changing lifecycle state when denied;
3. obtain `handle_from(interaction)` before any response can move the interaction away from the
   source message;
4. under the render lock, confirm the mount is still armed and adopt that fresh handle;
5. stage the latest application tree, including any state/resources that became dirty while
   armed, and edit the same message through the click handle;
6. commit the application generation, clear the armed state, and let the reactor observe the new
   deadline.

Editing through the click handle acknowledges the interaction. There is no successor message to
send and no old message to delete. The atomicity boundary is therefore the existing
stage → deliver → commit sequence: if staging or delivery fails, the renewal screen remains the
visible generation and the fresh handle remains available for a retry. A stale standing handle
does not matter because the click itself supplies the write authority.

Any ordinary application control that wins a race just before the renewal screen lands keeps the
existing plan-07 behaviour: if its interaction still addresses the message, successful dispatch
renews the handle and makes the pending arm attempt stale. Once the renewal generation is visible,
its single control is the only admitted binding. Repeated or stale renewal clicks are acknowledged
idempotently.

## E. Failure and lifecycle semantics

- **Arming edit fails with `StaleHandleError`:** keep the application generation logically live,
  leave it dirty, and log at debug. The outcome is today's click-to-resume degradation, not a
  finished mount.
- **Arming fails for another Discord/plan error:** roll back the lifecycle candidate and surface
  the error through the reactor's existing logging. Do not freeze locally unless Discord accepted
  the screen.
- **Renewal render fails:** route through the mount error hook while retaining the armed screen and
  fresh handle. A second click can retry.
- **Mount timeout while armed:** finish normally. A late button interaction gets
  `Chrome.session_ended`; renewal never extends the mount's host-chosen idle lifetime by itself.
- **Finish while armed:** terminal teardown owns the mount exactly once. Best-effort disabling uses
  whichever handle is still live and never reconstructs the hidden application tree solely for
  cleanup.
- **Permanent/new non-ephemeral authority:** disarm renewal immediately; a bot-token handle has no
  deadline to hand off.

`MountSnapshot` gains an immutable lifecycle state (`ACTIVE` or `RENEWAL_ARMED`) and the handle's
known expiry distance. DevTools can therefore distinguish “application render pending behind the
renewal screen” from an ordinary dirty mount.

## Non-goals

- **Spawning a replacement ephemeral message.** Squid can renew the clicked message directly;
  a second message would create an atomic swap problem and a best-effort orphan cleanup path.
- **Reconstructing a component or mount.** The live runtime already is the session being handed
  forward.
- **Keeping a session alive without a user interaction.** Discord does not grant new edit
  authority from a timer.
- **Durable/restart-surviving ephemeral UI.** Ephemeral messages remain unrecoverable and plan 34's
  durability boundary continues to reject them.
- **A general lifecycle-overlay framework.** Implement the one renewal generation; extract a
  broader abstraction only when a second policy needs it.
- **Moving ephemerality into components.** `Destination` chooses visibility, `DeliveryReceipt`
  reports it, and `Mount` applies lifecycle policy.

## Implementation sequence

1. `discord: model mount expiry policies` — `PauseUpdates`, `RenewEphemeral`, chrome, validation,
   public exports, mount delivery-visibility fact, and snapshot lifecycle fields.
2. `discord: supervise expiring mount handles` — weak reactor watch set, per-policy margins,
   automatic registration/finish cleanup, timeout-aware eligibility, and removal of the global
   `expiry_margin` policy knob.
3. `discord: arm ephemeral renewal screens` — lifecycle candidate/commit path, framework binding,
   attachment preservation, freeze semantics, and races with handle replacement.
4. `discord: renew ephemeral mounts in place` — access-controlled dedicated dispatch, fresh-handle
   adoption, application restore, retries, and idempotence.
5. `bot: opt long-lived private panels into renewal` — route construction through `create_mount`,
   starting only with panels whose timeout can cross their interaction deadline.

Each commit is independently valid and keeps `PauseUpdates` as the default until the opt-in host
commit.

## Verification

- **Policy:** invalid warning values fail at construction; default policy preserves the existing
  paused-status test; `expiry=None` produces no pre-expiry edit.
- **Eligibility:** permanent, unknown-deadline, non-ephemeral, already-finished, and shorter-timeout
  mounts do not arm; a long-lived ephemeral mount does.
- **Supervision:** a scheduler-backed mount is watched without `follow`; finish and collection
  remove it; several deadlines arm through bounded reactor workers rather than the sweep itself.
- **Freeze:** arming replaces a maximally full application layout with the measured renewal screen,
  preserves attachments, runs no component/resource load on later refreshes, and retains one dirty
  application render.
- **Renewal:** the click passes access, adopts the interaction handle before responding, restores
  the latest application state on the same message, acknowledges by edit, and exposes the new
  `expires_at`.
- **Races:** renewal versus sweep, application interaction versus arm, two renewal clicks, finish
  versus renewal, and an old queued refresh versus the armed screen all settle without losing the
  renewal control or double-committing a generation.
- **Failures:** stale arming degrades to pending; failed renewal keeps the screen retryable; denied
  access neither disarms nor renews.
- Run `packages/squid-layouts/tests/test_mount.py` and `test_reactor.py` with `--no-cov`, the bot
  panel tests selected by the consumer commit, `just typecheck`, changed-file formatting/linting,
  `git diff --check`, and the package suite because lifecycle generation changes touch every mounted
  interaction.

## Status

Proposed 2026-08-22. Supersedes plan 26 §D's rejection of a dedicated control and the resolved
ephemeral-handoff entry in plan 90. It builds on plans 07, 23, and 26; it does not depend on
CascadeUI-compatible view reconstruction.
