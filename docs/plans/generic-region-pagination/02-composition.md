# Shared composition and target participation

Status: design discussion in progress. This draft records decisions for the Phase 2 plan;
implementation steps and concrete interfaces are not settled.

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

## Questions currently under discussion

- Whether semantic widgets and targets supply built-in lossless alternatives automatically, with
  authors able to constrain them, or whether all alternatives must be listed by the author.
- How specifically authors must describe loss in non-equivalent alternatives, and how concise
  declarations relate to the effects the core needs for validation and comparison.

## Remaining design work

Define how the authored semantic graph relates to planning occurrences and selected realizations;
how targets offer and measure candidates; and how composition retains shared resource identities,
container context, continuation requirements, and interaction bindings. Establish the failure and
work-budget contracts, then plan reviewable implementation steps and caller migrations.

Validate the first working path with V2 and Slack, plus HTML as an unbounded case. Use tables,
alternatives, shared attachments, and persistent container/navigation costs to test the boundary.
The roadmap's later phases remain responsible for completing search, state integration, reuse,
and target coverage.
