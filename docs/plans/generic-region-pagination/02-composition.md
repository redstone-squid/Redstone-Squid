# Shared composition and target participation

Status: design discussion in progress. This draft records decisions for the Phase 2 plan;
implementation steps and concrete interfaces are not settled. The [API sketch](api-sketch.md)
proposes author syntax, planning concepts, and target participation for review.

Establish the first usable boundary between the rich semantic planning core and target-specific
realizations. The [Phase 1 handoff](01-shared-core.md#phase-2-handoff) supplies the requirements,
and the [composition experiments](composition-experiments.md) supply initial acceptance cases.
The joint allocation algorithm and ranking policy remain Phase 3 work.

## Agreed design choices

### Redesign author APIs and migrate callers together

Existing signatures for regions, pagination, and alternatives do not constrain the new model.
Choose coherent APIs and migrate callers with the corresponding changes. Compatibility adapters
are not a requirement. Migration still needs to preserve the agreed content, interaction, and
delivery guarantees and identify intentional behavioral changes.

### Use one alternatives API

Alternatives carry explicit fidelity and preference metadata. Fidelity describes preservation
of content, relationships, and behavior; preference ranks permitted choices. Strictness and hard
requirements determine admissibility independently of presentation preference.

The exact metadata types, default values, and syntax remain to be designed. An authored declaration
of equivalence is not proof that arbitrary prose has the same meaning; the core must distinguish
what it can validate structurally from what the author declares.

### Permit atomic extensions with declared planning behavior

Custom or target-native widgets may remain atomic when they expose their constraints, resources,
and effects. The core can place and account for such widgets without inspecting target payloads.
Adaptation requires richer semantics: the core must not invent internal breaks or transformations
from an opaque payload. Target adapters cannot perform undisclosed fitting after selection.

### Offer built-in lossless alternatives by default

Semantic widgets and targets supply lossless alternatives without requiring authors to enumerate
every representation. Authors can constrain the permitted representations and express preferences
among them. Automatic adaptation still respects hard requirements, content relationships, and
interaction behavior; a different visual shape alone is not evidence that a transformation is safe.

### Declare specific losses with concise helpers

Author-supplied lossy alternatives describe the particular content or behavior changes they allow.
Examples include omitted fields, shortened text, and changed interaction behavior. Concise helpers
should make common declarations practical while exposing structured effects to shared planning.
Coarse labels such as summarized or partial are not sufficient substitutes for those declarations.

The concrete effect vocabulary and how declarations refer to semantic units remain design work.
Author preference ranks permitted alternatives; it does not replace describing what an alternative
changes or authorize undeclared loss.

## Remaining design work

Define how the authored semantic graph relates to planning occurrences and selected realizations;
how targets offer and measure candidates; and how composition retains shared resource identities,
container context, continuation requirements, and interaction bindings. Establish the failure and
work-budget contracts, then plan reviewable implementation steps and caller migrations.

Validate the first working path with V2 and Slack, plus HTML as an unbounded case. Use tables,
alternatives, shared attachments, and persistent container/navigation costs to test the boundary.
The roadmap's later phases remain responsible for completing search, state integration, reuse,
and target coverage.
