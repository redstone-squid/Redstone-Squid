# 10 — Consistent selection/disclosure ownership

## Problem

Three overlapping mechanisms decide "what is selected/open", chosen per node type:

- `Choices` is a controlled component: the author stores selection in their own state
  (`Choices.selected`) and wires `on_change` back to it (`semantic.py:283-291`).
- `Items` focus is presentation-managed via `PresentationSession.selections`
  (`planning/adaptation.py:322-329` reads `context.session.selection(node.key)`), with
  `Items.focused` as an author override.
- `Details.open` is an author field while `DisclosureState` also exists in the closed
  presentation vocabulary (`runtime/presentation.py:26-28`).

An author moving from `Choices` to `Items` changes state-ownership models without
noticing. Durable snapshots serialize the presentation vocabulary, so which mechanism a
node uses silently decides whether its selection survives a restore.

## Design

One default: **controlled everywhere, presentation as fallback for un-wired nodes** —
matching `Choices`, the majority pattern.

1. Document the rule in the architecture doc: an author-supplied value
   (`selected=`/`focused=`/`open=`) always wins and the author owns updates via the
   node's event; when the author passes `None`, the engine manages the value in the
   presentation session under the node's key.
2. `Items`: change `focused: str | None` semantics to match — `None` means
   engine-managed (today's behavior), a value means fully controlled (today the session
   can override a stale author value; stop that: author value wins unconditionally).
   Add `on_focus` so controlled users receive changes; engine-managed remains available
   for the "just let readers flip through" case.
3. `Details`: same split — `open: bool | None = None`. `None` → engine-managed via
   `DisclosureState` (which today has no writer in adaptation: either wire it or delete
   it — audit first; if nothing toggles disclosure at runtime, drop `DisclosureState`
   from the vocabulary and the durability codec instead of keeping a dead state kind).
4. Snapshot compatibility: any presentation-vocabulary change bumps the relevant
   version so `SnapshotError` fires instead of silent misrestore
   (`discord/durability`).

Small, mostly documentation plus two node-semantics adjustments; schedule alongside or
after plan 04 since both touch adaptation lowering.

## Verification

- `test_semantic_structures.py`: controlled `Items.focused` wins over a stale session
  value; `None` keeps session behavior; `on_focus` fires.
- `test_durability.py`: snapshot round-trip across the vocabulary change fails loudly
  with the version bump, restores cleanly at the new version.
- Audit result for `DisclosureState` recorded in the commit message (wired or deleted).
