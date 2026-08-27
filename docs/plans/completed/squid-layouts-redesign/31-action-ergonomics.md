# 31 — Action ergonomics (Batch C)

## Goal

Make actions declarative about the three things every consumer currently hand-rolls:
whether a press may execute (guards), what the reader sees while it executes (busy
feedback), and how a multi-step flow lets the reader reconsider (wizard review). Plus the
ordinal control 29 deferred here (`ScaleField`/`rating`).

The batch is defined against the dispatch funnel, by stage rather than by line:

```text
_begin_dispatch: finished check → access policy (plan 34)
→ concurrency policy gate (ActionPolicy)
→ guard admission            ← new
→ busy interim               ← new
→ transaction + handler
→ flush
```

with the acknowledgement watchdog running alongside. Plan [34](34-safe-session-runtime.md)'s
`AccessPolicy` (already live in `Mount._begin_dispatch`) answers *who may interact with
this message*; guards answer *may this press execute now*. Panel-wide gating belongs in
`access=`; per-action requirements — a cooldown, a deadline, one privileged button on an
otherwise-public panel — are guards. The two compose and neither subsumes the other.

## 1. Guarded actions

Admission is a portable vocabulary in a new `guards.py`, attached per control:

```python
@dataclass(frozen=True, slots=True)
class GuardVerdict:
    allowed: bool
    reason: TextLike | None = None          # None → chrome default
    retry_after: float | None = None        # seconds, feeds chrome.try_again_in

ADMIT = GuardVerdict(True)
def deny(reason: TextLike | None = None, *, retry_after: float | None = None) -> GuardVerdict: ...

class Guard(Protocol):
    async def admit(self, event: ActionEvent, ledger: GuardLedger) -> GuardVerdict: ...

class GuardScope(StrEnum):
    ACTOR = "actor"
    MOUNT = "mount"

# sl.guards
def cooldown(seconds: float, *, per: GuardScope = GuardScope.ACTOR, key: str | None = None) -> Guard: ...
def when(predicate: Callable[[ActionEvent], bool | Awaitable[bool]], *, reason: TextLike) -> Guard: ...
def permission(check: Callable[[ActionEvent], Awaitable[bool]], *, reason: TextLike | None = None) -> Guard: ...
def once(*, per: GuardScope = GuardScope.ACTOR, key: str | None = None) -> Guard: ...
def rate_limit(count: int, per_seconds: float, *, per: GuardScope = GuardScope.ACTOR, key: str | None = None) -> Guard: ...
def until(deadline: datetime, *, reason: TextLike | None = None) -> Guard: ...
def all_of(*guards: Guard) -> Guard: ...    # first denial wins
def any_of(*guards: Guard) -> Guard: ...    # first admission wins; the last denial is reported
```

`semantic.Action` and `FormTrigger` gain `guard: Guard | None = None`, threaded through
`sl.action`/`sl.form` into `ActionBinding` and checked by the mount. Grouped actions keep
theirs: the select route bindings `_picker` builds carry the guard of the `Action` they
came from, so collapsing a row into a select does not drop admission.

The design decisions, in order of sharpness:

1. **Admission runs after the concurrency policy gate, inside the EXCLUSIVE lock, before
   the transaction.** Inside the lock so `when(...)` closures read consistent component
   state; before the transaction so a denial writes nothing, bumps no generation, and
   costs one ephemeral message. Pre-lock admission is rejected as racy; opening a
   transaction merely to deny is rejected as waste. Concretely the check sits at the end
   of `_dispatch_binding`, immediately before `invoke(...)`: inside the lock for
   EXCLUSIVE and REBASE, and — correctly — outside it for the two policies that never
   take it.
2. **Rejection is a private notice, not a redraw.** The denial responds ephemerally with
   the verdict's reason (or the chrome default), the same shape as the access denial in
   `_begin_dispatch`. Guards never affect rendering: `available=` remains the render-time
   tool, and the doc states why — a cooldown cannot disable a button it cannot re-render
   on a timer. An author who wants both disables via `available=` *and* guards the press.
3. **Stateful guards keep their state in a mount-owned `GuardLedger`, not on the guard.**
   Factories recreate guard objects every render, so `cooldown`/`once`/`rate_limit`
   compute a ledger key and read/write the ledger. The ledger lives and dies with the
   mount.
4. **Guard denial precedes any busy interim.** A denied press never flashes "Working…".

Two shapes the sketch left open, resolved:

- **The ledger a guard sees is an action-scoped view.** `Mount` owns one `GuardLedger`;
  each dispatch hands the guard `ledger.for_action(key)`, a cheap frozen view sharing the
  same entry dict and clock. A guard computes its bucket with
  `ledger.bucket(kind, per=..., actor=event.actor.id, key=...)`, which defaults to the
  scoped action key — so `cooldown(5)` on two different actions gets two buckets, and
  `cooldown(5, key="votes")` on both gets one. This keeps the sketch's two-argument
  `admit` while still letting a guard escape its action's namespace, which a bare
  `(event, ledger)` pair could not do.
