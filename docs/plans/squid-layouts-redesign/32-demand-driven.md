# 32 — Demand-driven patterns (Batch D)

## Goal

The last batch of the survey roadmap: rosters, tallies, grids, and multi-party agreement.
These were originally "wait for demand"; the demand call has been made, so they get full
designs — but they stay the most honest about thin consumers, and the most careful about
what the framework refuses to own.

Three of the four share one design problem, so its rules come first and each feature cites
them rather than re-deciding.

## 1. The controlled-ledger rules

Rosters, tallies, and approvals all render a **ledger**: membership, counts, or consents
keyed by actor. The rules:

1. **The host owns and persists the ledger.** The framework renders it and computes pure
   verdicts (placement, totals, resolution); it never stores votes, seats, or approvals as
   framework state, because a ledger in component state dies with the mount and a ledger
   in route parameters is stale by design.
2. **Every ledger panel ships in two forms.** Mounted — session `Action` handlers for a
   live panel — and routed — `routed_action` per control (plans [14](14-routed-actions.md)/
   [16](16-routed-actions-part-two.md)) for mass-posted, restart-surviving cards. The
   factory takes `on_*` handlers or a `routes` mapping; supplying both is an error.
3. **Actor identity comes from the event** — `ActionEvent.actor` or the route interaction
   — never from state. The renderer may mark "your" entries only because the host told it
   whose view it is rendering.

## 2. Roster

A roster is slots with capacities, members, and overflow. Apollo, Raid-Helper, and
ReadyRaider converge on the same machine — alternate statuses, per-slot capacity, FIFO
waitlist — and none of it is event-specific: lobbies, shifts, and reservations are the
same shape. Under rule 1 there is no honest pattern state, so `Roster` is a pure placement
helper plus a factory preset, not a `Pattern`.

```python
@dataclass(frozen=True, slots=True)
class RosterSlot:
    key: str
    label: TextLike
    capacity: int | None = None
    tone: Tone = Tone.NEUTRAL

class Overflow(StrEnum):
    REJECT = "reject"
    WAITLIST = "waitlist"

@dataclass(frozen=True, slots=True)
class RosterEntry:
    actor_id: str
    display: TextLike
    slot: str
    joined_at: datetime | None = None

class RosterVerdict(StrEnum):
    SEATED = "seated"
    WAITLISTED = "waitlisted"
    FULL = "full"
    MOVED = "moved"

@dataclass(frozen=True, slots=True)
class Placement:
    seated: Mapping[str, tuple[RosterEntry, ...]]
    waitlist: tuple[RosterEntry, ...]

    def verdict(self, actor_id: str, slot: str) -> RosterVerdict: ...

def place(
    entries: Sequence[RosterEntry],
    slots: Sequence[RosterSlot],
    *,
    overflow: Overflow = Overflow.WAITLIST,
) -> Placement: ...

def roster(
    slots: Sequence[RosterSlot],
    placement: Placement,
    *,
    key: str,
    on_join: Callable[[SelectionEvent], Awaitable[None]] | None = None,
    routes: Mapping[str, str] | None = None,      # slot key → route_id
    locked: bool = False,
    show_waitlist: bool = True,
) -> LayoutNode: ...
```

Two names diverge from the sketch deliberately: `RosterState` would collide with the
series-wide convention that `XxxState` names a pattern's state dataclass, so the slot
declaration is `RosterSlot`; and a bare `sl.WAITLIST` constant becomes
`Overflow.WAITLIST`, matching the enum-housed constants everywhere else.

`place` is pure and order-stable: seats fill FIFO by `joined_at` (ties broken by input
order), overflow waitlists in join order, and promotion on leave falls out of re-running
`place` on the updated ledger — the framework never mutates membership. Membership is
single-slot in v1: joining another slot moves the actor, and `verdict` reports `MOVED` so
the host can word it.

The factory renders one section per slot — label, a `chrome.slot_count` count, a
spillable member list, and a join control disabled when `locked` or when full under
`REJECT` — plus the waitlist section. `joined_at` and deadline lines render through 29's
`Timestamp`; signup close is 31's `until` guard on the join actions. Those two
dependencies are why Roster sits in this batch despite its survey rank.

