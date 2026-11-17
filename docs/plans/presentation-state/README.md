# Readable presentation controls

Proposal, with a static typing spike; no runtime API has been implemented.

Declare a control once, let the runtime update its value, and read that value from
other parts of the UI without an assignment callback:

```python
class Reports(sl.Component):
    work_lost = sl.presentation.toggle("Work lost only")

    def render(self):
        return (
            sl.action_controls(self.work_lost.bind, key="filters"),
            self.render_reports(only_work_lost=self.work_lost.value),
        )
```

Keep `sl.state` for component-owned data and `controlled(value, callback)` for
changes that require application behavior. Runtime-owned controls acknowledge
interactions, update presentation state, and invalidate their readers. They do
not save domain data or invoke application change handlers.

## Separate declarations, references, and controls

`sl.presentation` is one callable factory object with named control factories.
Each factory returns a descriptor. Class access returns that descriptor; instance
access returns a typed handle associated with the component instance:

```python
class PresentationField[HandleT]:
    @overload
    def __get__(self, instance: None, owner: type | None = None) -> PresentationField[HandleT]: ...
    @overload
    def __get__(self, instance: object, owner: type | None = None) -> HandleT: ...
    def __set__(self, instance: object, value: Never) -> None: ...
```

The handle exposes read-only `.value` and `.bind` properties. It is neither a
boolean proxy nor a renderable node. Reassigning the field or `.value` is an
error. Reset/write operations can be designed separately with explicit interaction
scope; they must not become writes during render.

Different factories return different handle types. Avoid one generic `.bind`
type that promises both a renderable control and a value binding for every case:

| Declaration | `.value` | `.bind` |
| --- | --- | --- |
| `presentation(initial)` | Inferred value type | `ValueBinding[ValueT]` |
| `presentation.toggle(label)` | `bool` | Callable subclass of `Toggle[object]` |
| `presentation.choices(first, *rest)` | `tuple[str, ...]` | Callable subclass of `Choices[object]` |
| `presentation.choices()` | `tuple[str, ...]` | Builder requiring choice entries |

`PresentationField[HandleT]` means descriptor typing is implemented once, while
each handle keeps its precise binding type. No `Any`, owner context type, or
callback event type is needed in the public declaration.

Value bindings must be invariant: a control that writes an `int` cannot accept
a binding whose cell only permits `bool`, even though `bool` is an `int` subtype.
The spike includes the internal commit operation to make both read and write
positions visible to variance inference. Read-only references may be covariant.
Descriptors also define a rejecting `__set__` signature so assigning another
handle cannot silently replace a field.

The callable constructor uses a generic `__call__` method, so its initial value
infers the value type. For an optional or empty custom value whose type cannot be
inferred, annotate the field explicitly. Python does not permit type arguments
on a generic function call; earlier sketches of `presentation[ValueT](...)`
should not become the promised syntax.

## Make `.bind` fit the existing vocabulary

For a declared toggle, `.bind` is a concrete `Toggle[object]` subclass with a
typed `__call__` method. Therefore it already fits `action_controls`, concrete
factory validation, and `LayoutNode`; merely implementing `Renderable` would
not satisfy all those consumers. Calling it returns an ordinary `Toggle[object]`
whose supplied configuration overrides the declaration for that render:

```python
sl.action_controls(self.work_lost.bind, key="filters")
sl.action_controls(self.work_lost.bind(available=can_filter), key="filters")
```

The subclass adds behavior, not another copy of the control's value. Its `on`
ownership contains a typed presentation reference. Construction and dataclass
replacement must preserve that reference. Factories should use explicit typed
parameters, with an omitted-value sentinel where `None` is a meaningful override;
do not introduce an untyped `**kwargs` configuration channel.

Generated controls have no application callbacks and use `object` context. The
existing contravariant context contract lets them appear in Discord screens
without parameterizing the descriptor by Discord request or client types.
Controls that wrap children must infer target/context from those children in
their builder method; they cannot assume every generated node is portable.

For custom controls, `.bind` is only a `ValueBinding[ValueT]`:

```python
class Prices(sl.Component):
    price_range = sl.presentation(PriceRange(10, 100))

    def render(self):
        return PriceRangePicker(value=self.price_range.bind)
```

