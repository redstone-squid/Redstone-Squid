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

class Guard(Protocol):
    async def admit(self, event: ActionEvent, ledger: GuardLedger) -> GuardVerdict: ...

class GuardScope(StrEnum):
    ACTOR = "actor"
    MOUNT = "mount"

# sl.guards
def cooldown(seconds: float, *, per: GuardScope = GuardScope.ACTOR, key: str | None = None) -> Guard: ...
def when(predicate: Callable[[ActionEvent], bool | Awaitable[bool]], *, reason: TextLike) -> Guard: ...
def permission(check: Callable[[ActionEvent], Awaitable[bool]], *, reason: TextLike | None = None) -> Guard: ...
def once(*, per: GuardScope = GuardScope.ACTOR) -> Guard: ...
def rate_limit(count: int, per_seconds: float, *, per: GuardScope = GuardScope.ACTOR) -> Guard: ...
def until(deadline: datetime, *, reason: TextLike | None = None) -> Guard: ...
def all_of(*guards: Guard) -> Guard: ...    # first denial wins
def any_of(*guards: Guard) -> Guard: ...    # first admission wins; the last denial is reported
```

`semantic.Action` and `FormTrigger` gain `guard: Guard | None = None`, threaded through
`sl.action`/`sl.form` into `ActionBinding` and checked by the mount.

The design decisions, in order of sharpness:

1. **Admission runs after the concurrency policy gate, inside the EXCLUSIVE lock, before
   the transaction.** Inside the lock so `when(...)` closures read consistent component
   state; before the transaction so a denial writes nothing, bumps no generation, and
   costs one ephemeral message. Pre-lock admission is rejected as racy; opening a
   transaction merely to deny is rejected as waste.
2. **Rejection is a private notice, not a redraw.** The denial responds ephemerally with
   the verdict's reason (or the chrome default), the same shape as the access denial in
   `_begin_dispatch`. Guards never affect rendering: `available=` remains the render-time
   tool, and the doc states why — a cooldown cannot disable a button it cannot re-render
   on a timer. An author who wants both disables via `available=` *and* guards the press.
3. **Stateful guards keep their state in a mount-owned `GuardLedger`, not on the guard.**
   Factories recreate guard objects every render, so `cooldown`/`once`/`rate_limit`
   compute a ledger key (default: action key + guard kind + actor when actor-scoped) and
   read/write the ledger. The ledger lives and dies with the mount.
4. **Guard denial precedes any busy interim.** A denied press never flashes "Working…".

`requires_role` is deliberately not portable — roles are a Discord fact, and the portable
core stays frontend-neutral (the same reasoning as plan [90](90-deferred.md)'s "portable
permission facts" entry, which this partially supersedes: the portable surface is
`Guard`/`GuardVerdict`, while frontend facts enter through plan 02's native access). It
ships as `sl.discord.guards.requires_role(role_id, *, reason=None)`, sugar over
`permission`. Its relation to 34's `Check` access policy: `Check` gates the mount,
`requires_role` gates one action.

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
2. **Threshold, not immediate, folded into the watchdog.** For feedback-bearing actions
   the existing acknowledgement watchdog becomes two-stage: at `pending_after` (default
   1.0s, mount-configurable) it performs the interim edit via the interaction response —
   which *is* the acknowledgement, so the deferral at `acknowledgement_timeout` never
   fires. Fast handlers never flicker. Actions without `feedback` keep today's behavior
   byte for byte.
3. **No persistent success state; failure restores.** Success chrome is the post-action
   flush itself. On handler error the mount must re-edit the original scene back (when
   `restore_on_error`) before `handle_error` runs — the current no-flush error path would
   otherwise strand the panel on "Working…" with every control dead. Transient success
   ticks ("Saved ✓" for two seconds) are considered and not done: they need timers the
   framework refuses to own, and `event.notice(...)` already covers the need manually.

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
work unchanged. New transitions: `review` enters the review screen (a final submit lands
there automatically instead of setting `complete` when review is enabled); `goto:<step>`
jumps to a step while keeping `reviewing=True` as the return anchor; a `submit:`/`next`
while reviewing returns to review instead of marching forward. Review renders `summarize`
or the default — one row per live step, its answer summarized through each field's
`format_prefill`, with an Edit (`goto:`) action — and Finish, available only when every
live form step has an answer. `transition` re-checks that completeness on `finish`; the
state machine enforces, the render merely reflects.

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
    value: Ownership[int | None, ChoiceEvent] = Managed(None),
    labels: Mapping[int, TextValue] | None = None,
) -> Choices: ...
```

The short alias is `Scale`. In the Discord modal adapter a span of ten or fewer renders as
a radio group (the `ChoiceField` shape); a larger span renders as a text input parsed like
`IntField` — an honest portable fallback needing no capability. `rating()` builds the
`Choice("1")…Choice(str(maximum))` set with star/label text and maps the string keys to
`int` for a `Controlled` value. Read-only averages are explicitly not this feature;
`measure` and `progress` already display them.

Consumers are thin today — suggestion/feedback flows and record quality scoring — and the
doc says so plainly: the placement in this batch is justified by cost, not by consumer
pressure.

## Considered, not done

- **Guards on routed actions** — plan 16's middleware is the routed seam; revisit only if
  a routed consumer needs the portable vocabulary specifically.
- **Framework-owned cooldown redraws** (re-enabling a disabled button when a cooldown
  expires) — requires timers per control; the notice-on-press model needs none.
- **Transient success feedback** — timers again; `event.notice` covers it.
- **A dedicated Rating node** — `Choices` adaptation already produces the row; promote
  only if a frontend needs rating-specific semantics (e.g. half-stars).

## Chrome

`Chrome` gains `not_allowed`, `try_again_in(seconds)`, `already_used`, `closed`,
`working`, `review`, `not_answered`, and `finish`, all resolved by `localize_chrome`.

## Landing order

Guards before busy: both extend the same dispatch funnel, and guard-precedes-busy is a
stated invariant, so the guard seam must exist when the interim lands. Wizard review and
`ScaleField`/`rating` are independent of both and of each other.

## Verification

- `test_guards.py`: a denial produces a private notice, runs no handler, opens no
  transaction, bumps no generation; cooldown admits after expiry and reports
  `retry_after` through `chrome.try_again_in`; `once` and `rate_limit` honor actor vs
  mount scope; `until` denies past the deadline; `all_of` short-circuits in order;
  `any_of` reports the last denial; an async `when` receives the event; the ledger
  survives across dispatches on one mount and is absent on a fresh mount; a guard on
  `FormTrigger` blocks modal presentation.
- `test_busy.py` (mount-level, via `fake_interaction`/`commit_render`): a slow handler
  gets an interim edit with the pending label and every control disabled; a fast handler
  gets none; the error path restores the original scene and then reaches `handle_error`;
  an action without feedback still defers at `acknowledgement_timeout` (regression); a
  denied press produces no interim.
- `test_wizard_pattern.py`: a final submit lands on review; `goto` plus resubmit returns
  to review; branch growth marks new steps unanswered and gates Finish; `reviewing`
  survives route serialization; the default summary uses `format_prefill`.
- `test_forms.py` / `test_form_discord.py` / `test_factories.py`: `ScaleField` bounds,
  labels, and parsing; radio at span ≤ 10 vs text input above; `rating()` shape and the
  `Controlled` int mapping.
- `test_public_api.py`: every new export. Run focused tests with `--no-cov`, then
  `just typecheck` and `git diff --check`.