Plan [34](34-safe-session-runtime.md)'s session `participants` are operational identity;
the roster ledger is domain data. Related, not the same — but a lobby built on 34's
participant phase renders naturally through `roster`, and this pairing is a candidate for
the worked multi-user example 34's final phase requires.

Consumers: none in the bot today, stated honestly under the productization standard — the
consumer is the library user, and `squid/bot/layout_showcase.py` hosts a raid-signup
worked example.

## 3. Tally

A tally is options with counts and the reader's own stance. Under rule 1 it is a factory
preset; a `Pattern` with a `TallyState` is rejected outright — counts in custom ids are
stale by design, and a state that holds nothing is not a state machine.

```python
@dataclass(frozen=True, slots=True)
class TallyOption:
    key: str
    label: TextLike
    count: int
    mine: bool = False
    emoji: str | None = None

def tally(
    options: Sequence[TallyOption],
    *,
    key: str,
    on_vote: Callable[[SelectionEvent], Awaitable[None]] | None = None,
    routes: Mapping[str, str] | None = None,      # option key → route_id
    total: int | None = None,
    show_bars: bool = True,
) -> LayoutNode: ...
```

Each option renders as a control labelled `label · count` (`mine` raises emphasis) with an
optional `progress` bar of `count/total`. The factory emits `sl.actions`/`sl.choices`, so
the ≤5-buttons-else-select adaptation is inherited rather than reimplemented. The host
records the ballot and re-renders — for routed cards, through the existing poll
reconciler.

Consumers: `generic_poll_text` in `squid/bot/voting/rendering.py` (hand-rolled counts and
mentions today), `poll_controls` in `squid/bot/voting/controls.py` (the routed-card
precedent this sits beside), and the reaction-vote migration in `squid/bot/reactions.py`.

## 4. Grid

This promotes plan [90](90-deferred.md)'s recorded grid/matrix entry and makes the
frontend boundary explicit: `sl.grid(...)` is semantic and may degrade, while
`sl.discord.button_grid(...)` is an exact Discord primitive that never silently changes
interaction shape.

1. **`TableDisplay.MATRIX`** — a new strategy on the existing `Table` axis: a dense
   code-block grid for content matrices (calendars, availability, comparisons). No new
   node.
2. **`sl.discord.button_grid(cells, *, key, columns)`** — a Discord-specific factory
   desugaring to exact button rows. It enforces Discord's five-column action-row geometry,
   preserves one stable cell key per button, and fails planning when the requested shape
   cannot fit. It does not degrade to a select because callers choosing this API have
   explicitly chosen the exact interaction shape.
3. **Semantic `Grid`** and its `sl.grid(...)` factory — the degradation-ladder promotion:

```python
@dataclass(frozen=True, slots=True)
class GridCell:
    key: str
    label: TextLike
    available: bool = True
    tone: Tone = Tone.NEUTRAL

@dataclass(frozen=True, slots=True)
class Grid:
    key: str
    columns: int
    cells: tuple[GridCell, ...]
    on_pick: Callable[[SelectionEvent], Awaitable[None]]
    flexibility: Flexibility = Flexibility.NORMAL
```

`sl.grid(...)` is the frontend-neutral authoring path. `sl.discord.button_grid(...)` may
be used when a Discord-native board is required, but it is not part of the semantic
ladder and has no HTML equivalent.

The strategy ladder is BUTTONS → COORDINATE (text grid plus one coordinate select, listing
only available cells) → PAGED_SELECT, nominated through the existing strategy machinery
and session-sticky through `StrategyState`. Every rung delivers the same
`SelectionEvent` carrying the cell key — a button press and a select pick are
indistinguishable to the handler, which is what makes the ladder honest. HTML needs
nothing new: the scene already carries rows, selects, and code blocks.

Consumers are thin and named honestly: position picking around `_parse_position` in
`squid/bot/submission/schematics.py`, and a `layout_showcase.py` board demo.

## 5. Agreement

N participants each confirm; the action commits when a threshold is met. Pokétwo trades,
staff dual-approval, deployment sign-off. This is the most speculative feature in the
series (~60% at survey time), and the design is deliberately minimal.

