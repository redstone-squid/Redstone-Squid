# Refactoring and cleanup in Python libraries

The goal is code whose concepts, ownership, and ordinary use are easy to understand.
A cleanup should reduce how much a reader must discover to make a correct change.
These guidelines apply to small utility packages as well as libraries with state,
asynchronous work, or integrations. Apply the parts that fit the library; a simple
function does not need an object lifecycle or an extension system.

For comments and docstrings, see [the documentation style guide](code-documentation-style.md).

## Understand the responsibility before changing the shape

Read the implementation, its callers, and its tests together. Work out what each
type represents, which object owns its state, and where its lifetime ends. Trace
an ordinary call from input to result before deciding what belongs together and
what should disappear.

Moving functions between files is useful when it makes those responsibilities
clearer. Renaming a vague abstraction without resolving its mixed responsibilities
leaves the hard part untouched. Likewise, splitting a large file into arbitrary
pieces just makes the reader visit more files.

For example, parsing configuration, validating it, and opening resources from it
are different responsibilities. Separate them when callers need to validate
without performing I/O, or when their dependencies and lifetimes differ. Keep
closely related steps together when splitting them would only add forwarding calls.
Let the actual responsibilities determine the structure.

## Put behavior with the state it governs

Keep mutation, validation, and lifecycle decisions with their owner. A registry
should manage its entries and duplicate-registration rules. Callers should ask it
to register an item rather than check and modify its private dictionary themselves.

A helper that mostly interprets an object's private fields may belong on that
object. Alternate constructors can belong on the type they construct. Keep
application policy and transport dependencies out of shared value types, and keep
stateless transformations as functions when no object needs to own them.

Give each fact one authoritative home. Derive a session's configuration from its
configuration object rather than copying individual flags into several wrappers.
Every stored copy creates another consistency rule that future changes must
preserve. Store derived data only when there is a reason, and define how it stays
valid.

## Make lifetime and context explicit

Use an explicit object when the caller is acting on a particular lifetime.
`session.on_close(callback)` says which session owns the registration. A global
registration function makes that relationship harder to see.

Pass dependencies and execution context explicitly where practical. Keep implicit
context where it serves the interface, such as request-scoped tracing. For each
`ContextVar`, explain who owns its value, how nested scopes restore it, and what
child tasks inherit. A copied context does not give each task an independent copy
of a mutable value.

Separate a record of earlier work from access to live resources. Retaining a job's
result should not accidentally retain an open connection or keep a session usable
after it closes. Define who closes resources, whether callers may supply borrowed
resources, and what remains valid after cleanup. Use context managers when scope
is the natural owner of cleanup.

Check the execution model before changing synchronization. Identify which state
can be shared across tasks or threads and where execution can interleave. Preserve
ownership checks, cancellation behavior, and cleanup on partial failure. Removing
a lock requires a concrete reason that the protected access no longer needs it.

## Use names and types that expose the actual distinctions

Use one term consistently for one concept. Distinguish a parsed value from raw
input, a resource name from an open handle, and a job definition from one execution
of that job. Avoid making readers infer these differences from call order.

Name functions and modules for their role. `current_session()` is clearer than
`active()` when the question is which session is running. Arrange definitions by
responsibility and reading flow, keeping related operations and their supporting
implementation near one another. Avoid turning `utils.py` into a home for unrelated
code whose ownership has not been decided.

Carry meaningful types through the whole path. A cache of a particular value type
should retain that type through lookup and loading. Use protocols for capabilities
that consumers actually need; declare concrete implementations explicitly when
that helps check their contract. Expose read-only properties when mutation is not
part of the interface. Annotations should enforce useful constraints rather than
surround untyped operations with casts or `Any`.

Choose syntax and typing features that fit the library's supported Python versions.
An internal cleanup should not silently raise its minimum Python version.

## Make the supported API small and usable

Give ordinary callers a public way to ask ordinary questions. They should be able
to inspect whether a session is closed without reading its private flags. Tests
should prefer observable results and inspection APIs over private storage when
those express the behavior being tested.

Choose clear, documented import paths. A small package may expose its everyday
operations at the root; a larger library may use focused public modules. Mark
implementation modules with a leading underscore and keep re-exports deliberate.
Users should not have to choose between several apparently equivalent entry points.

