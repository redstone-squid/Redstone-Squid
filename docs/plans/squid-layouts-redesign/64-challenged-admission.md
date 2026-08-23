# 64 — Challenged admission

## Problem

The CascadeUI comparison's fifth finding proposed two decorators:

```python
@confirm("Really delete this?")
@with_loading("Generating…")
```

One of them already exists under another spelling, and the other is not a decorator problem.

**Loading is shipped.** [31](31-action-ergonomics.md) §2's busy feedback is exactly
`@with_loading`, attached where the control is declared rather than where the handler is defined:

```python
sl.action("Generate", self.generate, key="generate", feedback=sl.Feedback(pending=L(t"Generating…")))
```

`Feedback` is `interactions.py:27`, `_BusyPaint` is `mount.py:647-701`, and the threshold is
`Mount(pending_after=1.0)` folded into the acknowledgement watchdog. This plan adds nothing there.
It records 31 §2's two known v1 gaps — `feedback=` is not accepted by `Choices` (no label to
relabel) or by a `FormTrigger`'s submission (a different interaction) — and closes neither.

**Confirmation is the real gap**, and it is one the decorator framing gets wrong.
`sl.patterns.confirm()` exists (`patterns/decision.py:153`) but is a component you *render*; there
is no way to say "this press requires reaffirmation" where the press is declared. So applications
hand-roll it, and this one did: `squid/bot/consent.py` builds `ConsentPrompt` around an
`anyio.Event` with a 120-second `move_on_after`, and `prompt_for_consent` opens it as an ephemeral
child and awaits the answer.

### The defect that makes this more than sugar

`AccountView._edit_page` (`squid/bot/account_view.py:242-249`) is an action handler:

```python
async def _edit_page(self, event: sl.PressEvent) -> None:
    interaction = sl.discord.native(event)
    if self._needs_consent:
        consent = await prompt_for_consent(
            interaction, user_id=self._author_id, locale=self.locale,
            parent=sl.discord.responder(event).mount,
        )
```

Handlers run inside `Mount._invoke`, inside the transaction, and — under the default
`ActionPolicy.EXCLUSIVE` — inside the mount's dispatch lock. So a confirmation dialog holds an
open transaction and the panel's lock for up to two minutes. `AccountView._consented`
(`account_view.py:389-397`) is the same shape, and the bare `await event.acknowledge()` it opens
with is the author working around the 2.5-second acknowledgement watchdog by hand.

No decorator fixes that, because the problem is *when* confirmation runs, not how it is spelled.
It is fixable only by moving confirmation before the transaction — and [31](31-action-ergonomics.md)
already built a stage there.

## Decision

The dispatch funnel puts guard admission after the access policy, after the concurrency gate, and
before the transaction (`Mount._admit`, `mount.py:2048`, called from `_dispatch_binding` at
`mount.py:2135`). The comment at `mount.py:2133-2134` states why in the terms this plan needs:
*"Inside the lock, so a `when(...)` closure reads component state nobody is writing, and before
the transaction, so a denial writes nothing."*

Guards answer *may this press execute now* with a two-valued verdict. Confirmation is the third
value:

> **not yet — ask the actor, and if they approve, this same press proceeds.**

So generalize what `Guard.admit` may return:

```python
type GuardOutcome = GuardVerdict | Challenge

@dataclass(frozen=True, slots=True)
class Challenge:
    """Admission deferred to the actor. Approval re-enters the same press."""

    ask: Callable[[ChallengeResolver], Component]
    deadline: float | None = 120.0
    on_decline: TextLike | None = None      # None -> chrome default

class ChallengeResolver(Protocol):
    async def approve(self) -> None: ...
    async def decline(self) -> None: ...

# sl.guards
def confirm(prompt: TextLike, *, danger: bool = True, deadline: float | None = 120.0) -> Guard: ...
```

`sl.guards.confirm` is sugar over `Challenge` plus the existing `sl.patterns.confirm()` component,
so the rendering half is reused rather than rebuilt.

This is a better abstraction than a `@confirm` decorator because it is one concept rather than a
special case. Accepting terms of service, acknowledging a cost ("this costs 500 coins"), and
re-authenticating are all *admission that needs an answer from the actor*, and all of them compose
through the existing `all_of` / `any_of` for free. A decorator would serve exactly one of them.

It is also the seam that keeps the answer out of the transaction, which is the property the whole
plan exists for.

## Mechanics

A feasibility pass over `mount.py` confirms this is implementable. One trap and three structural
constraints shape the API, and the first is the one most likely to be got wrong.

### 0. The approval interaction must not become the parent's edit target

`deliver.handle_from` (`delivery.py:260-269`) returns a handle to the message its interaction
*came from* — for an approval click, the **child's** ephemeral message. `_flush` prefers that
source over the mount's standing handle (`mount.py:2455`; `_write` at `mount.py:1746`). Resuming
the parent's press with the approving interaction would therefore render the parent's panel into
the confirmation dialog, and `_flush` (`mount.py:2481-2486`) would treat that write as having
answered the click.

