# Readable presentation controls

Proposal with a static typing spike; no runtime API has been implemented.

Declare a control once, let the runtime update its value, and read that value
elsewhere in the UI without an assignment callback:

```python
class Reports(sl.Component):
    work_lost = sl.presentation.toggle("Work lost only")

    def render(self):
        return (
            sl.action_controls(self.work_lost.bind, key="filters"),
            self.render_reports(only_work_lost=self.work_lost.value),
        )
```

Keep `sl.state` for component-owned data, `controlled(value, callback)` for
application-managed changes, and `uncontrolled(initial)` for inline, key-owned
presentation state. Declared presentation controls acknowledge interactions,
update their cells, and invalidate readers without application change handlers.

## Descriptors and binding types

`sl.presentation` is a callable factory object with named control factories.
Factories return descriptors; class access returns the descriptor and instance
access returns a handle. Handles have read-only `.value` and `.bind` properties.
Neither the handle nor the value is a proxy for the other. Assigning to the field
or `.value` is forbidden; explicit write/reset APIs are outside this plan.

| Declaration | `.value` | `.bind` |
| --- | --- | --- |
| `presentation(initial)` | Inferred value type | `ValueBinding[ValueT]` |
| `presentation(factory=...)` | Factory's return type | `ValueBinding[ValueT]` |
| `presentation.toggle(label)` | `bool` | Callable subclass of `Toggle[object]` |
| `presentation.choices(first, *rest)` | `tuple[str, ...]` | Callable subclass of `Choices[object]` |
| `presentation.choices()` | `tuple[str, ...]` | Builder requiring choice entries |

One `PresentationField[HandleT]` descriptor implements overloaded `__get__`
methods returning itself on class access and `HandleT` on instance access. Its
`__set__` accepts `Never`. Each specialized handle retains its exact binding
type without introducing an application context or callback event type.

Value bindings must be invariant: a control that writes `int` cannot accept a
cell restricted to `bool`. The spike includes the internal commit operation so
variance inference sees both reads and writes. Read-only references may be
covariant. Event reads use a nominal reference base, so arbitrary objects with a
`.value` property do not accidentally satisfy the contract.

A generic `__call__` infers the value type from `initial` or `factory`. Optional
or empty custom values may need an explicit field annotation. Earlier sketches
of `presentation[ValueT](...)` are not the proposed syntax: generic function
calls do not support that subscription. Supply exactly one of initial or factory.
Factories create independent defaults per placement; shared defaults are for
values treated as immutable. Initialization must respect candidate isolation.

For a complete control, `.bind` is a concrete node subclass with a typed
`__call__` method:

```python
sl.action_controls(self.work_lost.bind, key="filters")
sl.action_controls(self.work_lost.bind(available=can_filter), key="filters")
```

Concrete subclasses fit existing factory membership checks as well as
`LayoutNode`. Their ownership contains the cell reference; construction and
replacement must preserve it. Use explicit typed override parameters, with an
omitted-value sentinel when needed, rather than untyped `**kwargs`. Bind-time
configuration cannot change initial values, persistence, history ownership, or
cell identity. These policies belong to the declaration.

Generated controls have no application callbacks and use `object` context.
Existing context contravariance lets them compose inside Discord screens.
Future child-wrapping factories must infer target/context from their children.

Optionless choices declarations return a builder, not a renderable control:

```python
selected = sl.presentation.choices(minimum=0, maximum=25)

# Inside render, after checking options is nonempty:
self.selected.bind(*options)
```

Overloads distinguish complete declarations from builders. The spike requires a
first choice when binding; possibly empty collections need an empty-state branch.
Review that restriction against collection usage before implementation. A builder
passed directly into a document must fail type checking.

For custom controls, `.bind` is a value binding rather than a node:

```python
price_range = sl.presentation(PriceRange(10, 100))

# Inside render:
PriceRangePicker(value=self.price_range.bind)
```

Extend `Ownership[ValueT, EventT]` with `ValueBinding[ValueT]`. The existing
control handler extracts the next value and commits it to the reference's cell;
this member requires no callback event type. A custom control can accept the
same ownership union.

## Share the state backend, not `_State`'s descriptor contract

Reuse the existing cell, transaction, dependency, version, and comparison
machinery. An internal placement-owned reactive model can use ordinary state
fields. Extract shared lower-level machinery only where composition requires it.

Do not subclass `_State` as it currently stands. Its descriptor returns a value,
accepts assignments, and stores cells on the component instance. Presentation
returns a handle, rejects assignments, and resolves cells by placement. Making it
an `_State` merely to satisfy discovery would also send export and inspection
through the wrong owner. Extend those integration points explicitly.

The generic constructor supports `opaque=True` with existing state semantics:

```python
draft = sl.presentation(factory=CustomSelection, opaque=True)
```

Opaque values compare by identity. Replacements are transactional; in-place
mutations are not reversible. Extend `self.mutated(object)` to search both ordinary
opaque state and opaque presentation declarations. Presentation lookup uses the
active interaction placement and transaction; no separate mutation API is needed.
Require exactly one matching cell across both kinds, preserving existing ambiguity
errors. Render-time mutation notification is forbidden, and ordinary state-only
notification outside a placement keeps its existing behavior.

