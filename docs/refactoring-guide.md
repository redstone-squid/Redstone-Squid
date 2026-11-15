# Refactoring and cleanup

This guide captures the expectations reflected in the `squid_reactivity` changes
of November 14–15, 2026. Apply the same judgment elsewhere in the repository.
The goal is code whose concepts, ownership, and ordinary use are easy to understand.
A cleanup should reduce how much a reader must discover to make a correct change.

Read this alongside [the documentation style guide](code-documentation-style.md).

## Understand the responsibility before changing the shape

Read the implementation, its callers, and its tests together. Work out what each
type represents, which object owns its state, and where its lifetime ends. Then
decide what belongs together and what should disappear.

Moving functions between files is useful when it makes those responsibilities
clearer. Renaming a vague abstraction without resolving its mixed responsibilities
leaves the hard part untouched. Likewise, splitting a large file into arbitrary
pieces just makes the reader visit more files.

For example, transaction scope, execution, identity, and results are distinct
responsibilities. The scope installs the active transaction and hands completion
to the engine. The engine owns staging, publication, and outcome delivery. The
public handle gives callers access to context, results, and callback registration.
Each part should have enough authority to do its job without reaching into the
others' storage.

## Put behavior with the state it governs

Keep mutation, validation, and lifecycle decisions with their owner. A transaction
handle can check whether it is being used in the right context, but the engine
should manage its callback lists, ownership checks, and duplicate registration
rules. Callers should ask it to register a callback rather than manipulate those
lists themselves.

Methods that describe or inspect a cell belong on the cell when they are part of
its responsibility: `cell.label` and `cell.staged_value()` are examples. Avoid
scattering helpers that take an object only to interpret its private fields.
Keep application policy out of shared storage types.

Give each fact one authoritative home. Derive transaction metadata from the
active transaction rather than maintaining another ambient copy. Remove fields
that merely duplicate the handle's context or read-only status. Every stored copy
creates another consistency rule that future changes must preserve.

## Make lifetime and context explicit

Use an explicit object when the caller is acting on a particular lifetime.
`tx.after_commit(callback)` says which transaction owns the registration. An
ambient registration function makes that relationship harder to see.

Pass causality explicitly when code starts work, such as
`operation.start(caused_by=tx.context)`. Keep implicit context where the API needs
it, such as a field read discovering its transaction and dependency collector.
For each remaining context variable, explain who owns it, how nested scopes
restore it, and what child tasks inherit.

Separate an immutable reference to earlier work from access to live execution
state. Retaining a transaction's causal identity must not keep its overlay active.
Similarly, a cache belongs to the view in which it was computed; another
transaction must not accidentally read that view's staged values.

Remove synchronization only after checking the execution model. Synchronous
publication on an owning thread can make a commit lock unnecessary, but that
does not establish that the objects are safe to share across threads. Preserve
ownership checks and the cancellation behavior around publication.

## Use names and types that expose the actual distinctions

Use one term consistently for one concept. Reserve `Signal` for the valueless
source it actually names; ordinary state fields need not also be called signals.
Distinguish a subscription source from an owner of transactional state. Distinguish
a resolved dependency address from a name that a bus still needs to resolve.

Name functions and modules for their role. A lookup of the active transaction
engine should say that; a generic name such as `active` leaves readers guessing.
Arrange definitions by responsibility and reading flow, keeping related state,
public operations, and their supporting implementation near one another.

Carry meaningful types through the whole path. A typed cell should not lose its
value type when it enters staging, and an asynchronous binding should retain its
status type. Declare concrete protocol implementations explicitly where that
makes their contract checkable. Expose read-only properties when mutation is not
part of the interface. These changes should let the type checker enforce a real
constraint, rather than merely add annotations around `Any`.

## Make the supported API small and usable

Give ordinary callers a public way to ask ordinary questions. They should use
`current_transaction()` rather than read a private context variable. Tests should
prefer observable results and inspection APIs over private storage when those
express the behavior being tested.

