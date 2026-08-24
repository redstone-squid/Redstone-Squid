# 64 — Challenged admission

## Status

Shipped. `sl.guards.confirm` is `guards.py`; the funnel changes are `mount.py`; the presenter
and its supervisor are `discord/challenges.py`; the tests are `tests/test_challenges.py`.
`AccountPanel._unlink` is migrated and its three pieces of view state are gone.

Built on [31](31-action-ergonomics.md) and [43](43-mount-defaults.md). The task seam §3
demanded is `ChallengeRunner`, and it is part of this plan rather than a prerequisite for it.

Where the build differs from what was designed, the section says so — the substantial ones
are §2 (the routed key needed no new plumbing), §3 (the seam's shape), §4 (a counter, not a
token) and Scope (`FormTrigger` is refused, `ClaimReviewComponent` was never a consumer).

This is not the fix for the defect the first draft was written around. That defect is closed;
see Provenance, which is the part of this file worth reading first.

## Provenance

The first draft opened on the CascadeUI comparison's fifth finding, which proposed `@confirm`
and `@with_loading` decorators. Two of its three conclusions survive intact:

**Loading is shipped.** [31](31-action-ergonomics.md) §2's busy feedback is exactly
`@with_loading`, attached where the control is declared rather than where the handler is
defined:

```python
sl.action("Generate", self.generate, key="generate", feedback=sl.Feedback(pending=L(t"Generating…")))
```

`Feedback` is `interactions.py:27`, `_BusyPaint` is `mount.py:647-701`, and the threshold is
`Mount(pending_after=1.0)` folded into the acknowledgement watchdog. This plan adds nothing
there. It records 31 §2's two known v1 gaps — `feedback=` is not accepted by `Choices` (no
label to relabel) or by a `FormTrigger`'s submission (a different interaction) — and closes
neither.

**A decorator is the wrong attachment point for confirmation.** Still true, for the reason
under Rejected: it would bind the policy to the function rather than to the control.

What did not survive is the claim that made this plan urgent.

### The defect this was going to fix, and why it did not

`prompt_for_consent` was awaited from three mounted action handlers. A handler runs inside the
mount's transaction and, under the default `ActionPolicy.EXCLUSIVE`, inside its dispatch lock,
so awaiting the reader's answer held an open transaction and every control on the panel for as
long as they took to read — up to the prompt's full 120 s. The hand-rolled
`event.acknowledge()` and `interaction.response.defer()` those handlers opened with were the
authors working around the acknowledgement watchdog by hand, for the same reason.

The diagnosis was right. The proposed cure did not fit it:

- **Guard admission cannot reach two of the three sites.** `guard=` is accepted by `Action`
  (`semantic.py:434`), `FormTrigger` (`semantic.py:422`) and `primitives.Button`
  (`nodes.py:154`). `Toggle` has no such field (`semantic.py:349-358`, `factories.py:536-547`),
  and two of the three waits were behind `AccountPanel`'s two toggles. The first draft's
  Consumers section claimed both of that file's handlers would lose their wait; `_consented` is
  reachable only from toggles, so it could not.
- **The wait was not where the fix had to be.** The prompt is already a mount of its own,
  opened with `parent=`, so its buttons dispatch outside the press that asked. Inverting
  `ConsentPrompt.wait()` into an `on_answer` continuation removes the wait from all three sites
  with no framework change at all.

That is what shipped. `request_consent` returns once the notice is on screen and the press
ends; `with_consented_account` is the same inversion over `ensure_consented_account`, and it
owns the redraw — through the panel's own handle, never through the prompt's interaction. The
awaiting forms stay for commands, which own their wait and hold no mount state while it runs.

Two things fell out of writing the test that drives a real press through both mounts, rather
than stubbing the prompt as every earlier test did:

