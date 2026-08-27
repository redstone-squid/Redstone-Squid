# 67 — Lifetime naming: verbs carry it, nouns owe consistency

## Problem

A pre-publication naming reset, with lifetime as the organising concept. The obvious design is
a closed suffix vocabulary where a type's last word says what ends it.

**The data refuses it.** Across `sl`, `sl.discord`, its twenty sub-namespaces, `squid_reactive`
and `squid_stores` there are **93 distinct class-name suffixes, 60 used exactly once**; only 15
recur three times or more. A closed table would have to reject `Component`, `Mount`, `Screen`,
`Destination`, `Composition`, `Target` and `Work`, or grow until it was not a table. Collapsing
to one word per lifetime class is worse — see [90](../../squid-ui-redesign/90-deferred.md).

## Decision

**Lifetime is carried by verbs, not nouns.** Verbs name events natively; nouns name things.
Six closed verbs (`close`, `detach`, `finish`, `cancel`, `discard`, `run`), and nouns get three
consistency rules that need no dictionary. Both are written up in
`../../../squid-layouts-architecture.md` under "Ownership and lifetime".

The verbs were in better shape than expected. A scan found only two classes with more than one
terminating verb and both are correct (`PersistedPool` has `run` and `close`;
`SubscriptionReconciler` has `discard` and `close`), because `run` and `discard` name subjects
other than the object itself. `Fragment.release` and `ActionParticipant.abort` sit outside the
set deliberately, and `Fragment.release`'s docstring already argued why.

## What the rules caught

**`squid_layouts.discord` exported two different public classes named `MountSnapshot`**:
`mount.MountSnapshot` is a diagnostics view of a live mount that dies immediately, and
`durability.MountSnapshot` is serialized state that outlives the process. `operations.py`
imported the first while sitting beside the second. Fixed by renaming the durability family to
`MountState`/`ComponentState`/`PresentationState` — its inner types were already
`CursorState`, `SelectionState`, `DisclosureState`, so only the containers were wrong.
`DurableMountState` became `SessionMountRecord`, since it is a state plus its position in a
session graph and sat beside `DurableMountRecord` doing the same job.

**`MemorySnapshotStore` contradicted its own body**: methods `list_records`/`load`, field
`_records`, holding `StoredSessionRecord`. Now `MemorySessionStore` and siblings.
`TopicBridgeSnapshot` keeps its name — `bridge.snapshot()` is a view of a running bridge.

Two things keep the old spelling because they are deployed, not source: the `"snapshot"` JSON
wire key and the `squid_layout_snapshots` table. Both say so at the line that writes them.

## Enforcement

`../../../../tests/architecture/test_naming.py`, alongside the existing pytest-archon boundary rules:

1. One exported name means one class, asserted as set equality against a documented list of
   deliberate parallels — a semantic node and the primitive it lowers to, the parallel settled
   states of resources and operations, `sl.forms.X` beside `sl.X`. A new collision fails and
   must be argued for. Same shape as plan 58's `__all__` contracts.
2. No class defines both `close` and `finish`.
3. No `shutdown`/`stop`/`dispose`/`teardown`/`destroy` — a denylist, so it scales to synonyms
   nobody has written yet.

Rules "name matches its members" and "identity ≠ authority" stay review checks. They found two
of the three defects here, but neither is testable without guessing, and a fuzzy architecture
test is worse than none.

## Open

Rule 1 found two collisions it does not fix, both recorded in the test:

- **`Destination`** — one option in a navigation control (`semantic`), and how a mount's message
  gets created (`discord.delivery`). Unrelated concepts.
- **`Progress`** — a progress bar (`semantic`), and the capability an operation reports through
  (`squid_reactive.operations`).

Both are authoring vocabulary, so renaming either is a public API decision with taste in it
rather than a mechanical fix. Settle before publishing.

## Status

Rules, renames and enforcement shipped 2026-08-24. The two `Destination`/`Progress` collisions
are open.