**Invariant: a resumed press flushes through the mount's own `EditHandle`, never through the
approving interaction.** The child answers its own click and finishes; the parent redraws where it
already lives. This is stated as an invariant, not a note, because every other part of the design
reads correctly while getting it wrong.

### 1. A challenge presents and returns; it never awaits inside the lock

Awaiting the answer inside `_admit` would reproduce the `consent.py` defect one layer down, and on
EXCLUSIVE it would serialize every other action on the mount for the dialog's lifetime. Instead
the challenge is presented as an ephemeral child and `_admit` returns without admitting, releasing
the lock. The press is not parked; it is dropped, and approval starts a new one.

Presenting through `respond_to(interaction)` *is* the acknowledgement, which matters because
`_admit` runs before `_invoke` starts the acknowledgement watchdog (`mount.py:2156-2168`) — a
challenge has the interaction's own ~3s budget and no safety net, so presentation must be prompt
and must not be preceded by application I/O.

### 2. Approval re-enters `Mount.dispatch` from the top

Not from `_admit`. The actor may have lost access while the dialog was open, and the panel may
have re-rendered. `dispatch` re-resolves `self._handlers.get(key)` on every call
(`mount.py:1802`), so resumption is an ordinary fresh dispatch and every stage — finished check,
access policy, concurrency gate, guards — runs again against current truth.

Two details it must carry:

- **The routed binding key, not the original control key.** `dispatch` rewrites the key for a
  grouped select at `mount.py:1811-1819`; retaining the outer key would lose the route.
- **`generation=None`.** The submitted generation is stale by definition after a dialog, and
  EXCLUSIVE rejects a mismatched one at `mount.py:2115`. Passing `None` accepts that the press now
  runs against the newest scene, which is the same contract `ActionPolicy.REBASE` already offers
  and the right one here: the actor confirmed an intent, not a pixel.

Selection presses carry `_EntityValues` (`mount.py:414-417`), which holds live discord.py objects,
so retained challenge state is in-process and mount-scoped by construction. It cannot be
serialized, and does not need to be.

### 3. The approval token lives in the `GuardLedger`

Guards are rebuilt on every render and hold no state (`guards.py:62-63`), so a guard cannot
remember that it just challenged — without a token the resumed pass challenges again, forever. The
`GuardLedger` (`guards.py:59-94`) is the only mount-lifetime store a guard can see, which makes it
the right home. The token is consumed on read, so one approval admits one press.

### 4. A challenging pass records nothing

An earlier guard spending its ledger write before a later one denies is *documented, deliberate
behaviour*, not a bug — `all_of`'s own docstring (`guards.py:259-263`) says so and tells the author
to order cheap unconditional checks first. That advice works for denial and **cannot** work for a
challenge: `confirm` belongs last (you do not ask before checking the cooldown), so it is exactly
the guard whose non-admit outcome would consume every earlier one, and the actor who cancelled
would be the one paying for it.

The fix needs no overlay and no two-phase protocol. **An admission pass that ends in a challenge
records nothing**, because the press was not admitted; guards re-run on approval and record there.
Guards run twice, record once, on the pass that actually admits. Denial behaviour is unchanged
byte for byte, and 31 §1's "guards record at admission" holds verbatim — it is now true of a
single, atomic admission outcome.

Mechanically this means the composites cannot let their members write during a challenging pass,
so the ledger view handed to guards during admission buffers writes and discards them unless the
pass admits. `_RateLimit.admit` writes its pruned window even on the deny path (`guards.py:166`),
so the buffering is at the ledger view rather than at the verdict.

### 5. Evaluation stops at the first non-admit outcome

Denial or challenge. A guard chain never asks a question it is about to deny anyway, and
`any_of` reports its last denial as it does today. A `Challenge` reaching `any_of` is returned
rather than treated as a denial, since a question is not a "no".

### 6. `Mount` cannot open the dialog itself

It holds no `SessionRegistry` reference — the lookup runs the other way
(`SessionRegistry.session_for`, `sessions.py:604`) — and `Screen.open` needs a registry plus an
`Opener`. So the presenter is injected: a host-supplied `ChallengePresenter` on `MountDefaults`
([43](43-mount-defaults.md)), which is where the host-wide half of `Mount.__init__` already lives.

This also keeps `Challenge` portable. The core names *what to ask*; the Discord layer owns *how it
is shown*. A mount with no presenter configured refuses a challenge as a programmer error at
admission — routed to `handle_error` like a raising guard, never silently admitted.

### 7. `_admit` stops returning `bool`

It currently returns a boolean *and* terminates the profile itself (`mount.py:2074, 2086`), so a
third outcome is a small return-type change at both call sites (`mount.py:2106, 2135`). Two new
`DispatchDisposition` members, `CHALLENGE_ISSUED` and `CHALLENGE_DECLINED`, keep [37](37-runtime-profiling.md)'s
traces honest about which stage refused, the way `GUARD_DENIED` / `GUARD_FAILED` were added. Both
map to `TraceOutcome.COMPLETED` in `_DispatchProfile.finish` (`mount.py:626-635`) — nothing failed
— which is worth stating because the default mapping gets it right for the wrong reason.