- `ConsentPrompt._accept` assigned `self._consent` inside the prompt's transaction. Since
  [41](41-reactivity-cells.md) made undeclared transaction-time writes always raise, agreeing
  to the privacy notice had been raising and reading back as a cancellation. Declaring it as
  state does not fix it either — a cell write is staged until the handler returns, so a waiter
  in another task reads `None`, and the teardown `finish` performs a line later drops it. The
  answer is not view state and now lives off the component.
- A cross-mount state write does work: the prompt's press writes the panel's declared cells,
  they commit with the prompt's transaction, and the panel redraws through its own handle. §0's
  invariant is therefore already load-bearing in shipped code, one layer up from where this
  plan puts it.

So the defect is gone, and this plan is now what it should have been from the start: a library
capability, judged on library merits.

## The gap

`sl.patterns.confirm()` exists (`patterns/decision.py:153`) but is a component you *render*.
There is no way to say "this press requires reaffirmation" where the press is declared, so
every author writes the two-press state machine by hand. This bot writes it twice:

- `AccountPanel._unlink` (`account_view.py:223-241`) holds `unlink_armed`, returns early on the
  first press, relabels its own button to "Unlink for good", and renders the warning from
  `_footer()`. Three pieces of view state and render logic for "are you sure".
- `ClaimReviewComponent` holds `reassign_armed` for what looks like the same shape over a
  conflicting claim. It turned out not to be; see Scope.

Neither is consent, neither is exotic, and both are `primitives.Button` — where `guard=`
already is. That is the demand this plan answers, and unlike the consent case the scope
actually covers it.

## Decision

The dispatch funnel puts guard admission after the access policy, after the concurrency gate,
and before the transaction (`Mount._admit`, `mount.py:2111`, called from `_dispatch_binding` at
`mount.py:2169` and `2198`). The comment at `mount.py:2196-2197` states why in the terms this
plan needs: *"Inside the lock, so a `when(...)` closure reads component state nobody is
writing, and before the transaction, so a denial writes nothing."*

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
    on_decline: TextLike | None = None      # None -> say nothing; see §9

class ChallengeResolver(Protocol):
    async def approve(self) -> None: ...
    async def decline(self) -> None: ...

