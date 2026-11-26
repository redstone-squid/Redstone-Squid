# Readable presentation controls

Implemented through placement-owned reactive cells. The public API is described
in the `sl.presentation` API; the
[typing examples](../../../../packages/squid-ui/tests/typing_presentation.py) check
the actual implementation rather than a separate stub API.

## Ownership and binding

`sl.presentation` is a callable factory with `toggle` and `choices` methods.
Descriptors return handles with `.value` for render-time reads and `.bind` for
controls. Generic declarations infer their type from an initial value or a
factory. Choices without declaration-time options return a builder requiring
options; complete declarations supply a directly renderable binding. Concrete
control subclasses preserve existing factory membership and replacement rules.

Writable bindings are invariant. Read-only references retain their value type
through `event.read(...)`. Handles and bindings are never boolean/value proxies.
Explicit assignment/reset APIs remain outside this implementation.

`sl.state` retains component-instance ownership. Declared presentation cells belong
to a placement and declaration, independent of a control's key. Two placements of
one component instance have independent values. Removing a placement expires its
cells; a new owner at the same path receives new cells. Omitting a control while
its owner remains mounted preserves the value. Existing `controlled(...)` and
`uncontrolled(...)` bindings remain available.

## Runtime integration

Presentation descriptors compose with ordinary reactive state rather than
subclass `_State`, whose value-returning descriptor and instance storage have
different contracts. Defaults are prepared before interactions can first access
a cell inside a transaction. Failed candidates cannot publish placement lifetime
changes. Cached renders observe presentation cells through normal dependencies.

Render reads and handler dispatch resolve explicit placement scopes. References
passed to descendants can resolve an ancestor placement; ambiguous sibling access
fails. Callback projection, grouped actions, and form bindings preserve this
scope. `presentation_state` holds the existing planning/session contracts under
a distinct module name so imports cannot shadow the `sl.presentation` factory.

The generic factory supports `opaque=True` and per-placement `factory=` defaults.
`self.mutated(object)` searches ordinary opaque state and the current placement's
opaque presentation cells, requiring one match. Notifications are transactional;
in-place object mutations cannot be reversed. Replacements compare by identity.
Opaque values cannot be persisted. Developer inspection reports presentation cell
identities and versions without reading their values.

## History and storage

`record=` and `persist=True` are independent declaration policies, both disabled
by default. `record` accepts a concrete history or a class-level `history()`
descriptor, resolved against the owning instance. Toggles use their labels;
choices and generic declarations require `record_label`.

Cell patches distinguish transactional writes from reversible history changes.
Unrecorded presentation writes roll back on failure but do not enter an undo
plan. Recorded changes use the existing history reservation, conditional inverse,
and redo machinery. No-op changes produce no entry. Opaque mutation notifications
are not reversible content edits. Removal discards local entries for expired
cells while retaining external-operation and compensation receipts.

Durable snapshots store persistent values by placement path and declaration name,
with a versioned declared-state section. Old snapshots default to an empty section;
unsupported section versions fail validation. Restoration precedes consuming
renders, validates persistent field names and built-in values, and restores choice
arrays as tuples. History stacks and live handles are not serialized.

`ComponentRegistry.register` accepts paired `presentation_encode` and
`presentation_decode` callbacks for custom values. Portable callers can pass
`runtime.presentation_cells.export()` to a typed `squid_storage.Slot` and restore
the decoded mapping before rendering. This explicit exporter is the integration
boundary; `PersistentStatePool` does not automatically discover presentation
fields. Backend write/failure semantics remain those of the selected storage
adapter.

## Boundaries and validation

Pagination is unchanged; [layout introspection](../layout-introspection/README.md)
covers page facts. Other built-in presentation declaration factories are deferred.
Stored selections are not silently rewritten when render-time options change;
consumers should ignore unavailable values, and collection migrations need an
explicit selection-reset policy.

Tests cover target composition, placement isolation/replacement, expired bindings,
reader invalidation, rollback, history, opaque mutation notification, candidate
isolation, durable recovery, old snapshot migration, scoped storage, and custom
codecs. Static examples cover factory inference, class-level history references,
complete controls versus builders, event reads, and invalid bindings.
