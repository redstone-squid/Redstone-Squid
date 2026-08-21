# 10 — Consistent selection/disclosure ownership

**Shipped.** Landed as `layouts: distinguish an unset selection from an empty one`,
`layouts: make ownership a value instead of an inference`, and `bot: move the panels onto
sl.controlled`.

## Problem

Three overlapping mechanisms decided "what is selected/open", chosen per node type:

- `Choices` and `Navigation` were controlled-only: the author held the value and a
  required handler took the writes.
- `Items` treated `focused` as an override that beat `PresentationSession.selections`
  whenever it named a live entry.
- `Details` treated `open` as a seed the session took over on first toggle.

An author moving between them changed state-ownership models without noticing, and
nothing in the vocabulary said which they were getting.

It also left `Items` with a dead Back button: `back` cleared the session, but a static
`focused=` won again on the redraw, so a reader who opened an entry could not leave it.
No production code passed `focused=`, which is why nothing caught it.

## Design

Ownership is an explicit value, not an inference. Every stateful node takes an
`Ownership`:

- `sl.controlled(value, on_change)` — the author owns it. The value is authoritative on
  every render and the engine never touches the session.
- `sl.managed(initial)` — the engine owns it in the presentation session under the node's
  key. `initial` is a **seed**: it applies on a session miss and is ignored from then on.

`Choices` and `Navigation` gained the managed path they never had, so the rule holds for
all four nodes rather than for two with exceptions. `Items.focused` became `opened` and
`ItemDisplay.FOCUSED` became `OPENED`, since the node discloses one of N entries rather
than taking keyboard focus. `Items` and `Details` share one `OpenEvent[T]` — one of N
versus one of one.

`PresentationSession.selection` gained the `initial=` parameter `disclosure` already had.
Without it a key never written and a key the reader explicitly emptied are the same
value, and the seed would re-apply after Back — the trap that made the button dead.

**The presentation vocabulary did not change**, so no snapshot version moved. `Managed`
values persist through `PresentationSnapshot` under the framework's protocol and adapter
versions; `Controlled` values persist through the owning component's declared state under
the host's `component_version`. Both survive a restore — they just fail incompatible ones
under different version gates. Documented in the architecture doc rather than changed.

## Rejected: `None` as the ownership sentinel

The original plan proposed that an author-supplied value means controlled and `None` means
engine-managed. It cannot work. `Items.opened=None` **is** the list view — the node's
default state and where every drill-down returns — so a controlled author could never
express "nothing open" and would be pinned into a permanently-open entry.
`Details.open: bool | None` has no such collision (`False` ≠ `None`) and
`Choices.selected` had no `None` at all (`()` was already a legal controlled value). The
rule would have been uniform in spelling and different in meaning per node: the failure it
set out to kill. A sentinel meaning "unset" cannot also be a legal value.

Two of the original plan's premises were also stale: `DisclosureState` was never unwired
(`_details` both read and wrote it), and both ownership paths already survived a restore.

## Verification

`packages/squid-layouts/tests/test_ownership.py` covers both modes for all four nodes,
including the dead-Back-button regression: a managed seed opens an entry, Back leaves it,
and the seed does not re-apply on the next render.
