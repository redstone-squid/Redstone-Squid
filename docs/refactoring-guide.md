# Refactoring Python libraries

A refactor should make the library easier to understand and change. Look for the
right concepts, owners, interfaces, and dependency boundaries. Passing tests proves
part of correctness; it does not prove that the design has improved. A collection
of bug fixes, renamed helpers, or extracted utility functions may leave the main
structural problems untouched.

Use this guide when asked to refactor or clean up a library, even when the request
is brief. The user should not need to inspect every module and supply each design
observation. Follow [the documentation style guide](code-documentation-style.md)
and the repository's validation and commit instructions.

## Start with a structural diagnosis

Read representative callers, implementation paths, and tests before editing. Trace
an ordinary operation from its public entry point through state changes to its
result and cleanup. For a library with several backends, trace more than one.
Find where they share a concept but implement it independently.

Identify the highest-value problems and state the proposed before and after in
concrete terms. For example: two renderers separately resolve and validate cached
programs; one typed cache operation can own that rule. Or: a root object discovers
bindings by reflecting over arbitrary dataclasses; a message-plan type can prepare
bindings from the supported scene types for both primary and continuation messages.

Inspect all of these dimensions, without forcing a change in every category:

- **Organization:** which responsibilities deserve packages, and which definitions
  belong together within each module?
- **Ownership:** which helpers manipulate another object's state or construct its
  values, and should that object own the operation?
- **Typing:** where does a known relationship disappear into `object`, `Any`, casts,
  reflection, or a protocol that omits what consumers actually need?
- **Duplication:** where are there parallel pipelines, repeated rules, or multiple
  representations of the same fact?
- **Concepts and API:** which wrappers, aliases, options, and result types earn their
  complexity through actual use?
- **Dependencies:** does the reusable core know about a backend or application policy
  that should be supplied from outside it?

Prioritize changes that simplify a complete flow. A small extraction can be useful,
but do not let easy mechanical changes displace the architectural work.

## Design the ordinary use before the implementation

Show a short caller example when changing an interface. Explain what each object
represents, who owns it, and which operations belong on it. Compare alternatives
using an actual use case rather than attractive names or design-pattern labels.

The productive questions are often simple: What is this for? Why does it need this
argument? Why does this return an unknown type? Why are there two implementations?
Can the same goal be achieved by removing something?

For substantial redesigns, establish the overall direction and work in coherent
phases. Detail the next phase enough to implement it; do not freeze every later
choice prematurely. Raise meaningful unresolved API or behavior decisions with
concrete alternatives. Routine implementation choices do not need repeated approval.

Respect the requested scope. If the user requests behavior preservation, implement
structural improvements within that constraint and identify breaking proposals
separately. Do not interpret it as a request for only tiny local edits. Conversely,
a cleanup request alone does not authorize silently changing public behavior.

## Put operations and state under the same owner

A helper that primarily interprets one object's fields is a candidate for a method.
Construction from another representation often belongs in a class method. Move the
responsibility, including its invariants and dependencies, rather than adding a
method that merely forwards to the old helper.

For example, a cell can own the calculation of its slot name. A transaction can
register its own hooks without receiving one of its internal hook lists as an
argument. An explicit transaction handle can own commit callbacks instead of
requiring callers to understand a family of ambient registration functions.

Keep stateless transformations as functions where that is their natural home.
Few call sites do not make a function unnecessary, and a repeated initialization
line does not by itself justify a shared base class. Judge the resulting ownership
and coupling.

Give each fact one authoritative home. Avoid storing an address or configuration
both on an object and on a wrapper unless their meanings differ. Expose read-only
properties when mutation is not supported. Separate durable results from live
resources so retaining a result does not retain a transaction or connection.

## Organize by responsibility and reading order

Consider packages when several modules form a coherent subsystem. Messages,
rendering, testing, and developer tools can each have internal structure and clear
entry points; a flat directory of similarly prefixed files hides those boundaries.
Within modules, group related types and operations and place supporting helpers
near the behavior they explain.

File moves are only part of the work. After grouping a subsystem, review its helper
ownership, duplicated operations, type contracts, and dependencies. Splitting a
large function into many forwarding functions can make the reader work harder.
For a renderer with many node cases, consider focused handlers and explicit dispatch;
do not automatically put backend rendering methods on shared semantic nodes.

Names must describe the actual operation. A method that validates dependencies and
returns a version should say that, rather than sound like it waits for completion.
Complete conceptual renames across related types, methods, documentation, and
callers so old vocabulary does not survive in half the subsystem.

## Preserve useful type relationships

Carry value types through storage, loading, callbacks, and results. If a binding
returns a status associated with its concrete implementation, make that relationship
generic even when one consumer ignores the result. That consumer can use an
appropriately broad specialization without weakening every caller's contract.