Remove redundant aliases and forwarding layers when compatibility permits it.
Update callers, exports, tests, and examples together. For a published library,
repository searches cannot establish that a public API is unused: external callers
may depend on imports, signatures, subclass hooks, and serialized objects. Follow
the library's compatibility policy and provide a deprecation or migration path
when required. Keep temporary shims purposeful, with a defined removal condition.

## Remove machinery without a consumer

Check usage before preserving a report, codec, abstraction, or extension point.
Look beyond direct calls to callback registrations, plugins, dynamic imports, and
supported external integrations. Tests alone do not establish that an otherwise
unused abstraction serves a user.

Keep the library's responsibility distinct from application choices. A library
may produce diagnostic records while applications decide where and how to store
them. A serialization library, by contrast, owns its formats. Place behavior based
on the package's purpose rather than applying a universal rule about layers.

Remove unused reporting fields while preserving the internal bookkeeping needed
for correctness. Replace tests of removed bookkeeping with tests of the behavior
that still matters. Keep useful parsing, validation, lifecycle helpers, and named
domain rules; reducing line count alone is not a reason to inline them. Similar
code in unrelated domains does not always need a shared abstraction.

## Keep independent policies independent

Options should express separate decisions. Persistence, equality, caching, and
validation are different policies; one flag should not change several of them
unless that relationship is part of the documented contract.

Choose defaults that make the common case straightforward. Repeated opt-outs in
callers can reveal a poor default, but changing it is a behavior change. Review
existing callers and external compatibility before making that change.

Make expensive or restrictive behavior optional when only some callers need it.
For example, detailed diagnostics can be opt-in. Caching needs a defined key,
lifetime, and invalidation rule, including the inputs supplied through context.
Explain whether enabling it changes evaluation frequency or side effects.

Optimize a demonstrated cost. Compare against realistic alternatives and preserve
ordering, errors, and resource usage guarantees. Avoid allocating diagnostic data
when nobody consumes it, but retain the checks needed for correctness.

## Preserve the boundaries that callers observe

Before restructuring lifecycle code, identify the irreversible point. Once a file
has been replaced or a transaction committed, a later callback failure cannot
truthfully mean that nothing happened. Preserve that distinction in results, error
handling, and diagnostics.

Ordering, exception types, laziness, and resource ownership can all be observable
contracts. Replacing an iterator with a list changes when work happens and how much
memory it needs. Moving validation can change which error a caller sees first.
Queue and deduplication improvements can change callback order. Review these
consequences even when return values look identical.

Choose tests around the behavior at risk: invalid input, empty results, partial
failure, repeated calls, cancellation, callback failure, or concurrent access.
For an unfamiliar implementation, establish existing behavior before restructuring
it. Treat bug fixes and intentional contract changes as separate decisions, with
focused regression coverage. Avoid tests that freeze private representation unless
that representation is itself a required boundary.

## Explain the library on its own terms

A reader should be able to understand a library without first learning the
application that uses it. Introduce its concepts, start with what the reader wants
to do, show an ordinary example, and then explain the relevant limits. A concrete
input and result usually teach more than a list of exported symbols.

Keep the contract at the interface and the implementation's reasoning beside the
implementation. Document lifetime, ownership, and surprising behavior where needed.
Do not turn every change into another docstring paragraph, duplicate the README
throughout the source, or preserve historical explanations after their reason is gone.

## Finish the cleanup across its callers

Make small, independently valid commits. Separate mechanical organization from
semantic changes where practical, so reviewers can tell what requires behavioral
scrutiny. A rename is complete when supported imports, tests, examples, and
documentation agree, including any intentional compatibility paths.

Before finishing, check that:

- Each responsibility and piece of mutable state has a clear owner.
- Ordinary callers can use the supported API without knowing its storage layout.
- Removed interfaces have been checked against internal usage and external contracts.
- Observable behavior is preserved or deliberately changed with a migration path.
- Focused tests cover the affected behavior and pass after the final relevant edit.
- Formatting, linting, type checking, and diff checks pass under the project's rules.
- Changes to exports, dependencies, or packaging work from an installed distribution,
  and relevant checks cover the supported Python versions.

Scale validation to the change. Documentation edits need accuracy and link checks;
a package move may need installation and import checks; a lifecycle rewrite needs
failure-path tests. Follow the project's validation requirements without adding
tests that merely repeat the implementation.

The useful review question is whether an engineer new to this code can find the
right entry point, explain who owns the work, and predict its result without
tracing unrelated implementation details.
