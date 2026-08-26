# The Squid vocabulary

**Status: applied 2026-08-26. `tests/architecture/test_naming.py` enforces the
retired vocabulary across public and private identifiers.**

One dictionary for `squid-layouts`, `squid-reactivity`, `squid-replicated`, `squid-stores`,
`squid-discord`, and `squid-patterns`, covering head nouns, suffixes, callable verbs, and
private identifiers. It is the naming source of truth referenced by
[squid-layouts-architecture.md §Naming](squid-layouts-architecture.md#naming).

## The rules

1. **One meaning per word.** Two exported classes may not share a short name.
2. **One word per meaning.** Two words may not name the same kind of thing. This is the new
   half, and it is what closes the sets below.
3. **A name uses the same word its own members use.** A class called `*Store` has methods
   that talk about what it stores.
4. **Identity and authority are never one type.** An `Address` stays true forever; a `Handle`
   expires.
5. **Suffix says the kind, head says the subject.** `SessionKey` is a `Key` about a session.
   A name whose last word is not in the suffix set needs a listed exemption.

### Exemptions

A name may leave the dictionary when following it would surprise a reader who knows the
wider convention. Exemptions are listed, not assumed, and the list is part of the test.

| Category | Keeps | Because |
|---|---|---|
| Stdlib and protocol shapes | `dumps`/`loads`, `keys`/`items`/`values`, `__init__`, `__getattr__` | the reader's `json`/`Mapping` expectation outranks internal consistency |
| Reactive-framework terms | `state`, `computed`, `resource`, `batch`, `untracked`, `transaction`, `watch` | borrowed from Vue/Solid/MobX; the docs already say "Vue-inspired" and renaming costs recognition |
| discord.py mirroring | `send`, `respond`, `ephemeral`, `followup`, `defer`, `interaction` | a name that shadows discord.py on purpose matches discord.py's spelling |
| Domain terms of art | `prepare`/`apply`/`abort`, `commit`/`rollback`, `claim`/`lease`/`release` | two-phase commit and lease vocabulary are established outside this codebase |

Case-by-case exemptions are allowed and are added to the same list with a one-line reason.
The reverse also holds: where a convention reads badly *here*, the dictionary wins and the
deviation is recorded the same way.

**Carve-out.** CRDT "snapshot" (a version vector of a replicated document) is not the pinned
`Snapshot` below. `squid-replicated` keeps its own sense, recorded here so the rule stays
enforceable.

## Suffixes

### What a value describes

| Suffix | Means | After it |
|---|---|---|
| `State` | the facts, no metadata; restorable | `CursorState`, `MountState` |
| `Snapshot` | read-only view of a subject that is **still alive** | `BusSnapshot`, `HistorySnapshot` |
| `Record` | a serialized fact that **outlives** its subject; what a `Store` holds | `StoredSessionRecord` |
| `Report` | diagnostics about a finished operation, for a human | `PlanReport`, `AuditReport` |
| `Metrics` | numbers about a finished operation, for a machine | `PlanMetrics` |
| `Inspection` | what `inspect` returns: an **expensive** diagnostic assembly of a live subject, for tools rather than logic | `MountInspection`, `SessionInspection` |

`Inspection` and `Snapshot` are not near-synonyms and both stay. A `Snapshot` is the cheap
read any caller may take; an `Inspection` is the expensive assembly a tool asks for, and it
*embeds* the snapshot rather than competing with it — `MountInspection` is literally
`snapshot + middleware + observed + followed + histories`. The pair reads the same way the
`-er` rule does: the noun is what its verb returns.

`Summary` retires. `SessionSummary` is a `Snapshot` (its subject is live);
`ChangeSummary` and `ExceptionSummary` are `Report`s.

### What happened

| Suffix | Means | After it |
|---|---|---|
| `Result` | the settled outcome of one named operation, usually a union of arms | `PlanResult`, `HistoryResult` |
| `Status` | an enum naming which phase or arm something is in | `CompensationStatus`, `ResourceStatus` |
| `Decision` | a choice made by an injected `Policy` | `CollisionDecision`, `GenerationDecision` |

`Outcome`, `Verdict`, `Feedback` and `Receipt` retire into these three. This is what
resolves the `ActionOutcome` homonym without a coin-flip: the reactive union
`ActionCommit | ActionRollback` is a **Result**, and profiling's `ActionOutcome` enum is a
**Status**. They stop sharing a word because they were never the same kind of thing.

### Identity and authority

| Suffix | Means | Expires |
|---|---|---|
| `Id` | an opaque scalar identity | no |
| `Key` | a structured identity you can build and look up by | no |
| `Ref` | a reference to something owned outside this library | no |
| `Address` | where something is | no |
| `Token` | transferable, opaque authority | yes |
| `Handle` | authority to write to one subject | yes |

`Locator` retires into `Address`. `FrontendAddress` holds durable, frontend-neutral
coordinates; `MountAddress` holds typed Discord coordinates. Their heads keep the two values
distinct without reusing the retired suffix.

### Configuration

| Suffix | Means | After it |
|---|---|---|
| `Spec` | a frozen, reusable recipe for building something | `FormSpec`, `ModalSpec` |
| `Policy` | an **injected decision-maker** — a protocol or callable | `AccessPolicy`, `CollisionPolicy` |
| `Mode` | an **enum** naming one of a fixed set of behaviours | `DiscordMode` |
| `Limits` | hard numeric ceilings imposed by an external system | `V2Limits` |
| `Profile` | a named bundle of verified behaviour for one library | `AdapterProfile` |
| `Dialect` | one protocol: what a legal message of it is | `V2Dialect`, `ClassicDialect` |
| `Target` | the product of a dialect and an adapter, named by its triple | `Target` |

`Policy` currently covers both halves and that is its whole problem: `AccessPolicy` and
`CollisionPolicy` are protocols you inject, while `CommitPolicy`, `PendingPolicy`,
`ActionPolicy`, `FormValidationPolicy`, `AmbiguousTimePolicy` and `NonexistentTimePolicy` are
plain enums. Six of the fourteen are misfiled. `Protection` and `Strategy` retire into
`Policy`.

### Collaborators

| Suffix | Means |
|---|---|
| `Store` | owns bytes beyond the process; its name says what it stores |
| `Registry` | an in-process table you register into and resolve from |
| `Pool` | reuses instances keyed by a scope |
| `Cache` | a discardable memo; dropping it costs time, never correctness |
| `Bus` | delivers to subscribers |
| `Ledger` | append-only record of what happened |
| `Outbox` | durable queue of work to hand off |
| `Sink` | where events go to leave the process |
| `Codec` | a paired encoder and decoder |
| `Engine` | a pluggable backend implementation of one capability |
| `Runtime` | owns live objects and their lifetimes for one process |

### Events

| Suffix | Means |
|---|---|
| `Event` | something that happened, delivered to a handler |
| `Hook` | the protocol for one callback slot |
| `Handler` | the callable type for one hook |

### Errors

`Error`. Thirty-five of them and they are already consistent.

### The `-er` rule

An agent noun is not a closed word but a derivation: **`Xer` performs the verb `x`, and `x`
must be in the verb dictionary.** `Renderer`/`render`, `Planner`/`plan`, `Recorder`/`record`,
`Reconciler`/`reconcile`, `Scheduler`/`schedule`, `Loader`/`load`, `Router`/`route`,
`Adapter`/`adapt`, `Inspector`/`inspect`, `Resolver`/`resolve`, `Presenter`/`present`.

This makes the family self-checking, and it immediately catches `Reactor` — there is no verb
`react` in the dictionary, and the class actually schedules re-renders in response to topic
traffic.

## Prefixes

**No single-letter prefixes.** `RText`, `RTime`, `RSection`, `RPanel`, `RGroup`, `RContent`,
`RCard` and `RCardField` are measurement-realized nodes. The letter is unreadable and the
word is `Measured`.

**A prefix that repeats the module name is redundant.** `squid_layouts.scene` exports 39
classes that all begin with `Scene`. Under the rule you approved — *one word, two altitudes,
only when one lowers to the other* — `scene.Text` is exactly what `primitives.Text` lowers
to, so the prefix is carrying no information the import path does not already carry. The same
argument does **not** apply to `Discord*`, `Classic*`, `Durable*` or `Replicated*`, where the
prefix names a real variant that coexists with its alternatives in one namespace.

## Verbs

The six terminating verbs are unchanged and already enforced: `close`, `detach`, `finish`,
`cancel`, `discard`, `run`. The families below are the new half.

### Render and update

| Verb | Means | Does I/O |
|---|---|---|
| `render` | produce a projection from current state | no |
| `invalidate` | mark stale so the next render recomputes | no |
| `refresh` | re-render and deliver the result | yes |

`Mount` currently has `invalidate`, `flush`, `refresh` and `refresh_now` — four words, three
meanings. `flush` and `refresh_now` fold into `refresh`. Persistence `flush`
(`PersistedPool.flush`, `DurableSessionRuntime.flush`) names a different subject — writing
pending bytes — and stays.

### Serialize

Three pairs, each with a sharp job, and no fourth:

| Pair | For | Example |
|---|---|---|
| `dumps` / `loads` | JSON text, on a class named `*Codec`, mirroring `json` | `scene.Codec` |
| `encode` / `decode` | a value to and from bytes or an opaque string | `KindKeyCodec`, `ClaimToken` |
| `parse` / `format` | **user-entered text** to and from a value | form fields |

`format_prefill` becomes `format`. The three exist because they have three audiences — a
protocol, a wire, and a person — and collapsing them further would lose that.

### Read

| Verb | Means |
|---|---|
| `get` | one item by key, or `None` |
| `list` | enumerate items |
| `snapshot` | a read-only view of a live subject, returning a `*Snapshot` |
| `inspect` | a diagnostic read, for tools rather than logic |

### Write

| Verb | Means |
|---|---|
| `put` | store one item by key |
| `delete` | remove one item permanently |
| `clear` | remove everything |
| `purge` | remove by predicate |

`drop` retires into `delete`; `purge_expired` becomes `purge`; `list_records` becomes `list`
under rule 3, because a `*Store`'s own name already says what it holds.

### Lease

`reserve` → `claim` → `release` or `abandon`, with `commit` for the durable write. Exempt as
a domain term of art, listed here so it reads as deliberate rather than as drift.

### Predicates and decisions

| Shape | Returns |
|---|---|
| `is_*`, `has_*`, `supports_*` | `bool` |
| `check` | a `*Decision` |

`ReplacementProtection.allows` and `DevToolsPolicy.allows` return `bool` and become `permits`
— or `is_permitted`, pending the call below. `AccessPolicy.check` already returns a decision
and is the model.

### Constructors

Both `of` and `from_<source>` stay, and neither retires. Rule 2 is there to stop a reader
being misled, and a constructor's source is never ambiguous: nobody misreads
`Opener.of(interaction)` or `UndoPlan.from_commit(commit)`. Forcing one spelling buys
uniformity at the price of `from_interaction(interaction)`, and a name that stutters is a
worse name by the standard the rest of this document is held to.

The real defect here is narrower. **A classmethod that does not return its own class is not
a constructor and must not be spelled like one.** `Scope.of(opener)` returns a
`SessionScope`, not a `Scope`, so the name misdirects — a rule 3 violation rather than a rule
2 one, and the only one in this family. It becomes `Scope.resolve(opener)`, which reuses the
dictionary verb that already means "turn an abstract selector plus a context into the
concrete thing": `Router.resolve`, `TargetRegistry.resolve`, `PositionPolicy.resolve`.

### Methods are verb phrases

A member named with a bare noun is a property, not a method. `Pattern.component()`,
`Composition.files()` and `EditorSection.form()` are methods spelled as nouns; they become
properties, or gain the verb they actually perform (`build_component`, `attachment_files`).

## The trio, re-derived

Applying the dictionary rather than choosing a metaphor:

**`Screen`** is a frozen dataclass of `name`, `scope`, `policy`, `capacity`, `quota`,
`domain`, `access` and `options`, reused across every opening. That is a **`Spec`**. It is
not a `Policy` — it *holds* one — and it is not a screen, because nothing about it is
visual. It becomes **`ScreenSpec`**, and its `policy: SessionPolicy` field, which is
`{limit, collision}`, is an occupancy rule rather than an injected decision-maker.

**`Session`** survives. It is a bounded interaction with a set of participants, fourteen
dependent names already agree with it, and `squid-stores` speaks it independently.

**`Mount`** survives, under the discord.py/Vue mirroring exemption. It is Vue's own word for
binding a component to a render target, and the alternatives all collide: `Panel` is an exact
primitive, `View` is discord.py's, `Surface` and `Presentation` are taken. What must change is
the *family* around it — `MountLocator` becomes `FrontendAddress`, and `MountSnapshot` and
`MountState` now have non-overlapping definitions rather than a near-synonym pair.

The honest summary is that the trio was mostly right and its neighbours were wrong.

## Settled calls

The seven the dictionary could not make on its own, and what they were decided to be.

1. **Drop the `Scene` prefix.** 39 renames. `scene.Text` is what `primitives.Text` lowers to,
   so the prefix repeats the import path and carries nothing.
2. **`semantic.Destination` → `NavOption`.** `delivery.Destination` is the load-bearing
   protocol and keeps the word.
3. **`semantic.Progress` → `ProgressBar`.** `operations.Progress` is the capability an
   operation reports through; the semantic node is a bar and should say so.
4. **`allows` → `permits`.** Reads correctly at the call site and stays a verb.
5. **`Inspection` joins the suffix set; nothing is renamed.** `MountInspection` embeds
   `MountSnapshot` rather than competing with it, so rule 2 was never in danger.
6. **Drop the short `forms` aliases.** `Text`, `Time`, `Choice`, `Bool`, `Int`, `Float`,
   `Date`, `DateTime`, `Duration`, `MultiChoice`, `Scale` and `TextArea` retire; only the
   `*Field` spellings survive. They aliased a field rather than lowering to anything, so the
   two-altitudes rule never covered them — which also lets `Choice`, `Text` and `Time` leave
   `SAME_CONCEPT_TWO_LAYERS`, shrinking the exception set to the four genuine
   semantic/primitive pairs.
7. **`MountOptions` stays, exempt.** A `TypedDict` of keyword arguments is typing machinery,
   not a `Spec`.

## What the test enforces

`tests/architecture/test_naming.py` enforces:

- retired suffixes and callable names across public, private, module, and nested definitions
- retired identifier words across attributes, parameters, and local variables
- annotation-guided vocabulary for words that remain valid in unrelated contexts
- the agent-noun, single-letter-prefix, one-name-one-class, and lifetime-verb rules

Suffix and verb checks are denylists. The exported surface is too varied for a useful
allowlist: it would reject domain nodes such as `Heading`, `Paragraph`, and `Gallery`, or
require more exceptions than rules. New synonyms fail without constraining new concepts.

## Exceptions and boundaries

- `FrontendAddress` and `MountAddress` stay separate because one is durable and
  frontend-neutral while the other carries typed Discord coordinates.
- `BusySpec` is an interim-paint recipe, not a settled result.
- `UndoMode` is an enum; `Policy` remains reserved for injected decision-makers.
- `semantic.Summary` keeps the HTML `<summary>` term of art.
- `PatternControls.content/action/choices/form` mirror the module-level factories.
- The naming checks cover the six packages. Application repositories keep their own domain
  verbs where changing them would alter API operation names.

Scene, durability, profiler, and durable-store formats use the vocabulary-correct shape as
protocol or schema v1; no pre-reset wire compatibility is retained.

The redundant `Discord*` prefix inside `squid_discord` remains an explicit public-API
decision; target and adapter types outside that package still need the prefix.