Prefer typed access to known attributes and explicit handling of supported node
types over `getattr`, `vars`, or arbitrary dataclass traversal. Fix the boundary
that lost information before adding a cast at every use. Heterogeneous storage can
require type erasure; keep it localized and explain the invariant that restores it.

For classes within the library, explicitly declare the protocols they implement
when practical, making the intended contract visible and checked. Inspect runtime
consequences too: inherited properties, slots, initialization, and weak references
can change behavior. Do not add setters just to accommodate an inherited property
when the value should remain read-only.

## Remove unnecessary concepts and duplicate pipelines

Ask what a feature lets a real consumer do before preserving or expanding it.
Diagnostics may need safe records and observation without needing a codec registry
and a serialization framework. Tests of an abstraction are not evidence that users
need it. Keep internal bookkeeping required for correctness even when removing
public reports of that bookkeeping.

Look above the helper level for duplication. Sharing an element compiler can still
leave two independent planning pipelines. A complete consolidation identifies all
callers, fills necessary capability gaps, migrates them, and removes the obsolete
path. Name anything that remains explicitly; do not describe a partial extraction
as unification of the entire system.

Keep target-specific output in the target implementation. A Slack planner should
not need to construct HTML output and reinterpret it to reach Slack. Likewise,
a reusable UI library should not dictate the application's localization system.
Check dependency direction when extracting an adapter: solving one coupling by
making the localization library depend on the UI library may create another.

Do not keep compatibility aliases by reflex. Determine the actual compatibility
requirement. When an incompatible change is authorized, migrate imports, callers,
tests, and examples and remove the obsolete interface. For supported external
consumers, follow the compatibility policy; repository searches alone cannot prove
an exported interface is unused.

## Keep policies and lifetimes understandable

Separate independent choices such as comparison, persistence, caching, and reuse.
An option whose name hides several effects makes callers reason about implementation
history. Use names and defaults that expose the decision callers actually make.

Complex cache behavior may deserve its own owner with explicit validity and lifetime
rules. Establish which inputs affect results, whether reuse changes side effects,
and how transaction reads or other required bookkeeping survive a cache hit.
Optimize work that has no consumer before weakening correctness checks.

Audit implicit context and synchronization against the execution model. Explain
who sets and restores context, what child tasks inherit, and which state is shared.
Do not remove a lock merely because it looks inconsistent, or add thread-safety
machinery without deciding what sharing is supported. Preserve cancellation,
partial-failure cleanup, callback ordering, and the distinction between failures
before and after an irreversible commit.

## Finish against the design objective

Make small, independently valid commits. Validate behavior at the boundaries the
change can affect, including failure paths where relevant. Package and export moves
need import and distribution checks; typing work needs the project type checker.
Follow repository requirements without adding tests that merely freeze private
representation or repeating broad suites without a reason.

Before declaring completion, revisit the original structural diagnosis. Explain
which responsibilities now have better owners, which duplicate paths disappeared,
which type relationships are preserved, and how ordinary callers became simpler.
State remaining design work separately from validation limitations. A reliability
pass is useful work, but it does not complete a requested structural refactor.

## What the weekend conversations showed

This guide was revised from the local Codex conversations on 14–15 November 2026,
using Europe/Brussels dates, including threads started earlier. The comparison is
based on user prompts, corrections, and the agents' reported changes. It is not an
independent correctness audit of every resulting commit or a claim that silence
means satisfaction.

The directed reactivity review repeatedly questioned purpose and ownership:
“what actually is our goal with the diagnostic,” whether helpers should be cell
methods, whether a cell should be generic, and whether an address ever changes.
Those exchanges led to reported removal of codec machinery, generic value/status
contracts, explicit protocol implementations, and read-only addresses. The user
also requested module ordering and explanations that stood independently of UI
knowledge. These are concrete expectations an agent should investigate itself.

Two broad cleanup conversations expose the recurring failure:

- In the Sunday UI cleanup, guard movement and LRU bookkeeping extraction passed
  extensive tests, but prompted “you're not making the refactoring and cleanups
  that im hoping can exist.” The discussion then reached duplicated planning paths,
  shared composition, backend extraction, and renderer dispatch. Local deduplication
  had missed the larger architecture.
- In the Sunday Discord library cleanup, the agent reported completion after
  reliability fixes, optimization, documentation, and extensive validation. The
  user replied: “You didn't even group stuff properly or refactor into packages
  or change helpers into methods or improve typing or reduce duplications.” The
  subsequent pass reported dedicated packages, construction on owning types, typed
  scene traversal, and shared cache and control-ID logic.

The recurring problem was stopping at changes that were easy to verify while
leaving the user to discover the important design changes. The better prompts
named a conceptual problem, challenged its necessity, and followed the answer
through ownership, types, and callers. A refactoring agent should perform that
reasoning before claiming the work is done.
