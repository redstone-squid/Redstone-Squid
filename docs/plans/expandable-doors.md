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

1. **Storage and historical ingestion (implemented):** preserve source claims,
   partial domains, parameters, formula branches, and evidence through inference,
   draft persistence, build finalization, and history. Do not require a solver.
2. **Arithmetic feasibility spike (partly implemented):** one-variable smallest
   regions now cover exact crossovers, periodic domains, rounded formulas, fixed
   exceptions, bounded work, and isolated subprocess execution. Multi-axis
   regions and timing-based titles remain open before computation schemas.
3. **Concrete evaluation and live point lookup (implemented):** compare supported configurations
   through the existing ranking semantics, without materializing query sizes.
4. **Symbolic regions and publication:** compute regions, prove nonemptiness
   where possible and persist snapshots. Live width-range queries are exposed;
   publication remains pending.
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

## Storage implementation

Migration `f2d5a8c1e4b7` adds `doors.expansion` and
`submission_drafts.door_expansion`. Both are nullable JSONB with document-version
checks. `DoorExpansion` in `squid/builds/domain/expansion.py` validates the source
schema; SQLAlchemy converts immutable typed values to JSON and back.

Inference carries the document through `BuildDraftPayload`. Materializing an
inference candidate retains it beside submission form answers, so old pinned
form manifests cannot discard it. Preparation includes it in door details;
finalization payload version 6 preserves it for the worker. Version 7 also retains
categorized restriction proposals alongside the document. Build history captures
the document on creation and edits. Existing documents and finalization payloads
remain readable. The build API exposes `door.expansion` for reads and patches;
omitting it preserves the value, and null clears it. Dedicated UI is deferred.

Malformed inferred bounds remain unknown instead of rejecting other builds.
Evidence and raw descriptions are checked against the candidate's primary
messages; generated summaries are replaced with source excerpts.

For example, a partial source claim can be ingested as:

```json
{
  "schema_version": 1,
  "parameters": [
    {"name": "n", "meaning": "opening width", "minimum": 2,
     "maximum_kind": "unbounded", "initial_steps": [], "repeating_steps": [2]}
  ],
  "branches": [
    {"opening_width": "n", "opening_height": "3", "opening_depth": "1",
     "volume": "40+20n", "claimed_record_domain": "5 < n <= 8 or n > 15"}
  ]
}
```

This stores the claim without evaluating it. The default maximum_kind is
`unknown`, not `unbounded`. Omitting step fields means unknown increments;
`initial_steps: [1]` with `repeating_steps: [2]` differs from an empty initial
sequence with `repeating_steps: [1, 2]`. Branch `domain` retains arbitrary source
constraints, including coupled variables. `evidence` contains message IDs and
excerpts, and `raw_description` retains qualifications or unsupported syntax.

The fixed-size record loader excludes doors with an expansion document until
point evaluation is integrated into record queries and publication. Sample dimensions are retained but cannot accidentally
establish a family record. No existing descriptions or expandable tags are
backfilled automatically; those need source reprocessing or explicit review.

## Normalizer and concrete evaluator

`normalize_expansion` and `evaluate_expansion` in
`squid/builds/domain/expansion_evaluation.py` compile source documents and evaluate
individual opening sizes. They are pure domain operations: they do not mutate
source facts, query the database, create record definitions, or publish holders.
No solver dependency is required for this stage.

```python
from squid.builds.domain.expansion_evaluation import normalize_expansion, evaluate_expansion

normalized = normalize_expansion(door.expansion)  # Require a non-null document first.
result = evaluate_expansion(normalized, (6, 3, 1))
for configuration in result.configurations:
    # Each source branch remains an independent construction choice.
    print(configuration.branch_index, configuration.status, configuration.volume)
```

For the source example above, the 6×3 configuration has volume 160. A 7×3 request
is outside the declared two-layer progression. Each branch returns `supported`,
`outside_domain`, or `unresolved`. Supported membership does not guarantee known
metrics: volume and individual timing slots can remain unknown with diagnostics.
Top-level diagnostics cover absent branches or an unsupported parameter namespace.
Normalization also retains branch diagnostics independently of point membership.

Supported arithmetic consists of up to three declared ASCII parameters, exact
integer/decimal constants, addition, subtraction, multiplication, division by a
nonzero constant, powers zero through three, and `floor(...)`, `ceil(...)`, or `ceiling(...)`.
Rounding uses exact rational values, including nested calls and negative inputs;
for example, `floor(-3/2) = -2` and `ceil(-3/2) = -1`.
Common implicit products such as `20n` and `(x+3)(y+3)` are accepted. An undeclared `xy` is not guessed to mean `x*y`.
Polynomial parts use canonical sparse rational coefficients. Explicit rounding
and arithmetic around it remain typed expression nodes; unit conversion scales
the result without moving rounding inside it. These formulas also work in domain
comparisons and modulo tests. Limits are 512 source characters, 128 tokens, 192 AST nodes, degree three at intermediate steps,
64 terms per polynomial part, and 256-bit polynomial coefficients. Point dimensions
are positive integers of at most 256 bits. Unsupported syntax remains in the source document.