The layer decision is the sharp one. `Pattern.transition` is actor-blind — the protocol
passes an action name and values, not an actor — so an actor-keyed transition cannot be a
pure pattern without changing the protocol. Extending `Pattern` with an actor parameter is
rejected (a breaking change to every pattern for one feature); smuggling the actor through
`values` is rejected (a typed lie). `Agreement` is therefore a `Component` — a justified
exception with a *different* justification than the async-loading one: actor-keyed
transitions fall outside the pure-pattern contract.

```python
class Agreement(Component):
    approved: tuple[str, ...] = state(())
    resolved: bool = state(default=False)

    def __init__(
        self,
        prompt: ContentLike,
        participants: Collection[str],
        *,
        key: str = "agreement",
        require: int | Literal["all"] = "all",
        allow_withdraw: bool = True,
        on_resolve: Callable[[PressEvent, tuple[str, ...]], Awaitable[None]] | None = None,
    ) -> None: ...
```

Mounted-only in v1, and access control is 34's, not a new mechanism: the host mounts with
`access=sl.discord.Users(participant_ids)`, so a non-participant press is denied before
any state changes. Approve/withdraw toggles the pressing actor's entry; `EXCLUSIVE`
serializes concurrent presses through the existing action lock, so there is no new
concurrency model — the doc says so explicitly. `on_resolve` fires exactly once, guarded
by `resolved`. Render: the prompt, one status line per participant (name plus tick), the
`chrome.approved_count` threshold line, and the approve/withdraw controls.

Routed, restart-surviving approvals — the mass-posted trade card — are considered and not
done: that is a host-owned ledger rendered through the rules in §1, one paragraph, no new
machinery. Component state is the v1 ledger exception rule 1 permits because an agreement
is ephemeral by nature: if the panel dies, the proposal died with it.

Agreement plus Roster are the candidates for 34's required worked multi-user example; when
that example lands, 90's participant-tracking entry finally closes on 34's terms.

Consumers (prospective, kept at the survey's confidence): redstoner-role decision flows
(`squid/bot/give_redstoner.py`) and staff dual-approval in claims review.

## Considered, not done

- **A polling backend.** The framework renders tallies; persistence, authorization, and
  aggregation stay host-side. This is rule 1, restated because polls are where it is most
  tempting to break.
- **Multi-slot roster membership.** `verdict` and `place` extend naturally; wait for a
  consumer.
- **A framework participant model.** Plan 34 §B owns participant lifecycle; nothing here
  preempts it.
- **Routed Agreement.** See §5; the ledger rules already describe it.

## Chrome

`Chrome` gains `waitlist`, `full`, `slot_count(count, capacity)`, `approve`, `withdraw`,
and `approved_count(count, total)`, all resolved by `localize_chrome`.

## Landing order

Ledger rules with the first consumer, then Tally (smallest), Roster, Grid, and Agreement
last — it needs nothing new but is the most speculative.

## Verification

- `test_roster.py`: `place` seats FIFO under capacity, waitlists overflow in join order,
  promotes on removal, verdicts `FULL` under `REJECT` and `MOVED` on slot change; the
  factory renders counts, waitlist, locked, and full states; the routed form emits one
  `routed_action` per slot; handler-and-routes together raise.
- `test_tally.py`: counts, bars, and `mine` emphasis; the ≤5 adaptation is inherited
  (asserted, not reimplemented); routed round-trip.
- `test_grid.py`: `MATRIX` is nominated and session-sticky; `sl.discord.button_grid` emits
  exact five-column rows, preserves cell keys, and fails plans over budget; every semantic
  ladder rung delivers the same `SelectionEvent` key; the coordinate rung lists only
  available cells; strategy choice remains session-sticky after the first successful plan.
- `test_agreement.py`: per-actor approve and withdraw; threshold and `"all"` resolution;
  `on_resolve` fires exactly once; a non-participant press is denied by the access layer
  before any state change; `strict_state` clean.
- `test_public_api.py`: every new export. Run focused tests with `--no-cov`, then
  `just typecheck` and `git diff --check`.
