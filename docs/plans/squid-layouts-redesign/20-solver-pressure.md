# 20 — Solver pressure: glue budgets, break control, region pagination

## Problem

- All size pressure is target-derived. An author cannot say "this section should not
  dominate the message" — there is no aesthetic cap, only Discord's.
- `Paginate` attaches to five text primitives; option windows and root packing are
  separate purpose-built paths; nothing pages a region of heterogeneous children.
- Splitting is greedy first-fit: 1040 characters over a 1000 cap gives 1000/40 — the
  pagination orphan.
- No break vocabulary: nothing stops a heading landing as the last child on a page.

## Design

> A size is glue — minimum, preferred, stretch — and a break point carries a cost.

Prior art, adopted deliberately: TeX's glue/badness/penalties and Knuth–Plass breaking;
CSS Fragmentation's `break-inside`/`break-after`/`orphans`/`widows` names; AutoLayout's
dual priorities and priority *levels* — the last converging independently on
`search.py`'s no-scalar-weights lexicographic stance.

1. **`sl.budget(node, min=…, prefer=…, stretch=…)`**, an `Adaptation`-family wrapper.
   `min ≤ prefer` validated at construction; units are characters, because that is
   what the solver measures.

2. **Ceiling semantics.** Trip point = `min(prefer + stretch, solver-assigned budget)`.
   At or under it: nothing happens — the hysteresis that stops the 1000/40 split. Over
   it: the node's ordinary overflow policy fires at `prefer`, snapped to the nearest
   boundary within the band. A deterministic pre-pass; no new `CostVector` tier.

3. **Floor semantics.** `min` is a reservation siblings' stretch cannot eat.
   Collectively unsatisfiable floors raise `UnsolvableLayoutError` unless the region
   sits under `sl.best_effort` — the same permission model as truncation. Rejected:
   floors as distribution hints only ("min" wearing a hard name, off-brand here), and
   TeX-style proportional breach (degrades unless someone reads the report).

4. **Break annotations land *with* region pagination, not after**: `sl.unbreakable`
   (atomic under paging), `sl.keep_with_next` (a heading is never a page's last
   child), min-fill/widow knobs on `Paginate`. Without `keep_with_next`, the first
   paged `Section` ships a stranded-heading bug on day one.

5. **Balanced breaking.** Within one region, break points are chosen by dynamic
   programming over demerits (Knuth–Plass; trivial at Discord scales) rather than
   greedy fill — 520/520, not 1000/40. The scalar stays local: a deterministic
   sub-decision inside one breaker, never crossing into the global lexicographic
   search, which is TeX's own layering (badness inside a paragraph, pages decided
   separately). `Paginate`'s contract must not promise full pages, so balancing can
   land later without changing page identity.

6. **Region pagination**: page boundaries fall between the children of a keyed
   container; children are atomic (a `Field` never splits) except text children, which
   nest their own splitting; the fingerprint hashes child identity. `PageBroker` is
   unchanged — plan 06's single cursor lifecycle was built for exactly this; the
   region slicer asks `grant`, cuts, `record`s. Interactive children may differ per
   page (precedent: `Items` and `Details` already change component counts on toggle).

7. **`sl.paged(section, key=…, chars=…)`** is sugar: the budget wrapper plus
   region-granular `Paginate`.

8. **Solver policy, agreed explicitly: the implementation may be ugly; the contracts
   may not.** Land these as expedient special cases, keep the author declarations and
   the observable identity of pages (keys, fingerprints, sameness across renders)
   stable, log the warts, and run one global redesign pass once all requirements are
   visible. Expected convergence: every node's size is a ladder of candidate
   renderings × lexicographic cost — `Field.fallbacks` and `Variants` are already
   discrete shrink rungs of that model, and continuous stretch is a ladder with fine
   rungs over a small range.

## Staging

A: budgets over existing text pagination (independently useful — no region work
needed). B: region breaking plus break annotations, together. C: the sugar.

## Verification

- Hysteresis: 1040 chars under `prefer=1000, stretch=150` renders whole.
- Band snap: an over-band split lands on a boundary inside the band.
- Floor breach raises unless `best_effort`; `min > prefer` rejected at construction.
- `keep_with_next` holds a heading to its content across a page break.
- Balanced split produces near-equal pages; page identity stable across renders.
- The existing pagination suite passes unchanged after each stage.

## Status

Agreed 2026-08-21 (design session); not started.