All three opening expressions must be known, and enough independent affine
opening equations must determine the declared parameters. Gaussian elimination
recovers module counts without enumerating sizes; the remaining dimensions are
then checked exactly, including rounded expressions. For example, width=ceil(n/2)
and height=n can be evaluated; width=floor(n/2) with fixed height/depth cannot
uniquely recover n this way. Mappings lacking sufficient affine equations remain
unresolved, even when another inversion method could determine their parameters.
Integer-coefficient polynomials inside floor/ceiling simplify without rounding.
Consistency checks handle coupled openings such as n×n and reject noninteger
parameter values.

Domains accept chained comparisons, `and`/`or`/`not`, semicolon conjunctions, and
positive integer modulo tests, for example `n % 2 = 0`. Raw prose such as `odd n`
is diagnosed rather than interpreted. Structured bounds and layer progressions
remain authoritative: absent steps or an unknown upper bound are not filled in
from assumptions. Known exclusions still reject sizes, and a declared minimum
establishes the sequence's first size even if later sizes remain unknown.

Volume must evaluate to a positive integer. Timings are converted exactly to game
ticks (seconds ×20, redstone ticks ×2); nonintegral results remain unknown rather
than being rounded. Negative reset timings are allowed. Unknown units, unsupported
formulas, and duplicate slots within a named timing variant produce diagnostics.
A sample's fixed timings are never consulted. Source-claimed record domains and
completion text do not become eligibility constraints or verified completion dates.

The result retains separate alternatives, so callers cannot silently combine the
smallest branch's volume with the fastest branch's timing. Record integration must
check membership, retain unresolved competitors, and apply existing ranking rules
with explicit completion evidence. The live lookup below applies those rules;
published record runs still exclude families until snapshot integration is ready.

## Live point lookup

`RecordService.lookup_door` and anonymous `POST /v1/records/lookup/door` compare
current confirmed builds at one opening. For example:

```json
{"opening": [9999, 3, 1], "base_title": "smallest", "orientation": "Door"}
```

All five base titles are supported. Optional `type_ids` match the complete pattern
set; the empty set matches builds without explicit pattern tags. `restriction_ids`
select a subset. Parameterized restrictions also need `restriction_values`, for
example `[[7, "at_most", "4"]]` with tag 7 in `restriction_ids`. Thresholds that
cannot match the tag's value type do not qualify. `version_id` restricts candidates
to builds assigned that Minecraft version; omitting it compares all versions.

The loader reads fixed doors at that size and expandable families in a fresh,
read-only PostgreSQL repeatable-read transaction. It releases the transaction
before normalization and ranking. Queries create no category, computation run,
queue entry, or persistent cache. Sizes fit positive PostgreSQL integer coordinates;
this includes 9999×3 without enumerating intermediate sizes.

Results expose build-level holders, local configuration IDs, evaluated metrics,
branch indices, parameter assignments, and diagnostics. Alternatives retain their
own volume and timing variants. Unproven membership conservatively blocks an
official result; proven exclusions do not compete. Missing primary measurements
follow the existing exclusion rule, while missing decisive tie-breakers produce
unresolved results. If tied alternatives all belong to one build, the build can
hold the record even when its exact winning branch remains provisional.

Family configurations do not inherit sample measurements or completion dates.
Until verified per-configuration completion evidence is modeled, families cannot
win `first`, and completion ties across builds remain unresolved. Source completion
text remains unverified.

These results are live evaluations, not published records. Immutable candidate
snapshots, caching by snapshot, symbolic winning regions, and published point
views remain part of the publication stage below. Discord/UI integration is also
pending; the API and application service are the current entry points.

## Common coordinates and formula branches

Compare competitors in opening width, height, and depth coordinates. A fixed
6×3 is a singleton domain; n×3 fixes height and depth; n×n adds width=height.
Module-count parameters are translated into opening coordinates before ranking.
Each branch must map an opening size unambiguously back to its parameters;
multiple construction variants must be explicit branches.

A computational branch contains a domain predicate, volume formula, timing
variants, and completion evidence. Ordinary expansion has one branch. Periodic
phases become congruence guards, with initial exceptional sizes represented
separately. Timing at one example must not silently become constant family timing.

Start the arithmetic spike with degree-three polynomials over exact rational
coefficients. Bound expression size, coefficient size, and branch count; decide
limits using the spike. Rational floor/ceiling nodes must be lowered into
guarded polynomial phases before symbolic interval solving; retain the exact
formula when a bounded phase expansion cannot finish. Validate integral positive volume on supported integer
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