Keep everyday authoring operations at the package root and specialist interfaces
in focused public modules. Mark implementation modules with a leading underscore.
Avoid providing multiple competing import paths for the same everyday concept.
Keep any necessary integration internals narrow and explicit about their stability.

Remove redundant aliases such as `batch` when `transaction` already expresses
the operation. Update callers, exports, tests, and examples together. Do not leave
compatibility wrappers by habit; retain one only for an actual compatibility
requirement.

## Remove machinery without a consumer

Check repository-wide usage before preserving a report, codec, abstraction, or
extension point. Diagnostic snapshots and delivery belong in the reactive
library; an application's serialization format and storage can belong in its
sink. A codec with no consumer does not become necessary just because it already
has tests.

Remove unused reporting fields while preserving the internal tracking needed
for conflict detection. Replace tests of removed bookkeeping with tests of the
behavior that still matters. Keep useful parsing, validation, lifecycle helpers,
and named domain rules; reducing line count alone is not a reason to inline them.

Avoid work when nobody requested its result. Diagnostic observation should not
allocate delivery bookkeeping when there are no observers, and detailed validation
reports can be opt-in. Preserve redaction and delivery semantics when optimizing.

## Keep independent policies independent

Defaults should make ordinary declarations straightforward. Persistence is an
explicit field choice with `persist=True`; runtime-only fields should not need
repeated opt-outs. Identity comparison is a separate choice: a non-JSON value
does not automatically need `opaque=True`.

Likewise, cross-transaction computed reuse is an explicit optimization with
`@computed(reuse=True)`. It must validate dependencies and preserve conflict
tracking. Benchmark it against realistic alternatives, and explain the restrictions
on functions that can use it. Do not make a more complicated execution policy
universal just because some callers benefit.

Changing a default or adding listener behavior changes the contract. Treat that
as a deliberate behavior change, migrate affected callers, and test it separately
from mechanical moves or renames.

## Preserve the boundaries that callers observe

Before restructuring lifecycle code, identify the irreversible point. Once state
is published, a later notification failure cannot truthfully turn the outcome into
a rollback. Preserve that distinction in results, error handling, and diagnostics.

Ordering also belongs to the contract. Change listeners run after the commit has
finished announcing itself. Queue and deduplication improvements must preserve
that ordering and avoid exposing partially announced changes. Errors should name
the failing listener or operation so someone can act on them.

Choose tests around these boundaries: conflicting reads, rollback, stale contexts,
cancellation, callback failure, and listener ordering. Keep coverage that explains
why an invariant matters. Avoid tests that merely freeze the current file layout
or private representation unless the boundary itself is the requirement.

## Explain the library on its own terms

A reader should be able to understand reactive models, transactions, and listeners
without first learning the UI renderer. Start with what the reader wants to do,
show an ordinary example, and then explain the relevant limits. A concrete
conflicting transaction teaches more than a list of transaction-related symbols.

Keep the contract at the interface and the implementation's reasoning beside the
implementation. Document lifetime, ownership, and surprising behavior where needed.
Do not turn every change into another docstring paragraph, duplicate the README
throughout the source, or preserve historical explanations after their reason is gone.

## Finish the cleanup across its callers

Make small, independently valid commits. Separate mechanical organization from
semantic changes where practical, so reviewers can tell what requires behavioral
scrutiny. A rename is complete when imports, tests, examples, and documentation
all use the same vocabulary.

Before finishing, check that:

- Each responsibility and piece of mutable state has a clear owner.
- Ordinary callers can use the supported API without knowing its storage layout.
- Removed aliases, fields, and helpers have no remaining consumers.
- Defaults and observable behavior are preserved or deliberately migrated.
- Focused tests pass with `--no-cov` after the final relevant edit.
- Changed code is formatted and linted, `git diff --check` passes, and the whole
  project passes `just typecheck`, following the repository's validation rules.

The useful review question is whether an engineer new to this code can find the
right entry point, explain who owns the work, and predict its result without
tracing unrelated implementation details.
