# 32 — Controlled ledgers, grids, and agreement

## Status

Implemented on 2026-08-24. Refreshed against the shipped semantic planner, routed
controls, localized chrome, narrowed public API, and session membership before implementation.
This supersedes the original survey sketch; the four capabilities remain in scope, but
their APIs now follow the architecture that landed after the sketch was written.

## Design constraints

1. The host owns durable domain ledgers. Squid may validate, allocate, summarize, and
   render them, but it does not persist votes, roster membership, or routed approvals.
2. Framework wording is resolved at render time. A high-level factory must not capture
   `DEFAULT_CHROME`; nodes that need chrome lower through the planner, and components read
   `CHROME_CONTEXT` while rendering.
3. Actor identity comes only from `ActionEvent.actor` (or a routed interaction), never
   from component state or custom-id payloads.
4. Mounted and routed controls keep their actual guarantees. Mounted actions may adapt
   through the session strategy machinery. Routed actions cannot be folded into a select
   when each option has a different route id; `RoutedChoices` is the explicit one-route
   alternative.
5. The narrowed API policy applies. Semantic factories are root authoring verbs. Nominal
   feature types live in `sl.patterns`; Discord-exact construction lives in `sl.discord`.
   Roadmap provenance does not appear in module names.

## Module boundaries

- `patterns/roster.py`: immutable roster declarations, allocation, and verdicts.
- `patterns/tally.py`: the immutable tally declaration; `factories.py` owns the root factory.
- `patterns/agreement.py`: the actor-keyed mounted component.
- `semantic.py` / `factories.py`: the `Roster` and `Grid` semantic nodes and their root
  factories, because their lowering depends on active target capabilities or chrome.
- `discord/grids.py`: the exact Discord button-grid primitive factory.

There is no `demand.py`: “demand-driven” explains why this batch exists, not what any
runtime object means.

## Roster

Roster membership remains host-owned. `place_roster` is a pure, order-stable allocator;
`sl.roster` is a semantic rendering factory.

```python
@dataclass(frozen=True, slots=True)
class RosterSlot:
    key: str
    label: TextLike
    capacity: int | None = None
    tone: Tone = Tone.NEUTRAL

class RosterOverflow(StrEnum):
    REJECT = "reject"
    WAITLIST = "waitlist"

@dataclass(frozen=True, slots=True)
class RosterEntry:
    actor_id: str
    display: TextLike
    slot: str
    joined_at: datetime | None = None

class RosterStatus(StrEnum):
    SEATED = "seated"
    WAITLISTED = "waitlisted"
    REJECTED = "rejected"

@dataclass(frozen=True, slots=True)
class RosterPlacement:
    groups: tuple[RosterGroup, ...]
    waitlist: tuple[RosterEntry, ...]
    rejected: tuple[RosterEntry, ...]

    def status(self, actor_id: str) -> RosterStatus | None: ...

def place_roster(...) -> RosterPlacement: ...

def roster(
    placement: RosterPlacement,
    *,
    key: str,
    on_join: Callable[[SelectionEvent], Awaitable[None]] | None = None,
    routes: Mapping[str, str] | None = None,
    locked: bool = False,
    show_waitlist: bool = True,
) -> LayoutNode: ...
```

The allocator rejects unknown slots and duplicate actor rows instead of guessing that a
duplicate is a move history. Movement is a host ledger mutation, not a verdict a snapshot
allocator can infer. FIFO is by `joined_at`; missing timestamps retain input order after
timestamped entries. `REJECT` keeps rejected entries in the result so allocation never
silently drops domain data. Promotion is re-running the allocator after ledger removal.

`Roster` is semantic because slot counts, “full”, and “waitlist” use the active localized
chrome. Lowering emits one region per slot, spillable members, and a disabled control when
locked or full under `REJECT`. `on_join` and `routes` are mutually exclusive; routed mode
requires exactly one route per slot and intentionally remains exact routed buttons.

The `/layout lobby` example is migrated to use `place_roster` and `sl.roster` while
`Session.members` remains its domain ledger.

## Tally