## One-variable smallest regions

`smallest_record_regions` in `squid/records/domain/regions.py` is the first pure
region compiler. Its inputs are configurations already expressed in one shared
integer opening coordinate, with verified domains represented as unions of
bounded or unbounded progressions. Fixed doors use singleton domains. Unknown
domains produce `unsupported`; an empty domain means proven exclusion.

The compiler returns disjoint progressions covering the requested interval,
including no-candidate and unresolved regions. Each progression's minimum is a
nonempty witness, checked against the original volume formula and the existing
smallest-record resolver. Completion ties and alternative branches retain the
same build-level behavior as live lookup. Invalid or unknown primary volumes
are excluded according to the existing rules.

For example, compare volumes `n^3+1000` and
`n^3+1000+(n-5)(n-9)(n-15)` on positive integer openings. The first wins at
6–8 and from 16 onward; 5, 9, and 15 are ties requiring completion evidence.
Adding a smaller fixed door at n=7 creates a singleton exception.

`region_math.py` uses rational finite differences to split cubic integer
sequences into monotone runs, then binary search to locate sign changes.
A coefficient bound closes the unbounded tail. Floor/ceiling expressions,
including nested rounding, become polynomial formulas on residue classes;
nonintegral measurements remain excluded. This subset needs no SymPy or Z3
dependency. The proposed adapters above remain options for the broader spike.

Defaults bound candidate configurations to 32, domain pieces per configuration
to 128, combined residue classes to 128, output regions to 4096, and arithmetic
steps to 50,000. Input bounds and polynomial coefficients use at most 256 bits.
Exhausting a limit returns `limit_exceeded` with no partial awards. These are
deterministic work limits, supplemented by the process limits below.

Only smallest records in one varying coordinate are supported. Existing concrete
lookup continues to handle all five titles and supported multivariate configurations.

### Stored documents and live width queries

`analyze_door_widths` in `squid/records/application/expansion_regions.py` now
translates stored expansion documents into compiler inputs. At a fixed opening
height and depth, each affine inverse becomes a polynomial in width. This also
supports width slices of multidirectional families and coupled openings.
Substitution preserves floor/ceiling operations and source branch identity.

The adapter partitions wherever opening consistency, parameter integrality,
bounds, initial steps, repeating steps, or parsed domain comparisons can change.
Boolean predicates and modulo constraints retain the point evaluator's behavior,
including short-circuiting around invalid modulo operands. It checks membership
and volume with the original point evaluator at each partition witness. An
unknown domain blocks the query with `unsupported`; a provable exclusion does
not. A narrow query can therefore succeed when a broader query reaches unknown
sizes. Source-claimed record domains and completion text remain unverified.

`RecordService.lookup_door_ranges(DoorRangeQuery(height=3))` reads current
confirmed families and fixed doors, filters orientation, taxonomy and version,
and returns smallest-record regions. Optional `minimum` and
`maximum` bound the query. The repository retains typed opening dimensions
for fixed-size exceptions and reads related facts in a read-only repeatable-read
transaction. The adapter and compiler share one arithmetic work allowance.
No query creates categories, runs, queue entries or stored region results.

### Public range queries and process limits

`POST /v1/records/lookup/door/ranges` accepts a `DoorRangeQuery`:

```json
{"axis": "width", "height": 3, "depth": 1, "minimum": 6, "maximum": null,
 "base_title": "smallest"}
```

Bounds are inclusive; a null maximum means an unbounded query. Category and
version filters match concrete lookup. The response includes the query,
evaluation time, analysis status, regions, and configuration-to-build/branch
mappings. Region domains retain residue classes when periodic construction or
rounding changes winners. Only width queries for smallest records are currently
accepted. No query creates one record per opening size.

The service admits at most two concurrent range requests per application process,
including database loading. Saturation returns HTTP 503 without queueing or
loading candidates. After the transaction closes, each request starts a fresh
child process with only arithmetic facts sent as JSON. The child receives no
database handle or inherited integration credentials. Default limits are 15
seconds elapsed, 10 seconds CPU, 512 MiB address space, 2 MiB input, and 4 MiB
output. POSIX resource limits are required; unsupported hosts fail closed.

Cancellation and timeout terminate and reap the child. Unsupported arithmetic,
resource exhaustion, and elapsed timeout return `unsupported`, `limit_exceeded`,
or `timed_out`, respectively, with no partially awarded regions. Worker failures
return HTTP 503. Exact analysis may still contain unresolved record regions when
comparison facts are missing. Point queries remain independently available.

These are live, ephemeral answers. Publication still requires immutable candidate
snapshots, persisted region results, and atomic activation; live responses do not
replace published record runs.

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