Notification is staged until commit and discarded on rollback, but the object's
mutation cannot be rolled back or undone. History may restore an opaque reference
replacement; a mutation notification must not claim to capture an inverse of the
object's contents. Reject `opaque=True, persist=True`, as ordinary state does.
Built-in toggle and choices factories do not expose `opaque`: their boolean and
tuple values already have value semantics.

## Placement, reads, and lifetime

A cell belongs to a mounted placement and declaration. Two placements of one
component instance have different cells. Descriptors hold defaults and policies,
never live values. Control keys identify controls, not cells; changing a control
key must not reset the declaration's value.

`.value` resolves the render placement and registers a dependency, even before
`.bind` appears. It uses the declared default without publishing candidate state.
Outside render, use `event.read(self.selected)` to resolve the interaction's
placement and transaction snapshot. Do not evaluate the render-only `.value`
property or silently choose a placement when scope is missing.

Children can receive references anchored to an ancestor placement. References
to multiply placed siblings require explicit placement identity; component
identity and global string lookup are insufficient. Dependencies participate in
component/subtree caching, rollback, and successful-delivery reconciliation.

Omitting a control while its owner remains mounted preserves its cell. Removing
the owner collects cells after successful delivery, subject to history retention
below. Existing anonymous uncontrolled controls retain key-based behavior.
Readers see stored values; options built later in render cannot silently rewrite
them. Define a stale-selection policy before migrating collection controls.

## History is explicit and can be declared at class scope

Transaction participation, undo recording, and persistence are independent:

```python
from squid_ui.runtime import history

class SubscriptionList(sl.Component):
    edits = history()
    selected = sl.presentation.choices(
        minimum=0,
        maximum=25,
        record=edits,
        record_label="Change selection",
        persist=True,
    )
```

`record` accepts a concrete `History` or its declaration descriptor. Resolve the
latter against the same component instance when binding, obtaining `self.edits`.
Expose the actual descriptor return type of `history()` instead of its current
suppression-backed `History` return annotation. No lambda, string field lookup,
or new class-global stack is needed. Inheritance must preserve owner resolution.

By default, presentation writes roll back with failed actions but are excluded
from undo plans. Merely omitting a history entry is insufficient: current history
captures transaction patches, so explicitly distinguish transactional presentation
writes from history-eligible patches. Reading a selection for a recorded Remove
action must not make the selection part of that action's undo state.

With `record=`, the generated handler reserves one history entry before its value
write. Undo/redo restore the cell and invalidate readers without replaying the
interaction. Use the toggle label by default; choices and generic bindings require
`record_label` when recording. Avoid duplicate reservations when a custom control
already records its surrounding action. Suppress empty/no-op entries.

Use existing conditional patches and retention machinery. Entry identity includes
the original placement incarnation: never apply an old patch to a new placement
reusing its path. If the placement is removed, its local presentation entries
become unavailable and release retained cells. Do not silently discard unrelated
domain-operation receipts or external-effect reconciliation records. A history
shared by several placements must still distinguish each placement's cells.

## Serialization and `squid_storage`

`persist=True` marks values for export; it does not configure or create a storage
backend. Serialize values by stable placement path and field name, never handles,
descriptors, bindings, callbacks, history objects, or opaque collaborators.
Reconstruct declarations from the registered component restore recipe.

Add an explicit section for declared presentation cells to durable UI snapshots,
with schema validation/migration. Restore before the first consuming render.
Existing snapshots remain readable through a defined migration; unsupported
newer formats fail clearly. Removed/renamed declarations require migration rather
than accidental rebinding. Missing fields use declared defaults.

`squid_storage` stores the resulting snapshot through a typed `Slot` and codec.
`PersistentStatePool` currently calls ordinary `export_state`/`restore_state`, so
it cannot discover placement-owned presentation cells automatically. Adapt through
the internal placement model or an explicit presentation snapshot exporter. Keep
storage dependencies out of the portable UI core.

Reuse existing tuple restoration and codec facilities. Custom values need an
explicit codec in the snapshot/storage adapter, with clear errors for unsupported
values; annotations alone cannot supply serialization. Storage failure keeps the
configured backend's behavior, including asynchronous pool writes that report
errors without rolling back an already committed action.

Persisting values does not persist undo stacks. Recovery reconstructs presentation
values with empty history; durable history requires a separate design for patches,
versions, resources, and reversible external operations.

## Scope and validation

Pagination stays unchanged. Its effective page is a layout result that needs a
separate document-introspection design; this plan includes no page descriptor,
deferred value, or layout query API.

`spikes/presentation_api.pyi` sketches signatures against current semantic nodes.
`spikes/typing_examples.py` checks inference, direct/callable bindings, class-level
history references, custom factories, opaque mutation notification, event reads,
and composition inside a Discord screen. Commented negative probes must fail.
These are static examples, not an importable runtime implementation.

The revised spike passes both checkers, and both reject all twelve negative
probes. The whole-project Pyrefly check also passes.

Validate with Pyrefly and BasedPyright. BasedPyright needs a merged workspace
import tree, as in CI's installed-package approach, and the intentionally stub-only
module needs its missing-source warning disabled. Runtime tests must cover
factory membership, replacement, placement isolation, reader invalidation,
mutation lookup, rollback, history exclusion/opt-in, removal, and snapshot recovery.
Also check invalid option combinations and double history reservations.

Implement the placement-owned backend and read/mutation integration first, then
toggle/choices descriptors, history eligibility, and persistence adapters. Other
control factories need equally precise signatures before inclusion.