### 8. Lifetime

An unanswered challenge dies with the mount and carries a deadline. Resumption against a finished
mount is refused with the existing `session_ended` chrome, not re-run. A declined challenge
consumes its token and responds with `on_decline` or the chrome default.

## Scope

`Action` and `primitives.Button` only, matching where `guard=` is accepted today.

Form submissions cannot be challenged in v1: `dispatch_submit` synthesizes a `_SubmitBinding` per
submission (`mount.py:1949`) that never enters `_handlers`, and the modal interaction is
single-use, so there is no key to resume by. This is the same line 31 §1 drew for guards on forms —
the press that opens the modal is admitted, the submission that follows is the completion of an
already-admitted press.

`PatternControls.action` (`patterns/shells.py:85`), `Choices` and `SelectMenu` have no `guard=`
seam at all, so patterns cannot be challenged. Noted, not widened here.

Routed actions stay out for the reason 31 §1 gave: stateless dispatch has no mount and no ledger,
and [16](16-routed-actions-part-two.md)'s middleware onion is the routed admission seam.

## Consumers

`squid/bot/consent.py`. `prompt_for_consent` becomes a `Challenge`, and `account_view.py`'s two
handlers lose their hand-rolled `anyio.Event` wait, their manual `event.acknowledge()`, and their
three-way `NOT_ASKED` / `None` / consent return handling — the handler is left holding only the
work it was always about. That the sugar *deletes* code rather than wrapping it is the evidence
the abstraction is placed correctly.

`ConsentPrompt` itself survives as the component the challenge asks with, which is the point of
`Challenge.ask` taking a component factory rather than a string.

## Rejected

- **`raise sl.Ask(component)` from inside a handler.** Reads beautifully and is wrong: the handler
  runs inside a transaction, so raising unwinds it, and re-running after approval re-runs any
  pre-ask side effects. Guards run before the transaction precisely so that the question can be
  asked without anything to unwind. Recorded because it is the obvious idea and will be proposed
  again.
- **`@confirm` / `@with_loading` decorator aliases.** The package has no handler-wrapping
  decorators at all — the only `functools.wraps` in `src/` is `_checked_init`
  (`runtime/component.py:170`) — and handlers are plain bound methods passed positionally. A
  decorator layer would be a second attachment mechanism beside `guard=` / `feedback=` / `record=`,
  and it would attach the policy to the function rather than to the control, so the same handler
  could not be a confirmed button in one place and a plain one in another.
- **Collapsing `guard=`, `feedback=`, `record=` into one `Conduct` value.** Three keyword
  parameters is near the smell threshold, but they are genuinely orthogonal and independently
  optional; a container would add a noun without removing a decision. Not
  [56](56-one-declaration.md)'s situation, where the two declarations differed only by facts the
  owner already knew.
- **Value-carrying approval** (`approve(value)` surfacing as `event.answer`). It wants a typed
  channel from guard to handler, which is what `Form` ([18](18-forms.md)) already is. Yes/no in v1;
  a challenge that needs an answer with a shape is a form, and forms dispatch through the funnel
  on their own.
- **Awaiting the answer inside `_admit`.** See §1 — it is the defect this plan exists to remove.

## Verification

- A challenged press opens an ephemeral child, admits nothing, opens no transaction, bumps no
  generation, and releases the action lock before the answer arrives.
- Approval re-runs the whole funnel: a mount finished, access revoked, or a cooldown started while
  the dialog was open all refuse the resumed press.
- **The parent redraws on the parent's message.** Assert the edit target explicitly, and assert the
  child's message is not written by the parent's flush.
- A second press while a challenge is outstanding does not block on the first: EXCLUSIVE is free.
- The token admits exactly once — a replayed approval does not admit a second press, and an
  approval for a different action key does not admit.
- `all_of(cooldown(5), confirm(...))`: declining leaves the cooldown unspent; approving spends it
  exactly once. `all_of(cooldown(5), permission(deny))` keeps today's behaviour unchanged, with a
  test pinning it so the buffering change cannot alter denial semantics.
- `any_of` returns a challenge rather than treating it as a denial.
- A mount with no `ChallengePresenter` routes to `handle_error` and finishes `GUARD_FAILED`; it
  never admits.
- An expired challenge declines; a challenge outliving its mount is collected with it.
- Traces report `CHALLENGE_ISSUED` and `CHALLENGE_DECLINED` and map both to `COMPLETED`.
- `feedback=` behaviour is unchanged: run the existing busy-paint tests untouched.
- Migrate `squid/bot/consent.py` and `account_view.py`, and confirm the two handlers no longer
  await inside their transaction.
- Focused runs of `test_guards.py`, `test_mount.py::TestGuards`, `test_decision_pattern.py` and the
  consent tests with `--no-cov`, then `just typecheck`, `alembic heads`, and `git diff --check`.

## Status

Designed. Depends on [31](31-action-ergonomics.md) (shipped) and [43](43-mount-defaults.md) for the
presenter's home.
