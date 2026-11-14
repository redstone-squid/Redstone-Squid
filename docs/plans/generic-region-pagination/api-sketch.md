# Shared planner API sketch

Status: working Phase 2 design. The declaration and correspondence types now exist in
`squid_ui.presentations`; public exports and planning integration remain in progress.
The `sl.*` examples below describe the intended integrated API. This sketch applies the agreed
policies in [the Phase 2 draft](02-composition.md), without fixing the solver algorithm.

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

Proposed defaults and interpretation:

- Every candidate has the same fidelity, preference, and identity rules. `full` is simply a
  descriptive name; position in the list does not make it the semantic reference. Lower preference
  values are favored among otherwise comparable admissible choices. The global tradeoff between
  fidelity, stability, preference, and page turns belongs to phase 3.
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

Limits apply to the chosen representation of the wrapped subtree. Every enclosing budget is
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

## Details to validate during implementation

1. How typed references and correspondence helpers carry semantic units through combined or split
   presentations of the explicit shared content source.
2. Which fidelity effects and reference helpers make common loss declarations concise and precise.
3. Whether the four target operations expose enough structure for contextual composition without
   encouraging another hidden backend search.
4. Where incremental measurement and native extension preparation fit without retaining stale
   bindings or losing the shared work-budget contract.

Validate these details against the table, shared-resource, and persistent-content examples in
the implementation sequence recorded in the Phase 2 plan. Concrete types and names may change
as those examples exercise the contracts. Full joint search, state integration, cache optimization, and remaining target coverage
continue in their assigned roadmap phases.