# sl.guards
def confirm(
    prompt: TextLike,
    *,
    danger: bool = True,
    deadline: float | None = 120.0,
    on_decline: TextLike | None = None,
) -> Guard: ...
```

`sl.guards.confirm` is sugar over `Challenge` plus the existing `sl.patterns.confirm()`
component, so the rendering half is reused rather than rebuilt.

This is a better abstraction than a `@confirm` decorator because it is one concept rather than
a special case. Accepting terms of service, acknowledging a cost ("this costs 500 coins"), and
re-authenticating are all *admission that needs an answer from the actor*, and all of them
compose through the existing `all_of` / `any_of` for free. A decorator would serve exactly one
of them.

## Mechanics

A feasibility pass over `mount.py` and `squid_reactive/core.py` says this is implementable, but
§3 is a prerequisite the package does not currently satisfy.

### 0. The approving interaction must not become the parent's edit target

`deliver.handle_from` (`delivery.py:297-306`) returns a handle to the message its interaction
*came from* — for an approval click, the **child's** ephemeral message. Three places in the
parent's dispatch would take it:

- `_flush` prefers that source over the mount's standing handle (`mount.py:2518`; `_write` at
  `mount.py:1796`), so the parent's panel would render into the confirmation dialog, and
  `_flush` would treat that write as having answered the click.
- `_renew` (`mount.py:1826`, called from `_invoke_and_flush` at `mount.py:2294`) trades the
  mount's standing handle up to the click's. The parent would re-address itself to the dialog's
  ephemeral message **permanently**, outliving the press. This is the worse of the two and the
  first draft missed it.
- `_note_address(interaction.message)` (`_begin_dispatch`, `mount.py:2057`) would record the
  dialog's coordinates for a parent that has not yet learned its own.

**Invariant: a resumed press treats its interaction as an actor identity and a private
answering channel, never as this mount's message.** The child answers its own click and
finishes; the parent redraws where it already lives.

Mechanically this is one field, not a parameter threaded through six frames:
`_DispatchProfile.resumed`. All three sites already hold the profile — `_begin_dispatch` and
`_invoke_and_flush` take it directly, and `_flush` receives it as `dispatch=`. There is a
fourth, which the draft missed twice over: `_BusyPaint` takes its own handle from the
interaction, at `show` and again at `restore`, so it is constructed with the flag. All four
now go through one `Mount._source(interaction, resumed=...)`.

**As built, the hazard cannot arise anyway, and the flag is still worth having.** The resumed
press carries the interaction that *asked*, not the one that answered — approval needs no
fresh interaction, and reusing the asking one keeps `ChallengeResolver.approve()` argument-free.
That interaction spent its response on the question, and `handle_from` already returns `None`
for an interaction "whose response has been spent on something that is not that message"
(`delivery.py:300-306`). So the invariant holds twice: once because the mount refuses to take
a handle on a resumed press, and once because there is no handle to take. The flag is what
makes it the mount's own rule rather than a consequence of what the presenter did with the
interaction — a presenter that deferred first would otherwise quietly re-enable it.

### 1. A challenge presents and returns; it never awaits inside the lock

Awaiting the answer inside `_admit` would reproduce the defect this plan's first draft was
written about, one layer down, and on EXCLUSIVE it would serialize every other action on the
mount for the dialog's lifetime. Instead the challenge is presented as an ephemeral child and
`_admit` returns without admitting, releasing the lock. The press is not parked; it is dropped,
and approval starts a new one.

Presenting through `respond_to(interaction)` *is* the acknowledgement, which matters because
`_admit` runs before `_invoke` starts the acknowledgement watchdog (`mount.py:2217-2229`) — a
challenge has the interaction's own ~3 s budget and no safety net, so presentation must be
prompt and must not be preceded by application I/O.

### 2. Approval re-enters `Mount.dispatch` from the top

Not from `_admit`. The actor may have lost access while the dialog was open, and the panel may
have re-rendered. `dispatch` re-resolves `self._handlers.get(key)` on every call
(`mount.py:1863`), so resumption is an ordinary fresh dispatch and every stage — finished
check, access policy, concurrency gate, guards — runs again against current truth.

Two details it must carry:

- **The routed binding key, not the original control key.** `dispatch` rewrites the key for a
  grouped select at `mount.py:1872-1880`; retaining the outer key would lose the route. This
  needed no new plumbing, which the draft did not know: a grouped select's route bindings are
  registered in `_handlers` under their own keys (`dialect.py:85-87`), and re-routing an
  already-routed binding is a no-op (`routed()` returns `self` when `routes` is empty). So the
  resumption passes the key `_admit` saw, together with the same values, and lands on the same
  binding.
- **`generation=None`.** The submitted generation is stale by definition after a dialog, and
  EXCLUSIVE rejects a mismatched one at `mount.py:2176`. Passing `None` accepts that the press
  now runs against the newest scene, which is the same contract `ActionPolicy.REBASE` already
  offers and the right one here: the actor confirmed an intent, not a pixel.

Selection presses carry `_EntityValues` (`mount.py:414-417`), which holds live discord.py
objects, so retained challenge state is in-process and mount-scoped by construction. It cannot
be serialized, and does not need to be.

### 3. The resumption needs a task the package does not have

This is the prerequisite, and it is not negotiable.

`transaction()` **flattens** rather than nests: `if _CURRENT.get() is not None: yield; return`
(`squid_reactive/core.py:631-635`). So resuming the parent's press from inside the approving
handler would run the parent's whole action inside the *child's* transaction — the parent's
writes would stage in the child's overlay, commit with it, and a parent failure would unwind
through the child's error hook. Worse, `readonly_transaction()` raises outright when nested
(`core.py:663-668`), so a resumed `PARALLEL_READ` press would not merely misbehave, it would
fail.

Spawning a task from inside the handler does not help either: `_CURRENT` is a `ContextVar`, and
a task started there inherits the context, transaction and all. The resumption has to run in a
task whose context was captured *before* the approving press — a supervisor started at host
startup, fed the approval through a queue.

`Mount` has no such seam. `Scheduler` (`mount.py:206-229`) only schedules refreshes. So the
seam is part of this plan, and the cheapest honest home for it is the presenter, which is
host-supplied anyway (§7): it owns both showing the dialog and handing the approval to
something that will run it outside the answering press. The shipped Discord implementation
takes a supervisor; the protocol documents the requirement, and a test pins it by asserting the
resumed press commits its own transaction.

The first draft did not have this section, which is why it read as smaller than it is.

**As built.** `ChallengeSupervisor` is one synchronous method, `resume(press)` — deliberately
synchronous, because an implementation that could await would be tempted to await the press.
`ChallengeRunner` is the shipped one: `resume` is an `asyncio.Queue.put_nowait`, and `run()`
is a host background task, started next to the reactor, that drains the queue and starts each
press from *its own* task. That last detail is the whole point and is easy to get wrong —
`TaskGroup.create_task` copies the caller's context, so calling it from the approving handler
would carry the transaction across; called from the drain loop it copies the loop's, which
predates every press. The queue is what crosses the boundary, and it is bounded: a full runner
drops the approval with a warning rather than awaiting inside a transaction, and the actor can
press again.

`resume` is called by the presenter's resolver, not by the mount, so `ChallengeRequest.approve`
— which is the resumed dispatch — reaches the supervisor without ever being awaited by the
dialog's own handler.

### 4. The approval lives in the `GuardLedger`, and it is a count

Guards are rebuilt on every render and hold no state (`guards.py:62-63`), so a guard cannot
remember that it just challenged — without a record the resumed pass challenges again, forever.
The `GuardLedger` (`guards.py:58-94`) is the only mount-lifetime store a guard can see, which
makes it the right home. It is keyed by action and actor like every other bucket, and consumed
on read, so one approval admits one press.

Two things the draft called a "token" needed pinning down to build it:

- **Where the name comes from.** The mount writes the approval and a guard reads it, so the two
  have to agree on the bucket without either owning the other. `guards.approvals(ledger, actor)`
  is that agreement, exported beside the vocabulary it belongs to.
- **It is an integer, not a flag.** With a flag, `all_of(confirm(a), confirm(b))` never
  converges: the second pass has the first guard eat the flag and the second guard ask again.
  With a count it does, and §5 is why — the pass that ends in the second question is discarded,
  so the first guard's consumption rolls back and both approvals are still there on the third
  pass. "One approval admits one press" survives verbatim; it just also composes.

### 5. A challenging pass records nothing

An earlier guard spending its ledger write before a later one denies is *documented, deliberate
behaviour*, not a bug — `all_of`'s own docstring (`guards.py:259-263`) says so and tells the
author to order cheap unconditional checks first. That advice works for denial and **cannot**
work for a challenge: `confirm` belongs last (you do not ask before checking the cooldown), so
it is exactly the guard whose non-admit outcome would consume every earlier one, and the actor
who cancelled would be the one paying for it.

**An admission pass that ends in a challenge records nothing**, because the press was not
admitted; guards re-run on approval and record there. Guards run twice, record once, on the
pass that actually admits.

The rule is **discard iff the pass challenged** — not "discard unless it admits". The first
draft wrote the latter and then, two sentences later and again under Verification, required
that denial behaviour be unchanged. Those contradict: `_RateLimit.admit` writes its pruned
window on the deny path (`guards.py:160`), and `all_of(cooldown(5), permission(deny))` spends
the cooldown today. Discarding on denial would silently change both. Denial keeps its writes,
byte for byte; only a challenge rolls them back.

Mechanically the composites cannot let their members write during a challenging pass, so the
ledger view handed to guards during admission buffers writes and replays them unless the
outcome is a `Challenge`. Buffering sits at the ledger view rather than at the verdict, because
a guard may write and then deny in the same call.

Built as `GuardLedger.staged()` and `.commit()`: `_admit` stages every pass and commits the
ones that did not end in a question. Guards see no difference, because a staged view reads its
own writes back. `clear()` stages too — a tombstone rather than a write — so a guard that
forgets an entry during a challenging pass has not actually forgotten it.

31 §1's "guards record at admission" holds verbatim — it is now true of a single, atomic
admission outcome.

### 6. Where evaluation stops

`all_of` stops at the first non-admit outcome, denial or challenge: a chain never asks a
question it is about to deny anyway.

`any_of` stops at a challenge and **not** at a denial — it continues past denials today
(`guards.py:191-199`) and must keep doing so or it stops being `any_of`. A `Challenge` reaching
it is returned rather than treated as a denial, since a question is not a "no". The first
draft's "evaluation stops at the first non-admit outcome" was written for `all_of` and is wrong
as stated for `any_of`.

This gives `any_of` an ordering rule of its own, and it is the opposite of `all_of`'s:
`any_of(confirm(...), permission(admin))` asks an admin who the permission branch would have
admitted for free, so put `confirm` **last** there too, but for the other reason.

### 7. `Mount` cannot open the dialog itself

It holds no `SessionRegistry` reference — the lookup runs the other way
(`SessionRegistry.session_for`, `sessions.py:604`) — and `Screen.open` needs a registry plus an
`Opener`. So the presenter is injected: a host-supplied `ChallengePresenter` on `MountDefaults`
([43](43-mount-defaults.md)), which is where the host-wide half of `Mount.__init__` already
lives, and which §3 makes carry the resumption seam as well.

This also keeps `Challenge` portable. The core names *what to ask*; the Discord layer owns *how
it is shown*. A mount with no presenter configured refuses a challenge as a programmer error at
admission — routed to `handle_error` like a raising guard, never silently admitted.

The dialog opens with `parent=` and inherits the asking mount's `localization`, so the question
and its Confirm/Cancel chrome are in the reader's language rather than the host's default. Two
consequences worth knowing when wiring a host: a panel that was never opened through the
registry has no session to attach to, so its dialog opens as a root session under the screen's
own key and still dies on its deadline; and *every* construction path needs the presenter, not
just the registry's. In this bot most panels are built by `create_mount` from a module-level
`MOUNT_DEFAULTS` and never touch `bot.mounts`, so the bot installs the presenter into both —
otherwise a migrated button would challenge and be refused as a programmer error in exactly the
places least likely to be tested.

### 8. `_admit` stops returning `bool`

It currently returns a boolean *and* terminates the profile itself (`mount.py:2137, 2149`), so
a third outcome is a small return-type change at both call sites. It returns `_Admission`, and
the reason it is three-valued rather than "refused, already answered" is §1: the challenge has
to be carried back out of the `try` block so the dialog is opened with the action lock already
released. Two new
`DispatchDisposition` members (`profiling/model.py:30-45`), `CHALLENGE_ISSUED` and
`CHALLENGE_DECLINED`, keep [37](37-runtime-profiling.md)'s traces honest about which stage
refused, the way `GUARD_DENIED` / `GUARD_FAILED` were added. Both map to
`TraceOutcome.COMPLETED` in `_DispatchProfile.finish` (`mount.py:618-635`) — nothing failed —
which is worth stating because the default mapping gets it right for the wrong reason.

### 9. Lifetime

An unanswered challenge dies with the mount and carries a deadline; expiry is a decline that
never issued an approval, so there is nothing to consume and nothing to clean up. The deadline
is simply the dialog mount's `timeout`, so this needs no timer of its own. Resumption against a
finished mount is refused with the existing `session_ended` chrome, not re-run.

`on_decline` is delivered by the mount as a private followup, not rendered by the dialog, so
every challenge declines the same way whoever wrote its `ask`. `None` says nothing at all
rather than falling back to chrome, which is what the draft had: a dialog that closes is
already the answer, and a second ephemeral saying so is noise.

The first draft said a declined challenge consumes its token. It never had one: the approval is
written by `approve`.

## Scope

`Action` and `primitives.Button` only, matching where `guard=` is accepted today.

`Toggle` (`semantic.py:349-358`), `Choices`, `SelectMenu` and `PatternControls.action`
(`patterns/shells.py:85`) have no `guard=` seam at all. Widening it is a separate decision with
its own cost, and this plan does not assume it.

Form submissions cannot be challenged in v1: `dispatch_submit` synthesizes a `_SubmitBinding`
per submission (`mount.py:2012`) that never enters `_handlers`, and the modal interaction is
single-use, so there is no key to resume by. This is the same line 31 §1 drew for guards on
forms — the press that opens the modal is admitted, the submission that follows is the
completion of an already-admitted press.

**Nor can a form *trigger* be challenged, which the draft did not notice.** `FormTrigger`
accepts `guard=` (`semantic.py:422`), but the press it guards answers with `send_modal`, and a
challenged press has spent its response on the question. Since the resumption reuses that same
interaction (§0), there is nothing left to open a modal through. `_present_challenge` refuses
it as a programmer error — `key in self._form_bindings` is the whole check — rather than
letting it fail at Discord. Confirming a form is `confirm()` *inside* the form, or an ordinary
button that opens it.

### The second consumer was not one

The gap named two hand-rolled machines. Only `AccountPanel._unlink` is a confirmation.

`ClaimReviewComponent.reassign_armed` looks identical from the outside — a flag, an early
return, a relabelled danger button — but it is armed by `AliasAlreadyClaimedError` coming back
from the service (`claims_view.py:178-181`), not by the first press. The question "take this
name from its current holder?" only exists once the attempt has failed, and a guard runs before
the handler, so `confirm()` cannot know to ask it. Asking on every approval instead would be
worse than the flag.

It is left alone. That the first draft counted it as a consumer is the same failure it
diagnosed in its own predecessor: naming a consumer without checking that the mechanism reaches
it. One genuine consumer is enough to justify the sugar; two would have been a nicer sentence.

Routed actions stay out for the reason 31 §1 gave: stateless dispatch has no mount and no
ledger, and [16](16-routed-actions-part-two.md)'s middleware onion is the routed admission
seam.

## Rejected

- **`raise sl.Ask(component)` from inside a handler.** Reads beautifully and is wrong: the
  handler runs inside a transaction, so raising unwinds it, and re-running after approval
  re-runs any pre-ask side effects. Guards run before the transaction precisely so that the
  question can be asked without anything to unwind. Recorded because it is the obvious idea and
  will be proposed again.
- **`@confirm` / `@with_loading` decorator aliases.** The package has no handler-wrapping
  decorators at all — the only `functools.wraps` in `src/` is `_checked_init`
  (`runtime/component.py:170`) — and handlers are plain bound methods passed positionally. A
  decorator layer would be a second attachment mechanism beside `guard=` / `feedback=` /
  `record=`, and it would attach the policy to the function rather than to the control, so the
  same handler could not be a confirmed button in one place and a plain one in another.
- **Collapsing `guard=`, `feedback=`, `record=` into one `Conduct` value.** Three keyword
  parameters is near the smell threshold, but they are genuinely orthogonal and independently
  optional; a container would add a noun without removing a decision. Not
  [56](56-one-declaration.md)'s situation, where the two declarations differed only by facts
  the owner already knew.
- **Value-carrying approval** (`approve(value)` surfacing as `event.answer`). It wants a typed
  channel from guard to handler, which is what `Form` ([18](18-forms.md)) already is. Yes/no in
  v1; a challenge that needs an answer with a shape is a form, and forms dispatch through the
  funnel on their own.
- **Awaiting the answer inside `_admit`.** See §1 — it is the defect this plan's first draft
  existed to remove.
- **Resuming from inside the approving handler.** See §3: transactions flatten, so the parent's
  press would have no boundary of its own, and a `PARALLEL_READ` resumption would raise. This
  is the idea the first draft implicitly assumed.
- **Continuation callbacks as the general answer.** They are what fixed the consent defect and
  they reach controls this plan cannot, but they put the whole two-press state machine back in
  the author's hands — which is the thing The gap is about. The two are complementary: a
  continuation is how a *component* asks something, a challenge is how a *control declares*
  that it must.

## Verification

- A challenged press opens an ephemeral child, admits nothing, opens no transaction, bumps no
  generation, and releases the action lock before the answer arrives.
- Approval re-runs the whole funnel: a mount finished, access revoked, or a cooldown started
  while the dialog was open all refuse the resumed press.
- **The parent redraws on the parent's message.** Assert the edit target explicitly, assert the
  child's message is not written by the parent's flush, and assert the parent's *standing*
  handle is unchanged afterwards — the `_renew` half needs its own assertion because it
  survives the press that would expose it.
- **The resumed press commits its own transaction.** Assert it is not joined to the approving
  press's, and cover a `PARALLEL_READ` binding, which raises rather than misbehaves if it is.
- A second press while a challenge is outstanding does not block on the first: EXCLUSIVE is
  free.
- The token admits exactly once — a replayed approval does not admit a second press, and an
  approval for a different action key does not admit.
- `all_of(cooldown(5), confirm(...))`: declining leaves the cooldown unspent; approving spends
  it exactly once. `all_of(cooldown(5), permission(deny))` keeps today's behaviour unchanged,
  with a test pinning it so the buffering change cannot alter denial semantics.
- `any_of` returns a challenge rather than treating it as a denial, and still reports its last
  denial when no member challenges.
- A mount with no `ChallengePresenter` routes to `handle_error` and finishes `GUARD_FAILED`; it
  never admits.
- An expired challenge declines; a challenge outliving its mount is collected with it.
- Traces report `CHALLENGE_ISSUED` and `CHALLENGE_DECLINED` and map both to `COMPLETED`.
- `feedback=` behaviour is unchanged: run the existing busy-paint tests untouched.
- A `FormTrigger` carrying a challenging guard is refused as a programmer error.
- Migrate `AccountPanel._unlink` and delete the view state, the early return and the
  relabelling it holds. That the sugar *deletes* code rather than wrapping it is the evidence
  the abstraction is placed correctly. (`ClaimReviewComponent` is not a consumer; see Scope.)
- Focused runs of `test_guards.py`, `test_mount.py::TestGuards`, `test_decision_pattern.py` and
  the account-panel tests with `--no-cov`, then `just typecheck`, `alembic heads`, and
  `git diff --check`.

### What the build actually ran

`tests/test_challenges.py` is the new file, 27 tests over the list above. Every bullet is
covered except two, and both are deliberate:

- *"An expired challenge declines"* — the deadline is the dialog mount's own `timeout`, so the
  behaviour under test belongs to `Mount`, which already has it covered. Nothing here re-tests
  it.
- *"`feedback=` behaviour is unchanged"* — the existing busy-paint tests ran untouched and
  pass, which is the assertion. `_BusyPaint` took a `resumed` flag on the way past.

Suites: `../../../../packages/squid-layouts/tests` — 1624 passed with the same 9 pre-existing failures as
before the change (`test_adoption` and friends, none of them admission); `../../../../tests/unit/bot` —
595 passed with the same 5 failures and 2 errors as the branch already had. `pyrefly` holds at
287 errors, unchanged: widening `Guard.admit` to `GuardOutcome` cost 41 of them in
`test_guards.py` until those assertions were narrowed through one `_verdict` helper, which is
the honest price of the third outcome and is paid once. `alembic heads` was not run — nothing
here touches the schema.