Extend the existing `Ownership[ValueT, EventT]` union with
`ValueBinding[ValueT]`. Unlike `Controlled`, that member needs no event type:
the control's existing handler knows how to obtain the next value from its
event. It writes that value to the reference's cell, rather than to the control's
own key. A generic custom control can accept the same ownership union. The
spike's custom picker accepts only `ValueBinding[PriceRange]` to pin type safety.

## Distinguish complete controls from builders

An optionless choices declaration is useful when options come from loaded data:

```python
selected = sl.presentation.choices(minimum=0, maximum=25)

# Inside render:
self.selected.bind(*options)
```

Its `.bind` must be callable but must not inherit `Renderable` or `Choices`.
Otherwise passing it directly would type-check yet render an incomplete control.
Use overloads to return a complete handle when declaration-time choices exist,
and a builder handle when they do not. The spike requires a first choice for
both declaration and binding; callers with a possibly empty sequence must branch
to an empty-state view before binding. This is a deliberate restriction to review
against existing collection usage before implementation.

Other configuration can vary during render without changing the value type.
`initial` and the field's state identity cannot be overridden by `.bind(...)`.
Control keys identify rendered controls; the cell belongs to the declaration.
Changing a control key must not silently reset that cell.

## Placement and reads

A cell is identified by mounted placement, declaring component, and field name.
Two placements of the same component instance have separate cells. Field
descriptors contain immutable defaults/configuration, never live values. Generic
mutable defaults need a factory variant or a documented immutable-value
restriction; the initial API should require immutable values.

`.value` resolves the current render placement and registers a reactive read.
It must work before `.bind` appears in the render, using the declared initial
value without mutating live state. Reading without a render scope raises; it
must not fall back to a default or choose an arbitrary mounted placement.

An interaction reads through `event.read(self.selected)`, using a shared nominal
reference base to infer `tuple[str, ...]`. That base prevents arbitrary objects
with a `.value` property from accidentally satisfying the contract. Runtime
resolution must use the internal cell reference,
not evaluate the render-only `.value` property. The dispatcher must carry the
placement and transaction snapshot into the event. Values must follow the
existing admission/transaction rules, rather than capture a stale rendered value.

Readers in child components can consume a reference anchored to a particular
ancestor placement. Unanchored references to multiply placed siblings are
ambiguous and must fail. This needs explicit placement metadata; ordinary
component identity or a global string lookup is insufficient.

Dependencies must participate in component and subtree render caches, transaction
rollback, and successful-delivery reconciliation. Planning remains free of
application callbacks. Removing the declaring placement collects its cells after
successful delivery; omitting its control while the owner remains mounted does
not reset them. Existing anonymous uncontrolled controls retain their current
key-based behavior.

Ordinary readers see the stored value, not a value silently normalized against
options constructed later in the same render. Removed selections need an explicit
policy before migration.

## Pagination is outside this plan

Keep existing pagination and uncontrolled page ownership intact. The effective
page is a layout result, which needs a separate document-introspection design;
this plan proposes no page descriptor, deferred value, or layout query API.

## Evidence and implementation boundary

`spikes/presentation_api.pyi` sketches the public signatures against current
semantic nodes. `spikes/typing_examples.py` checks descriptor inference, direct
and callable bindings, generic custom values, event reads, and composition in an
actual Discord screen. Its commented negative probes must each be rejected.
This is a typing experiment, not importable production code.

Before implementation, validate those examples with Pyrefly and BasedPyright.
The current spike passes both; all eight negative probes are rejected. Pyrefly's
whole-project check also passes. BasedPyright needs a merged workspace import
tree (as in CI's installed-package approach) because `squid_ui.ext` is distributed
across packages. Validation used a temporary merged source tree and disabled only
the missing-source warning for the intentionally stub-only `presentation_api`.
Runtime tests will additionally need to cover concrete factory membership,
namespacing, duplicate placements, reader invalidation, rollback, failed delivery,
and removal. In particular, static acceptance of node subclasses does not prove
that every planner/replacement path preserves their ownership references.

Implement value references and placement-aware reads first, then toggle and
choices descriptors. Defer other control factories until their child and value requirements have
equally precise signatures.
