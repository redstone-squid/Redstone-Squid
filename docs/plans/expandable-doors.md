# Expandable doors: storage and record architecture

## Objective and rollout

Preserve expandable-door facts from historical messages before implementing
record computation or editing screens. A door family can expand along several
axes, have finite or unbounded domains, and use periodic construction phases.
For example, an x×y×z opening can have cumulative wiring volume
`(x+3)*(y+3)*(z+1)`. Support increments of two, alternating increments of one
and two, and an initial increment followed by a repeating sequence.

An n×3 family must compete in regular 6×3 records when it supports n=6.
Store winning regions such as `5 < n <= 8 or n > 15`, rather than materializing
one record for every size. Fixed builds can create exceptions in unrestricted
size records, but cannot enter categories that require expandability.

Implementation order:

1. **Storage and historical ingestion (current work):** preserve source claims,
   partial domains, parameters, formula branches, and evidence through inference,
   draft persistence, build finalization, and history. Do not require a solver.
2. **Arithmetic feasibility spike:** validate exact one-variable crossovers,
   periodic domains, three-axis comparisons, fixed-build exceptions, missing
   timings, and timeouts before committing to computation schemas.
3. **Concrete evaluation and point lookup:** compare supported configurations
   through the existing ranking semantics, without materializing query sizes.
4. **Symbolic regions and publication:** compute regions, prove nonemptiness
   where possible, persist snapshots, and expose results.
5. **UI and ingestion review:** explain domains, source claims, unresolved facts,
   and winning regions; add dedicated editing controls.

## Source facts and storage

Source claims and validated computational inputs are different artifacts.
Historical messages can omit bounds, variable meanings, or timings, or contain
formulas outside the future calculator's grammar. Retain those claims verbatim
with message evidence; missing information must remain unknown. A source's
claimed record-breaking domain is evidence, not an official computed result.

Store an optional versioned expansion document on each door. Use a dedicated
JSONB column and typed application schema: this is build data, not miscellaneous
extra_info or a tag. The document contains parameters with explicit meanings,
opening expressions, domain claims, optional structured bounds/step sequences,
formula branches, timings with units, and source evidence. Partial documents
are allowed. The existing fixed dimensions describe a measured example only;
never insert a variable into an integer field or substitute a minimum size.

Retain source syntax rather than interpreting algebra during ingestion. Later
normalization produces canonical coefficients and predicates, retaining the
source expression alongside them. Do not store SymPy or Z3 objects or pickles.
Unknown bounds must be distinguishable from explicitly unbounded domains.
Documents must survive saved inference drafts, finalization, reloads, edits,
and build history. Old rows and payloads default to no expansion document.
Legacy expandable tags without formulas remain incomplete evidence.

Until evaluation is implemented, structured families must not be treated as
fixed-size record candidates using their sample measurements.

## Common coordinates and formula branches

Compare competitors in opening width, height, and depth coordinates. A fixed
6×3 is a singleton domain; n×3 fixes height and depth; n×n adds width=height.
Module-count parameters are translated into opening coordinates before ranking.
Each branch must map an opening size unambiguously back to its parameters;
multiple construction variants must be explicit branches.

A computational branch contains a domain predicate, volume polynomial, timing
variants, and completion evidence. Ordinary expansion has one branch. Periodic
phases become congruence guards, with initial exceptional sizes represented
separately. Timing at one example must not silently become constant family timing.

Start the arithmetic spike with degree-three polynomials over exact rational
coefficients. Bound expression size, coefficient size, and branch count; decide
limits using the spike. Validate integral positive volume on supported integer
sizes. Allow unknown measurements and preserve the existing timing-slot rules,
including negative reset timings where permitted.

## Exact evaluator and symbolic compiler

Use the existing concrete record resolver as the reference for ranking:
minimum volume, timing priority, slowest timing variants, completion ties, and
decisive missing evidence. Define ranking operations interpretable both over
concrete values and symbolic conditions. Combined titles retain their pool-first
semantics; do not replace them with a generic pairwise metric comparison.

Regions use a typed expression graph with And/Or/Not, polynomial comparisons,
integer bounds/equalities, and congruences. Share repeated subexpressions and
avoid eager conversion into every possible combination of disjoint regions.
A predicate can be exact without its nonemptiness having been established.
Award a claim that a family wins somewhere only after establishing an integer
witness or otherwise proving nonemptiness. Check witnesses through the concrete
resolver. Missing evidence and solver limitations are separate statuses.

Proposed adapters:

- Our domain code owns polynomial representation, membership, evaluation, and
  ranking semantics.
- SymPy isolates univariate roots exactly, yielding sign intervals that can be
  intersected with integer and periodic domains.
- Z3 checks integer feasibility and searches for witnesses with resource limits.
  Unknown/timeouts remain explicit; nonlinear integer solving is not guaranteed.
- Our formatter renders intervals and periodic constraints, retaining readable
  symbolic predicates when simplification cannot finish.

ISL is not initially needed: linear integer-set manipulation alone does not
handle nonlinear volume boundaries. The spike must verify dependency support
for the repository's Python version before adoption.

## Queries, persistence, and workers

Introduce typed competition identities for orientation, patterns, restrictions,
and version scope. Opening sizes become constraints, not strings embedded in
competition identities. Keep existing fixed-title URLs as point views.

A 9999×3 lookup reads the active candidate snapshot, tests membership, evaluates
formulas, and invokes the concrete resolver. It neither creates a permanent
category nor triggers recomputation. Cache by snapshot, competition, and size.

Reuse the computation queue and atomic run activation. Extend persistence with
immutable candidate snapshots, winning and gap predicates, nonemptiness status,
optional witnesses, and simplified representations. Record calculator and
arithmetic-library versions. A family can be resolved in one region and
unresolved elsewhere, so one TitleResult status is insufficient.

Run algebra in a supervised subprocess with memory/time limits, outside the
bot process and database transaction. Initially recompute existing queued scopes;
optimize by competition group and input hashes only after measurement. Never
combine a published region with live edited candidate formulas.

## Validation

Storage tests must exercise multidirectional and coupled openings, both layer
patterns, finite/unbounded/unknown bounds, phase-dependent formulas, ambiguous
raw expressions, source evidence, saved draft compatibility, and database/history
round trips. Ingestion must retain unsupported syntax instead of dropping a build.

Later, property tests must compare symbolic region membership with the concrete
resolver at each tested size. Include crossover endpoints, ties, periodic holes,
finite limits, fixed exceptions, missing timings, and very large point queries.
A symbolic timeout must leave concrete point lookup available.

## References

- Local rules: [door-rules.md](../door-rules.md), sections 2.1.1 and 4.2.
- [SymPy exact polynomial roots](https://docs.sympy.org/latest/guides/solving/find-roots-polynomial.html).
- [Z3 arithmetic and nonlinear limitations](https://microsoft.github.io/z3guide/docs/theories/Arithmetic/).
- [ISL integer-set library](https://libisl.sourceforge.io/).