- **Guards record at admission, not at commit.** `once()` is spent and `cooldown()` starts
  ticking the moment the press is admitted, whether or not the handler then succeeds.
  Recording after a successful commit was considered and rejected: it needs the handler's
  outcome, which turns `Guard` into a two-phase protocol, and it makes a handler that
  fails on every attempt an unguarded retry loop — the exact thing a rate limit exists to
  stop. An author who wants "succeeded once" wants component state and `available=`.

`GuardVerdict.reason` wins over `retry_after` when both are set: explicit author wording
beats generated wording, and the delay is then only advisory metadata for a host reading
verdicts.

`until` reads the wall clock (`datetime.now(UTC)`, aware deadlines only — a naive one
raises at construction), while `cooldown`/`rate_limit` read the ledger's monotonic clock.
That split is deliberate: a deadline is a fact about the world, an elapsed interval is a
fact about this process, and only the second may be frozen by a test clock.

`requires_role` is deliberately not portable — roles are a Discord fact, and the portable
core stays frontend-neutral (the same reasoning as plan [90](../../squid-ui-redesign/90-deferred.md)'s "portable
permission facts" entry, which this partially supersedes: the portable surface is
`Guard`/`GuardVerdict`, while frontend facts enter through plan 02's native access). It
ships as `sl.discord.guards.requires_role(role_id, *, reason=None)`, sugar over
`permission`. Its relation to 34's `Check` access policy: `Check` gates the mount,
`requires_role` gates one action.

**A guard gates the press, and for a form that is the press that opens the modal.**
`FormTrigger.guard` is checked when the button is clicked; the submission that follows is
the completion of an already-admitted press and is not re-admitted. Re-checking on submit
was rejected because every stateful guard would then double-consume — one `cooldown(30)`
would deny the reader's own filled-in form. A deadline that expires while a modal is open
is the handler's business, not the guard's, and the doc says so.

A guard that raises is a programmer error, not a denial: it routes to
`handle_error(interaction, error, f"guard:{key}")` and finishes the dispatch as
`GUARD_FAILED`, mirroring how a raising access policy is handled. Two new
`DispatchDisposition` members, `GUARD_DENIED` and `GUARD_FAILED`, keep 37's traces honest
about which stage refused.

Routed actions are out of scope in v1: stateless dispatch has no mount and no ledger, and
plan [16](16-routed-actions-part-two.md)'s middleware onion is already the routed
admission seam.

Consumers: `BuildEditComponent._may_event` and `SettingsPanel._may_event` (async
permission checks with their own wording — the exact case plan 90's participant-tracking
entry records), `squid/bot/utils/permissions.py` `requires`/`hide_unless`, vote cooldowns
in `squid/bot/voting/`, and `give_redstoner.py` panel actions.

## 2. Busy feedback

```python
@dataclass(frozen=True, slots=True)
class Feedback:
    pending: TextLike | None = None         # None → chrome.working
    restore_on_error: bool = True

sl.action("Generate", generate, key="generate", feedback=sl.Feedback())
```

Three decisions carry the design:

1. **The interim render is a scene patch, not a component re-render.** The handler runs
   inside a transaction; rendering components mid-action would observe half-written
   state. The mount instead re-emits its last committed scene with the pressed control's
   button relabelled to the pending text and **every** interactive control disabled —
   honest, since EXCLUSIVE will not accept another press anyway. Disabling only the
   pressed control is rejected because it invites clicks that will be swallowed.
   Mechanically this redraws `self._plan.scene` through the existing `Renderer` with a
   patching `wire` closure, so no scene-tree walker is added: the renderer already visits
   every control, and `_disable_all` finishes the job for links and routed buttons.
2. **Threshold, not immediate, folded into the watchdog.** For feedback-bearing actions
   the existing acknowledgement watchdog becomes two-stage: at `pending_after` (default
   1.0s, mount-configurable) it performs the interim edit via the interaction response —
   which *is* the acknowledgement, so the deferral at `acknowledgement_timeout` never
   fires. Fast handlers never flicker. Actions without `feedback` keep today's behavior
   byte for byte. When the interim edit cannot be made (no usable handle, a stale one),
   the watchdog falls through to the ordinary deferral at the remaining timeout.
3. **No persistent success state; failure restores.** Success chrome is the post-action
   flush itself. On handler error the mount must re-edit the original scene back (when
   `restore_on_error`) before `handle_error` runs — the current no-flush error path would
   otherwise strand the panel on "Working…" with every control dead. Transient success
   ticks ("Saved ✓" for two seconds) are considered and not done: they need timers the
   framework refuses to own, and `event.notice(...)` already covers the need manually.

Two mechanical consequences the sketch did not state:

- **A successful handler that changes nothing must also restore.** `flush` acknowledges
  without writing when the render is not dirty, which would leave the interim on screen
  forever. The busy paint is therefore restored whenever the flush did not write,
  independent of `restore_on_error` (that flag is about *errors*, and a stranded panel is
  not a policy choice).
- **The paint and the flush are ordered by the paint's own lock, not by cancellation.**
  The watchdog is cancelled only after `_invoke_and_flush` returns, so a paint already in
  flight is never cancelled mid-write. Before flushing, the invoker calls `close()`, which
  takes the same lock — it therefore waits out an in-flight paint and then latches the
  paint closed, so a watchdog that wakes late paints nothing over the final render. No
  cancellation shielding is needed, and none is used.

v1 attaches `feedback=` to `Action` only. A `Choices` selection has no label to relabel,
and a `FormTrigger`'s slow half is its submission, which is a different interaction;
both are recorded below rather than half-supported.

The relation to plan [33](33-resources.md) is worth one sentence in the doc: resource
settlement already gives *loads* a visible pending render; `Feedback` gives *actions* one.
Same philosophy — visible progress as a delivery policy — at a different stage of the
funnel.

Consumers: the long schematic operations (`squid/bot/submission/schematics.py`
`measure_timing`, `detect_lattice`, `schematic_render` and their component-surface
siblings), poll publish (`squid/bot/voting/poll_wizard.py`), and "Refresh weights"
(`squid/bot/voting/controls.py`).

## 3. Wizard review and jump-back

`Wizard` gains an optional review destination; no second setup pattern.

```python
@dataclass(frozen=True, slots=True)
class WizardReview:
    label: TextLike | None = None                                     # default chrome.review
    summarize: Callable[[WizardAnswers], ContentLike] | None = None   # default: per-step rows

class Wizard:
    def __init__(self, title, steps, *, key="wizard", review: WizardReview | bool = False) -> None: ...
```

`WizardState` gains `reviewing: bool = False`, which is route-serializable, so both shells
work unchanged. **Where the reader is and where the reader returns to are two facts, so
they are two fields**: `current` holds the reserved step key `"@review"` (`REVIEW_STEP`)
while the review screen is displayed, and `reviewing` says review is home. A step key may
not be `"@review"`; `_steps` rejects one. Folding both into `reviewing` was tried and
cannot express "editing step 2, will return to review".

New transitions: `review` enters the review screen (a final submit lands there
automatically instead of setting `complete` when review is enabled); `goto:<step>` jumps
to a step while keeping `reviewing=True` as the return anchor; a `submit:`/`next` while
reviewing returns to review instead of marching forward; `back` while reviewing returns to
review rather than to the previous step, so a jumped edit cannot wander off. Review renders
`summarize` or the default — one row per live form step, its answer summarized through each
field's `format_prefill`, unanswered steps shown as `chrome.unanswered`, with an Edit
(`goto:`) action per step — and Finish, available only when every live form step has an
answer. `transition` re-checks that completeness on `finish`; the state machine enforces,
the render merely reflects.

The sharp case: a jumped edit changes an answer and the branch grows new unanswered
steps. The wizard returns to review anyway, showing them as unanswered with Finish gated
— review is home once visited, and silently resuming march mode would lose the reader's
place. Orphan retention is untouched; review summarizes `live_answers()` only, exactly
like Finish.

This plan also retires the two hardcoded `"Finish"` literals in `wizard.py` — an existing
chrome leak — in favor of `chrome.finish`.

Consumers: `PollConfirmation` (`squid/bot/voting/poll_wizard.py` — a hand-rolled review
step with Edit/Publish/Cancel, the primary migration) and the build submission wizard
(`squid/bot/submission/submit.py`).

## 4. ScaleField and rating

Ordinal ratings need no new semantic node: `Choices` with `maximum=1` and a handful of
options already adapts to a button row, and that row *is* the star row. What is missing is
the typed form field and the message-surface sugar.

```python
@dataclass(frozen=True, slots=True)
class ScaleField(FormField[int]):
    minimum: int = 1
    maximum: int = 5
    labels: Mapping[int, TextLike] | None = None    # e.g. {1: "Poor", 5: "Excellent"}

def rating(
    *,
    key: str,
    maximum: int = 5,
    value: Ownership[int | None, ScaleEvent] = Managed(None),
    labels: Mapping[int, TextValue] | None = None,
) -> Choices: ...
```

The short alias is `Scale`. In the Discord modal adapter a span of ten or fewer renders as
a radio group (the `ChoiceField` shape); a larger span renders as a text input parsed like
`IntField` — an honest portable fallback needing no capability. `rating()` builds the
`Choice("1")…Choice(str(maximum))` set and maps the string keys back to `int`.

Two resolutions:

- **The event is a `ScaleEvent`, not a `ChoiceEvent`.** `Toggle` already sets the
  precedent: a control with a typed value hands its handler that value
  (`ToggleEvent.value`). Making rating authors write `int(event.selected[0])` would be the
  only typed control in the vocabulary that does not. `rating` owns the wrapping closure,
  so this costs one dataclass and no framework change.
- **Default labels are stars, explicit labels replace them.** Value *n* is labelled
  `"★" * n` when `maximum <= 5` (the button-row case) and `str(n)` above that, since a
  select of ten star strings is unreadable. A `labels` entry replaces the label for that
  value outright rather than decorating it: `TextLike` values may be deferred `Message`
  objects, which cannot be concatenated with a star prefix without going through `md()`,
  and half-localized labels are worse than either alternative.

Read-only averages are explicitly not this feature; `measure` and `progress` already
display them.

## Chrome

`Chrome` gains `not_now` (the default guard denial), `try_again_in(seconds)` (used when a
verdict carries `retry_after` and no reason; the default rounds up to whole seconds),
`working` (the default busy label), `review`, `finish`, and `unanswered`, all resolved by
`localize_chrome`.

## Considered, not done

- **`feedback=` on `Choices` and `FormTrigger`.** A select has no label to swap, so its
  interim would be "everything disabled" with no explanation; a form trigger's slow half
  is the submission, which arrives on a different interaction and would need
  `FormBinding` to carry the feedback. Both wait for a consumer.
- **Guards on routed actions.** No mount, no ledger, and 16's middleware onion is already
  the seam. A routed guard vocabulary would have to invent its own state store.
- **Guard state that outlives the mount.** A cooldown that survives a restart is a host
  concern with a host's storage; `GuardLedger` is deliberately in-memory and mount-scoped,
  and a host that needs durability writes a `Guard` over its own store.
- **Recording guard state on commit rather than admission.** See §1.
- **A `Rating` semantic node.** `Choices` with `maximum=1` already lowers to the row we
  want; a node would duplicate the whole selection-ownership story for one label rule.

## Landing order

`guards.py` and the ledger first (self-contained, no mount changes), then the mount's
admission stage and `sl.discord.guards`, then `Feedback` (touches the same dispatch
funnel, so it lands second to keep the two diffs readable), then `ScaleField`/`rating`
(independent), then wizard review (largest pattern change, depends on nothing above except
the new chrome).

## Verification

- `test_guards.py`: each built-in guard's admit/deny paths against a frozen clock;
  actor-versus-mount scoping; `key=` sharing one bucket across actions; `all_of`/`any_of`
  precedence and reported reason; `until` rejecting a naive deadline; sync and async
  `when` predicates.
- `test_mount.py`: a denied press writes an ephemeral reason, runs no handler, bumps no
  generation, and traces `GUARD_DENIED`; the chrome default and `try_again_in` wording; a
  raising guard reaches `on_error` and traces `GUARD_FAILED`; admission runs after the
  stale check under EXCLUSIVE and still runs under IMMEDIATE; a grouped action's select
  route is guarded; `FormTrigger` guards the opening press and not the submission.
- `test_mount.py` (feedback): a fast handler paints nothing; a slow one paints the pending
  label with every control disabled and never defers; a handler error restores the
  previous scene before `on_error`; `restore_on_error=False` leaves the interim; a
  no-change success restores; a late-waking watchdog paints nothing after `close()`.
- `test_wizard_pattern.py`: review entry from a final submit; `goto:`/`back`/`submit:`
  round trips keeping `reviewing`; Finish gated in both `transition` and the render;
  branch growth after a jumped edit; `summarize` override; default rows through
  `format_prefill`; routed shell parity (`WizardState` still serializes).
- `test_forms.py` / `test_form_discord.py`: `ScaleField` parse bounds, prefill, and label
  mapping; the radio-group shape at span ≤ 10 and the text-input shape above it.
- `test_factories.py`: `rating` option keys, star and explicit labels, and `ScaleEvent`
  delivery for controlled values.
- `test_public_api.py`: every new export. Run focused tests with `--no-cov`, then
  `just typecheck` and `git diff --check`.

## Status

Shipped framework-side: `guards.py`, `sl.discord.guards`, the mount's admission stage and
busy paint, `ScaleField`/`sl.rating`, and `Wizard(review=...)`. The bot consumers each
section names are follow-up migrations, as they were for batches A and B — nothing in
`squid/` has moved onto guards, `Feedback`, or wizard review yet.