`TallyOption` is immutable host-computed display data. `sl.tally` composes existing
`Progress` and `Choices` nodes; it is not a new semantic node or a pattern state machine.

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
    route_id: str | None = None,
    total: int | None = None,
    show_bars: bool = True,
) -> LayoutNode: ...
```

Mounted mode uses controlled `Choices`, inheriting buttons/select adaptation and emitting
one `SelectionEvent` key. Routed mode uses one `RoutedChoices` route, whose handler receives
the selected key; it does not pretend differently routed buttons can become one select.
With neither dispatch argument, tally is an inert display. Supplying both is an error.

`render_generic_poll` adopts the inert tally display when totals are visible, retaining
weighted totals and visible voter names in option labels. Hidden anonymous polls continue
to omit counts entirely.

## Grid

Grid has three deliberately separate surfaces.

1. `TableDisplay.MATRIX` renders a dense code-block matrix through the existing Table
   strategy axis. An explicit display is authoritative; `AUTO` continues to choose between
   tabular and records.
2. `sl.discord.button_grid(*cells, key, columns, on_pick)` returns exact `Row` primitives.
   It validates non-empty unique keys and positive columns at authoring time. Planning
   enforces the target's five-button row width and total component/row budgets. It never
   degrades or changes interaction shape.
3. `sl.grid(*cells, key, columns, on_pick, flexibility=...)` creates semantic `Grid`.
   Its strategy axis is `buttons → coordinate → paged_select`: exact rows when they fit;
   a text matrix plus one select of available coordinates; then a paged select when more
   than 25 available cells remain. Every rung emits the same `SelectionEvent` cell key.

`GridCell` carries `key`, `label`, `available`, and `tone`. `columns` is positive; cell
keys are unique. Coordinate labels are deterministic spreadsheet-style coordinates
(`A1`, `B1`, …), while submitted values remain stable cell keys. Strategy state uses a
versioned `discord.grid` adapter id and the existing session update path, so the first
successful plan is sticky. HTML needs no new primitive because lowering produces existing
code, row, and select scenes.

A board is added to the layout showcase. The submission position parser remains a named
future application: changing a modal text field into an interactive board would alter a
real workflow and is not implied by adding the reusable primitive.

## Agreement

Agreement is a mounted component because its transition is actor-keyed and the pure
`Pattern.transition` protocol intentionally has no actor parameter.

```python
@dataclass(frozen=True, slots=True)
class AgreementParticipant:
    actor_id: str
    display: TextLike

class Agreement(Component):
    approved: tuple[str, ...] = state((), persist=False)
    resolved: bool = state(default=False, persist=False)

    def __init__(
        self,
        prompt: ContentLike,
        participants: Sequence[AgreementParticipant],
        *,
        key: str = "agreement",
        require: int | Literal["all"] = "all",
        allow_withdraw: bool = True,
        on_resolve: Callable[[PressEvent, tuple[str, ...]], Awaitable[None]] | None = None,
    ) -> None: ...
```

Participant ids must be unique and the threshold must be reachable. Rendering reads active
chrome, shows supplied display text rather than raw ids, and exposes separate approve and
withdraw controls because one shared render cannot truthfully label a toggle for every
viewer. The handler validates membership as a frontend-neutral safety invariant; Discord
hosts should additionally mount with `sl.discord.Users(...)` so denial occurs before
dispatch. `ActionMode.EXCLUSIVE` serializes presses. `resolved` gates `on_resolve` exactly
once and disables both controls. Agreement state is intentionally ephemeral: when its mount
ends, the proposal ends. Routed approvals remain a host-owned ledger rendered separately.

## Chrome

`Chrome` gains `waitlist`, `full`, `slot_count`, `approve`, `withdraw`, and
`approved_count`. `localize_chrome` resolves every field. Roster consumes these during
semantic lowering; Agreement consumes them from `CHROME_CONTEXT` during component render.

## Landing and verification

1. Roster model + semantic lowering + lobby consumer — shipped in `6e210e01`.
2. Tally factory + generic poll consumer — shipped in `39161c5a`.
3. Table matrix + semantic and exact variadic grids + showcase — shipped in `f3305aa5`.
4. Agreement component — shipped in `733f7493`.
5. Public API, README, architecture, and completion/deferred records — shipped with this status update.

Each slice gets focused tests and a reviewable commit. Final validation runs all new tests
together, affected bot tests, `git diff --check`, changed-file Ruff, and project Pyrefly
against the recorded 307-error baseline. The full suite remains CI-owned unless a slice
shows broader planner fallout.
