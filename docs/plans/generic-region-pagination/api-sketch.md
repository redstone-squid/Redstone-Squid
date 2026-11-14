# Shared planner API sketch

Status: implemented phases 2 and 3. Peer alternatives are exported from `squid_ui`; named
resource budgets remain in `squid_ui.presentations` while the legacy budget API is migrated.
Use `plan(..., composition=CompositionOptions(automatic=True))` for joint representation and page
search, or omit `automatic` for fixed choices. The [phase 3 handoff](03-joint-search.md) records
implemented behavior and remaining production routes.

## Authoring content and alternatives

Keep the semantic authoring model: tables contain rows and columns, sections contain related
content, and controls describe interaction behavior. Built-in lossless realizations are available
automatically. Authors can constrain them or express preferences without enumerating each target's
widgets. Existing APIs may change where the new model calls for it.

Use one entry point for authored alternatives, with the same shape for every candidate. All
presentations declare fidelity and preference relative to shared semantic meaning. The first
candidate has no special authority. Shared authored content supplies the semantic reference,
independently of which presentation is selected:

```python
content = sl.stack(
    sl.paragraph("Length: 10 blocks; width: 5 blocks", key="dimensions"),
    sl.paragraph("Valid only in Java edition", key="edition"),
)
full = content  # One candidate directly presents the shared semantic content.
compact = sl.stack(
    sl.paragraph("10 × 5 blocks", key="dimensions"),
    sl.paragraph("Java edition only", key="edition"),
)

summary = sl.alternatives(
    sl.alternative(
        full,
        key="full",
        fidelity=sl.equivalent(),
        preference=0,
    ),
    sl.alternative(
        compact,
        key="compact",
        fidelity=sl.equivalent(),
        preference=10,
    ),
    sl.alternative(
        sl.paragraph("10 × 5 blocks", key="dimensions"),
        key="dimensions-only",
        fidelity=sl.losses(sl.omit("edition")),
        preference=20,
    ),
    content=content,
    key="build-summary",
)
```

Defaults and interpretation:

- Every candidate has the same fidelity, preference, and identity rules. `full` is simply a
  descriptive name; position in the list does not make it the semantic reference. Lower preference
  values are favored among otherwise comparable admissible choices. Automatic comparison is specified
  by the [phase 3 plan](03-joint-search.md#preference-and-fidelity), including ordinal preference ranks
  and fair compromise between regions. Automatic composition implements this policy; fixed composition
  evaluates the supplied choices without ranking them.
- An alternative must explicitly declare its fidelity. Omitting a declaration does not silently
  assert equivalence. Supplying `losses(...)` authorizes those specific effects in non-strict mode,
  subject to inherited hard requirements. It does not authorize unrelated loss elsewhere. Strict
  planning rejects a lossy alternative even when explicitly supplied.
- `equivalent()` is the author's assertion that content, relationships, and behavior are preserved.
  Structural checks can validate identities and associations, but cannot prove arbitrary prose
  equivalence. Built-in transformations must establish their preservation contracts through tests.
- `omit("edition")` refers to a unit in the shared semantic contract, scoped to `build-summary`.
  Unknown or ambiguous references are errors. Loss declarations describe changes rather than
  assigning a generic quality score. `shorten(source, max_chars=..., keep="head" | "tail")` and
  `change_interaction(source, attribute=...)` identify concrete text and behavior changes.
- An alternative's own key identifies the choice. Content keys identify semantic units across
  choices. Switching from `full` to `compact` must not make `dimensions` a new logical item.

The shared semantic reference is independent of candidate order and selection. For built-in
representations, the authored semantic node already supplies it. Custom alternative groups supply
that content explicitly, using the existing semantic vocabulary. A presentation may directly reuse
the content node, as `full` does here, without becoming privileged among the candidates.
`content=` establishes the reference; it is not rendered alongside the selected alternative.
Display and resource accounting follow the selected presentation and persistent surrounding content.

Helpers should carry semantic references automatically when presenting shared content. Reusing
semantic units and their identities permits structural checks; combining or splitting them requires
an explicit correspondence. An arbitrary paraphrase still relies on an author assertion of
preservation. The core records that distinction and rejects known omissions even when a candidate
claims equivalence. It does not try to prove natural-language meaning from text similarity.


### Source-referenced text

Use structured fragments when a text transformation needs machine-checked provenance. Literal
connective text is separate from source references; arbitrary authored prose still relies on an
author assertion. For example:

```python
operation = sl.shorten("description", max_chars=100)
compact = sl.present_text(operation, " · ", sl.ref("edition"), key="summary")
choice = sl.alternative(
    compact,
    key="compact",
    fidelity=sl.losses(operation),
)
```

A `ref` fragment retains the source unit's full structured text. A `shorten` fragment performs the
declared operation after localization and records the retained source range in Unicode code
points. Its loss declaration must match the operation. The presentation's correspondence follows
from these fragments; authors do not repeat a separate mapping.

A field has separate `label` and `value` units, so `ref("width", "value")` cannot claim to preserve
its label. References must identify one text unit; a whole table or control cannot be flattened
implicitly. Full references preserve links and other rich text. Current shortening handles plain
and styled inline text; unsupported rich behavior rejects that candidate explicitly. More inline
semantics must be modeled before expanding those transformations.

The structural check records which presentations depend on authored equivalence. It cannot certify
that a manually rewritten paragraph is a character-limited version of a source unit. Layout
requirements and strict admission remain responsibilities of contextual composition evaluation.

## Regions and identity

A region owns a position and author-defined persistent content. Its content can contain other
regions or alternatives. The existing shape remains a useful starting point:

```python
sl.region(
    summary,
    build_table,
    key="browser",
    header=(sl.heading("Build browser"),),
    footer=(close_action,),
    page=sl.uncontrolled(0),
)
```

Root and local pagination will use the same planning machinery. Author APIs for budgets, break
preferences, and ownership can evolve during migration; they must not create separate solvers.
Current key arguments remain the authoring mechanism for identity, without an extra identity wrapper.
Author budgets name the resources they limit. The prototype is available separately from
production `sl.budget`:

```python
from squid_ui.planning.resources import Axis
from squid_ui.presentations import budget

budget(content, limits={Axis.DISPLAY_TEXT: 100, Axis.COMPONENTS: 20})
```

Each axis follows the target's protocol counting rules. In V2, `DISPLAY_TEXT` counts serialized
text, including Markdown and link destinations; `COMPONENTS` counts physical components including
containers; `ATTACHMENTS` counts distinct attached assets. Slack currently exposes `BLOCKS`.
HTML currently exposes no protocol resource axes. Requesting an unsupported axis rejects the
candidate rather than treating its cost as zero. Field-specific limits, such as Slack section
text, remain target constraints and are not interchangeable with a message-wide text budget.

Limits apply to the chosen representation of the wrapped subtree. Budgets are structural
boundaries: an alternative may rearrange content inside one, but replacing a larger region must
preserve its inner boundaries with equal or tighter limits. It cannot merge budgeted content with
outside content into one widget. Moving the budget outward explicitly gives the larger region a
shared limit. Boundaries do not require visible containers. Every enclosing budget is
checked, and shared resources count once within each scope even when siblings also use them.
Platform limits always apply: a larger author limit cannot relax them. A budget does not authorize
shortening or omission. Allocation preferences and visual-length preferences are separate future
API work; this prototype has no bare `max`, `min`, `prefer`, or `stretch` parameters.
Production `sl.budget` will migrate with the planner entry point.

Separate semantic identity, occurrence identity, representation choice, and page position in the
planning model. Occurrences carry inherited policies and placement context. Representation changes
retain semantic identity where meaning is retained. Combining several semantic units into one
visual element will require an explicit correspondence; a single rendered key cannot stand in for
all of them. The first implementation step will establish typed references and correspondence
helpers against these cases; the ownership and preservation rules are already settled.

## Information the core retains

Start from the authored semantic graph and derive planning occurrences and choices. Preserve source
relationships throughout exploration rather than replacing a table with strings before page choices
are known. This sketch does not require a second public content tree.

| Planning concept | Information retained |
| --- | --- |
| Semantic occurrence | Authored node, scoped semantic identity, occurrence identity, inherited policies, relationships, and ownership. |
| Realization offer | Applicable source, representation identity, preserved semantic units and relationships, permitted breaks and continuation rules, fidelity effects, preference, and target payload. |
| Candidate composition | Selected realizations and fragments, container context, repeated content, navigation, interactions, and resource dependencies. |
| Evaluation | Feasibility, measured resource use, identified shared resources, contextual constraint results, resolved effects, and work consumed. |
| Selected plan | Chosen composition and page structure, resolved positions, visible interactions, diagnostics, and proposed state updates. |

A target payload can remain opaque to the core. The information needed to select, compose, or
fragment it cannot. Atomic native widgets expose their constraints, resources, and effects and
remain indivisible; additional adaptation requires additional semantic information.

## Target participation

A possible contract has four operations. These signatures describe responsibilities, not final
Python types or package locations:

```python
class TargetRules[PayloadT, PreparationT, BodyT](Protocol):
    def offers(
        self, occurrence: SemanticOccurrence, context: PlanningContext
    ) -> Iterable[RealizationOffer[PayloadT]]: ...

    def evaluate(self, candidate: CandidateComposition[PayloadT], context: PlanningContext) -> Evaluation: ...

    def build(self, preparation: PreparationT, context: PlanningContext) -> BodyT: ...

    def validate(self, body: BodyT, expected: Evaluation) -> None: ...
```

`offers` exposes target-supported ways to realize semantic content and the constraints attached
to them. The core retains the choices. An offer must expose children, legal breaks, or other
structure needed for the transformations it supports; it cannot be only a prebuilt scene and cost.

`evaluate` measures a concrete composition in context. It may resolve costs and specific effects
of transformations the candidate already chose. It must not silently choose more transformations,
truncate content, or run an independent fitting search. An infeasible candidate returns structured
constraint results so the core can explore other choices.

A feasible evaluation includes a typed immutable preparation. `build` consumes that preparation
without reinterpreting the candidate or serializing its content again. The core assembles the surrounding plan result,
including resources, bindings, diagnostics, and proposed presentation updates. `validate` checks
that the built body honors the selected constraints and measured contract. A mismatch is an
implementation error; it is not permission for a final trimming pass.

Planning context supplies the target, localization, relevant presentation inputs, and a shared work
budget. Offers and evaluation can run repeatedly for rejected candidates. They must not commit
state, invoke application change handlers, or fetch more data. Existing route encoders must remain
safe to repeat. Building the selected result does not publish it; delivery remains the frontend's
responsibility.

The sketch deliberately allows context-dependent measurement. Exact incremental cost summaries,
safe pruning rules, and the search strategy require the later algorithm design. A successful
evaluation of one visible page is not proof that every reachable page combination fits.

## Resource composition

Retain different kinds of contribution rather than flattening everything into a cost vector:

- Additive use where the target establishes that addition is valid.
- Identified shared resources, such as an attachment referenced by multiple visible controls.
- Context-dependent structure, such as container overhead, local nesting restrictions, and
  repeated headings or navigation on continuation pages.

The core keeps these dependencies and chooses the composition. Target rules supply the precise
accounting and constraints for that composition. Two references to asset X contribute one shared
attachment; references to X and Y contribute two. Equal standalone counts cannot justify pruning
one of those candidates in place of the other.

Whether these facts use declarative constraints, target evaluation functions, or both remains to
be established by the first working examples. A single `cost: ResourceCost` is not the full contract.

## Walkthroughs

For a table, the core retains rows, columns, keys, and relationships. Shared semantic transformations
and target rules expose legal presentations, such as a native table or labeled records. Evaluation
includes row limits, headings, fragment boundaries, and navigation. The core can choose a lossless
partition or another admissible presentation. The final target builder cannot slice away extra rows.

For shared downloads, composition retains the asset identity behind both references. Evaluation
accounts for the attachment once and charges the visible controls separately. Region boundaries
do not change resource identity. Different page combinations may have different shared-resource
sets, which the solver must account for when certifying the whole page structure.

For HTML, target rules can expose an unbounded full-content realization. Explicit authored paging
and constraints still apply, but the core does not invent a global message budget. Slack receives
the same semantic information directly and contributes its own realizations and constraints.

## Evidence and remaining integration

1. How typed references and correspondence helpers carry semantic units through combined or split
   presentations of the explicit shared content source.
2. Which fidelity effects and reference helpers make common loss declarations concise and precise.
3. Whether the four target operations expose enough structure for contextual composition without
   encouraging another hidden backend search.
4. Where incremental measurement and native extension preparation fit without retaining stale
   bindings or losing the shared work-budget contract.

The phase 2 composition fixtures and phase 3 search oracles exercise these boundaries. State integration,
cache optimization, and remaining target coverage continue in their assigned roadmap phases.

## Public planning entry point

```python
from squid_ui.planning import CompositionOptions, OccurrenceId

planned = sl.planning.plan(
    summary,
    target=sl.html.target(),
    strict=True,
    composition=CompositionOptions({OccurrenceId(("root", "build-summary")): "compact"}),
)
```

The document occurrence is `("root",)`. Children append their semantic key, or a positional key
such as `"@0"` when unkeyed. An alternative's selected presentation appends `"@presentation"`;
its choice key is separate. In fixed mode, a sole offer is automatic; multiple offers require an explicit
selection. Candidate rejection raises `CompositionRejectedError`, carrying structured violations.
It does not retry, paginate, or claim that the document is unsolvable. Cache and memo reuse are
bypassed until their shared-core integration.

For custom rules, pass `FixedComposition(rules, selections)` instead. Opaque semantic leaves
subclass `AtomicNode`; their offers carry `AtomicContract` costs, shared identities, capabilities,
and legal placements. Use `occurrence.scope_effects(node.effects)` to retain source-scoped effects.
Evaluation must report scoped resource usage, and validation must independently check the prepared
native output. Richer internal adaptation requires semantic structure; an arbitrary payload is not
permission to discover children or shorten its contents.


## Automatic composition and stable pages

```python
from squid_ui.planning import AllocationGoal, CompositionOptions, OccurrenceId, RegionPolicy
from squid_ui.planning.resources import Axis

region = OccurrenceId(("root", "results"))
options = CompositionOptions(
    automatic=True,
    policies={region: RegionPolicy(goals=(AllocationGoal(Axis.DISPLAY_TEXT, 200),))},
    work_limit=50_000,
)
result = plan(document, target=target, composition=options)
second_page = result.for_page({region: 1})
```

`work_limit` covers automatic semantic traversal, discovery, enumeration, every reachable page's
certification, and output preparation. Fixed mode continues to use the request's `search_budget`.
`pages` selects initial zero-based coordinates, `selections` pins offer keys, and `partitions` pins
legal exclusive page ends. Pinning leaves the same contextual evaluation and validation in place.
Use `AutomaticComposition(rules, options)` for custom typed rules.

The result carries immutable region cuts, source anchors, navigation inputs, and a request-local
allocation identity. `for_page` selects an already prepared scene with its visible resources and
bindings, retaining that identity and search diagnostics. It performs no fitting or state publication.
`report.search` distinguishes completion, unsupported branches, and exhaustion; `metrics` separates
work, generated states, target evaluations, and certified combinations. An exhausted search returns
its best certified allocation when available, or raises `JointExhaustedError`. A completed impossible
domain raises `JointInfeasibleError`; unsupported domains retain `JointUnsupportedError`.

Targets expose fragment units and repeated sources with `FragmentContract`, flow boundaries with
`RealizationOffer.flow`, and page chrome with `RegionContract`. `OfferDiscovery` distinguishes complete,
unsupported, and interrupted enumeration. The [phase 3 implementation](03-joint-search.md) specifies
the comparison policy, correctness bounds, and limitations. Mounted page ownership, cursor
reconciliation, and delivery-time publication still belong to phase 4.
