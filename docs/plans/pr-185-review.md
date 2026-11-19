# PR 185 review follow-up

## Completion audit — 2026-11-17

All 61 inline requests have been implemented or investigated with the decisions
below. Original wording and links remain in the inventory. The initial audit was
against `64aeb5611e7c969f989460ac76b17eebd0dcf662`; implementation continued in this
checkout after the stack merged.

GitHub was rechecked on 2026-11-17: PR 185 is merged, with published head
`962c2f71ca42b23a7d298a927b255d981c98e1c3`. All 61 threads remain unresolved
(two anchors are outdated). These follow-ups are local commits on `master`;
no replies, thread resolutions, pushes, or published PR updates were made.

## P1 Permission subjects and guild boundaries

`Actor` supplies trusted application identity. Discord adapters reject membership
from a different guild before account lookup and remove guild roles/administration
in DMs while retaining owner identity (`f07b46cd3`). Anonymous checks do not create
accounts. The resolver consumes a minimal `PermissionContext` protocol, without
Discord membership fields; adapter facts become typed rules before resolution.

`SubjectRecords` carries its exact actor/context. Repository loading accepts that
actor, and assembly rejects a different actor (`42af21979`).
`PermissionRuleSnapshot` owns the one conversion from persisted definitions,
grants, assignments, and the Manage Server bridge to parsed resolver inputs
(`228bf9efe`). Database IDs become role keys and parsed patterns there; node names
are extracted only for persistence or presentation. No independent reverse maps
remain in the permission service. Cache keys include membership and bridge facts;
epoch changes invalidate loaded rules.

Coverage includes matching/foreign guilds, DMs, plain users, owners, subject binding,
role membership, bridge precedence, API authorization, and epoch invalidation.

## P2 Route-owned private responses

Routes/routers declare private-response policy through the OpenAPI extension
`x-squid-private-response`. Registered routes supply matching;
the outer ASGI wrapper also covers unmatched descendants and early body failures
(`637e9a9c7`). Private success, authentication, validation, and oversized-body errors
retain no-store/no-cache headers. Public endpoint policies remain independent.
Request-body ownership already uses route metadata; no duplicate path list remains.

## P3 Submission sources and typed drafts

`bebc091e0` replaces active build/submission terminology with source submission,
sponsor attribution, and tag origin, including errors, serialized documents,
consumers, translations, and build history. Historical migration inputs and review
quotes retain their original spelling. The source naming migration rewrites known
retained documents and history without adding revisions; collisions abort.

There is one strict schema-1 finalization payload, with optional validated sponsor
data. The one-time migration checks the old job and original-input digests before
rewriting both payloads and digests transactionally. Corruption aborts the rewrite.
The runtime codec accepts only the current schema; old version classes and
compatibility fixtures are removed. Downgrade emits the latest retired shape with
all facts retained and updates both digests. Rollback/source-origin regressions
are covered in `e9bd6e067`.

`80d40c7cb` separates common `BuildDraft` facts from `DoorDraftDetails`,
`ExtenderDraftDetails`, and `GeneralDraftDetails`. An undecided draft can retain a
candidate detail object. Selecting a compatible category preserves it; selecting a
different kind clears its details and preserves common fields. Creation/finalization
reject category/detail mismatches. Inference, guided submission, prefill, and
recalculation narrow the selected detail type. Retained inference documents keep
their flat wire representation, with discrimination at that serialization boundary.

Round-trip, sponsored/unsponsored, unsupported-version, sponsor consistency,
category-change, retained-input corruption, and PostgreSQL migration tests pass.
Schema artifacts were regenerated; Alembic has one head, `c9f3a7e1b5d0`.

## P4 Typed catalogue and explanations

`Catalogue` implements `Mapping[str, PermissionNode]` with key iteration and typed
node expansion. Role declarations accept convenient strings but retain parsed
`Pattern` values internally (`01ae0e63f`, `94ecf6bf6`). Rank is an ordered named
representation explaining specificity, scope, subject, and origin precedence;
trace loss reasons form a closed literal type, including ties. Explanations retain
deterministic deny/forbid/owner behavior.

Keep one `rules_from_role` signature: all valid input arguments produce the same
rule tuple. Overloads would repeat this contract without preserving additional
type information. Mapping, expansion, precedence property, and trace tests cover
these choices.

## P5 Role graph correctness and cost

`RoleGraph` validates missing roles, cycles, and depth before expansion; mutation
validation uses the same graph. A snapshot reuses shared subtrees, including their
exclusions, without allowing cached subtree height to bypass depth validation.
Independent roles can still grant nodes subtracted from another role.

A deterministic benchmark measured repeated matching at 0.182 microseconds cached
versus 0.265 direct; expanding a 100-parent graph took 0.069 ms. Parsed patterns and
matching have bounded immutable-value caches (4096/8192 entries), and each catalogue
owns at most 1024 expansion entries (`94ecf6bf6`). A new catalogue cannot inherit
another catalogue's wildcard expansion; subject rules remain epoch-bound. Tests
cover graph errors, diamonds with expansion call counts, subtraction, dynamic
wildcards, and epoch changes without timing assertions.

## P6 Permission persistence and vocabulary

Persisted definitions are `PermissionRole`; membership is
`PermissionRoleAssignment`. The former ambiguous record aliases are gone.
Transactional row interfaces consistently use Repository (`42af21979`): permissions,
settings, accounts/authentication, edits, tags, builds, schematic metadata/jobs,
submissions/attachments, notifications, search projections, and existing record,
voting, message, post, and event repositories. `ArtifactStore`, `LocalArtifactStore`,
and `S3ArtifactStore` retain Store because they own bytes rather than transactional
rows. No compatibility aliases preserve the inconsistent vocabulary.

Built-in patterns remain code-owned. Stored additions/exclusions are explicit
extensions; code exclusions are always applied after union. Deployments expand
wildcards against the current catalogue. Protected definitions require the owner
for structural changes, and the owner role cannot be assigned. This keeps the
owner-only boundary independent of database overrides.

Application administration accepts tagged `AccountGrantSubject` or
`ExternalRoleGrantSubject`, preventing invalid nullable combinations (`228bf9efe`).
Retain the existing grant/assignment tables: exact-one subject, guild, and uniqueness
constraints already enforce their representation. A shared subject table would
introduce joins/lifecycle management; separate membership tables would duplicate
queries, auditing, and account-merge handling. Neither improves the validated
storage invariant. Conversion to constrained columns stays at the persistence
boundary; no permission schema migration is needed.

`AuditAction`, `AuditSubjectKind`, and `PatternMode` have one domain definition;
repositories persist their values. Role reads separately aggregate patterns and
edges in SQL before outer joining roles, avoiding a pattern/edge product and
preserving empty roles. Permission unit/property and PostgreSQL tests cover both
subject kinds, empty roles, graph composition, built-ins, uniqueness, merges, and
transactional audit writes.

## P7 Fuzz campaign diagnostics

`committed_contract_path` names the reviewed contract; preflight compares parsed
live JSON against it before generation (`9fac4fdd7`). The container requires and
validates the parent's explicit fake port before child startup.

`6fd12a85b` records bounded diagnostic artifacts with stage, exception type/message,
chained cause, and traceback for attestation, reset, contract, launch, and malformed
reports. Known persona/seed credentials and the run sentinel are redacted, alongside
credential headers and URL secrets; artifacts are capped at 32 KiB.

Inspected the installed pinned Schemathesis 4.24.2 `engine.events` and
`reporting.ndjson.serialize`: FatalError uses `exception`, NonFatalError uses `value`,
and both encode `{type, message}`. These are separate contracts, not speculative
fallbacks. `CampaignEvent` decodes names/statuses/stop reasons once into enums;
classification consumes typed evidence. Unknown/malformed events and wrong versions
fail closed. Keep the security check allowlist: it is the campaign's explicit check
policy, independent of event classification. Failure-limit completion preserves
product findings; transport failures and timeouts retain their distinct states.
Tests cover every stage, both real error envelopes, wrong versions, unknown fields,
report limits, redaction, and multibyte/control-character diagnostic bounds.

## P8 Concurrency and runtime ownership

`f4aad3864` returns tagged `TaskSuccess`/`TaskFailure` from `settle_all`, preserving
exceptions returned as ordinary data. Ordered indexed results need no placeholder
casts. Cancel-on-failure batches own temporary inputs, reaction reconciliation, and
post removals; removal handles expected Discord failures locally. Durable media and
schematic jobs settle before raising so each job records its outcome. Vote rendering
uses best-effort tagged outcomes and reports each destination failure.

A minimal async fixture reproducer under installed pytest-asyncio 1.4.1.post1
confirmed that setup, test, and teardown use different tasks: both
`current_task() is setup_task` comparisons were false. AnyIO cancellation scopes
must exit in their entering task. Retain lexical supervisor `async with` in these
tests. The existing UI test harness wraps the test coroutine itself in the host's
task group; an async-yield supervisor fixture would violate ownership rather than
improve reusable setup. Concurrency/supervisor/starboard coverage retains ordering,
capacity, cancellation, coalescing, and shutdown assertions.

LISTEN wakes the poller sooner; polling remains authoritative after missed
notifications, and cancellation ends the listener. Inspected installed asyncpg
annotations: supplying `connection_class=asyncpg.Connection` selects the precise
connection type and removes the cast (`2eb824f36`). PostgreSQL DSN validation accepts
SQLAlchemy driver-qualified forms and preserves credentials/query options
(`c4511c1e9`). Cache arguments consistently say epoch; built-in runtime jobs use a
stable string enum, with strings retained for genuinely dynamic caller jobs.

## P9 Configuration, versions, and vote setup

Narrow reusable settings models live at module scope; loaders read the environment
at call time (`c4511c1e9`). Tests change environment between loads without needing
unrelated process secrets.

`559cd0514` returns names and version entities in one joined lookup, handles empty
input without SQL, and computes missing names with a set difference. Keep that
small Python difference: an input-relation anti-join would complicate parameter
construction without reducing the single round trip. Duplicate/unknown-name tests
preserve useful errors.

Configured vote channels are narrowed to native guild messageable types and
deduplicated by channel ID. Setup batches presets for distinct guilds in one read,
with defaults supplied by the vote service, then creates/reuses the session under
its existing advisory lock and asks reconciliation to publish. No channels remains
a successful no-card outcome for an already saved build. Tests cover multiple
guilds, duplicates, current thresholds, missing configuration, and retry-safe setup.

## Existing dispositions and validation

Idempotency encryption uses field/model validation without constructing a discarded
config object. Capability protocol bounds use `CURRENT_SUBMISSION_PROTOCOL`.
The REST base URL derives from discord.py's `Route.BASE` (client ownership now lives
in `squid/discord_rest.py`). The obsolete untyped reaction-outcomes helper is gone;
reaction handling belongs to `squid/bot/voting/reactions.py`.

The final full local suite passed: 8,982 tests, with 20 opt-in cases skipped.
Whole-project Pyrefly reports zero errors. Final PostgreSQL checks cover all
migration, history, finalization, and API-key tests; durable-job and worker-wire
regressions also pass. Commit hooks format and lint changed files. Generated
OpenAPI/schema artifacts and `git diff --check` are current.

## Complete comment inventory

The section references assign each comment once. Duplicate comments remain separate source records
but share implementation work. Comment text is quoted verbatim, including links and spelling.

All 61 comments have a local implementation or the explicit design disposition above.
These statuses describe code, not GitHub thread resolution.

### 01 Comment 3886685322

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3886685322) — **Satisfied**, P9.
Original path: `squid/core/builds/infrastructure/repository.py`.

> I feel like there is a way to do this in SQL directly, though idk whether it would be better.

### 02 Comment 3886688164

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3886688164) — **Satisfied**, P2.
Original path: `squid/api/private_responses.py`.

> not a good idea hardcoding paths outside of their definiton

### 03 Comment 3886689809

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3886689809) — **Satisfied**, verified disposition above.
Original path: `squid/api/request_body.py`.

> same here hardcoded paths

### 04 Comment 3886726334

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3886726334) — **Satisfied**, verified disposition above.
Original path: `squid/config.py`.

> hmm, not sure how I feel about constructor as validation

### 05 Comment 3886742891

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3886742891) — **Satisfied**, P9.
Original path: `squid/bot/voting/build_session.py`.

> not a fan of this

### 06 Comment 3886755107

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3886755107) — **Satisfied**, P9.
Original path: `squid/bot/voting/build_session.py`.

> we are definitely doing something wrong in this function

### 07 Comment 3888309632

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3888309632) — **Satisfied**, P3.
Original path: `squid/core/builds/application/services.py`.

> https://github.com/redstone-squid/Redstone-Squid/blob/efd55aca6ad8dabc72905d66a9bb358ef39abb3d/squid/core/builds/application/services.py#L139-L139
>
> ban "provenance"

### 08 Comment 3888328584

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3888328584) — **Satisfied**, P3.
Original path: `squid/core/submissions/infrastructure/finalization_repository.py`.

> move the schema back to v1, we don't care about backwards compatibility

### 09 Comment 3888395244

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3888395244) — **Satisfied**, verified disposition above.
Original path: `squid/api/v1/capabilities.py`.

> definitely should be CURRENT_SUBMISSION_PROTOCOL here

### 10 Comment 3894201080

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3894201080) — **Satisfied**, verified disposition above.
Original path: `squid/core/voting/infrastructure/discord_rest.py`.

> don't duplicate constants

### 11 Comment 3894339673

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3894339673) — **Satisfied**, P7.
Original path: `tests/fuzz/api/schemathesis.py`.

> what is a canonical path

### 12 Comment 3894438678

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3894438678) — **Satisfied**, P7.
Original path: `tests/fuzz/api/schemathesis.py`.

> seems like we are losing important debugging information by not even storing the error

### 13 Comment 3894477298

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3894477298) — **Satisfied**, P7.
Original path: `tests/fuzz/api/schemathesis.py`.

> too many string names. StrEnum.

### 14 Comment 3894480352

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3894480352) — **Satisfied**, P7.
Original path: `tests/fuzz/api/schemathesis.py`.

> so many rules...

### 15 Comment 3894483154

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3894483154) — **Satisfied**, P7.
Original path: `tests/fuzz/api/schemathesis.py`.

> do we really have to do this

### 16 Comment 3899722246

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3899722246) — **Satisfied**, P7.
Original path: `tests/fuzz/api/container_entrypoint.py`.

> i think we should require FAKE_PORT_ENV and have no fallback to prevent subtle bugs

### 17 Comment 3904128562

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3904128562) — **Satisfied**, P8.
Original path: `tests/unit/bot/test_starboard.py`.

> Seems like either we have a problem, or pytest have a problem that we should work around differently, because forcing running() inline doesn't seem like a good fix

### 18 Comment 3904310414

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3904310414) — **Satisfied**, verified disposition above.
Original path: `squid/bot/voting/base_session.py`.

> why is outcomes object...

### 19 Comment 3909670026

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3909670026) — **Satisfied**, P8.
Original path: `squid/core/concurrency.py`.

> TODO: rethink run_all and settle_all

### 20 Comment 3913994966

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3913994966) — **Satisfied**, P4.
Original path: `squid/core/permissions/domain/catalogue.py`.

> looks like a Mapping

### 21 Comment 3914025743

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3914025743) — **Satisfied**, P4.
Original path: `squid/core/permissions/domain/catalogue.py`.

> I feel like these should be Patterns or PermissionNodes

### 22 Comment 3914122595

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3914122595) — **Satisfied**, P5.
Original path: `squid/core/permissions/domain/matching.py`.

> caching?

### 23 Comment 3914166345

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3914166345) — **Satisfied**, P4.
Original path: `squid/core/permissions/domain/resolution.py`.

> lol we are just gonna not add docstring on the single thing that needs it the most

### 24 Comment 3914168367

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3914168367) — **Satisfied**, P4.
Original path: `squid/core/permissions/domain/resolution.py`.

> i dont understand

### 25 Comment 3914219347

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3914219347) — **Satisfied**, P1.
Original path: `squid/core/permissions/domain/resolution.py`.

> discord being priviledged here, hopefully already fixed

### 26 Comment 3914240212

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3914240212) — **Satisfied**, P4.
Original path: `squid/core/permissions/domain/resolution.py`.

> if "TIE" has a special meaing this prob shouldnt be a str

### 27 Comment 3914251156

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3914251156) — **Satisfied**, P4.
Original path: `squid/core/permissions/domain/resolution.py`.

> shouldn't be str?

### 28 Comment 3914294269

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3914294269) — **Satisfied**, P5.
Original path: `squid/core/permissions/domain/resolution.py`.

> no error, really?

### 29 Comment 3914301137

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3914301137) — **Satisfied**, P5.
Original path: `squid/core/permissions/domain/resolution.py`.

> this feels horrifyingly inefficient

### 30 Comment 3914310052

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3914310052) — **Satisfied**, P5.
Original path: `squid/core/permissions/domain/resolution.py`.

> this exclusion also feels inefficient

### 31 Comment 3914312246

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3914312246) — **Satisfied**, P4.
Original path: `squid/core/permissions/domain/resolution.py`.

> overload?

### 32 Comment 3919902896

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3919902896) — **Satisfied**, P4.
Original path: `squid/core/permissions/domain/catalogue.py`.

> why the names instead of PermissionNode?

### 33 Comment 3919906535

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3919906535) — **Satisfied**, P4.
Original path: `squid/core/permissions/domain/catalogue.py`.

> why not Pattern or PermissionNodes

### 34 Comment 3919914429

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3919914429) — **Satisfied**, P4.
Original path: `squid/core/permissions/domain/resolution.py`.

> lol we are just gonna not have docstring only for the most confusing type ever

### 35 Comment 3919916097

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3919916097) — **Satisfied**, P4.
Original path: `squid/core/permissions/domain/resolution.py`.

> kinda confusing

### 36 Comment 3919920324

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3919920324) — **Satisfied**, P1.
Original path: `squid/core/permissions/domain/resolution.py`.

> discord is being privileged here, hopefully already fixed

### 37 Comment 3919924259

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3919924259) — **Satisfied**, P4.
Original path: `squid/core/permissions/domain/resolution.py`.

> if we have some special strings expected then maybe this should be a literal?

### 38 Comment 3919928215

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3919928215) — **Satisfied**, P4.
Original path: `squid/core/permissions/domain/resolution.py`.

> str is more convenient for users but the actual types is better for internal usage, not sure which one is beter here (or in other palces)

### 39 Comment 3919930158

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3919930158) — **Satisfied**, P5.
Original path: `squid/core/permissions/domain/resolution.py`.

> this seems horrifyingly inefficient

### 40 Comment 3919938251

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3919938251) — **Satisfied**, P4.
Original path: `squid/core/permissions/domain/resolution.py`.

> Seems like a literal should be returned

### 41 Comment 3933780012

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3933780012) — **Satisfied**, P6.
Original path: `squid/core/permissions/application/ports.py`.

> AssignmentRecord is such a bad name

### 42 Comment 3933843633

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3933843633) — **Satisfied**, P1.
Original path: `squid/core/permissions/application/services.py`.

> not quite sure whats the point of these two reverse mapping

### 43 Comment 3933849494

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3933849494) — **Satisfied**, P6.
Original path: `squid/core/permissions/application/services.py`.

> inconsistent naming we need a full pass on repository/store etc

### 44 Comment 3933860822

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3933860822) — **Satisfied**, P1.
Original path: `squid/core/permissions/application/services.py`.

> this feels very roundabout

### 45 Comment 3933873719

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3933873719) — **Satisfied**, P1.
Original path: `squid/core/permissions/application/services.py`.

> why do we have the Subject and its records? why doesnt the Subject carry the records or the records carry the subject?

### 46 Comment 3934049383

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3934049383) — **Satisfied**, P6.
Original path: `squid/core/permissions/infrastructure/models.py`.

> is this really a good idea

### 47 Comment 3934356660

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3934356660) — **Satisfied**, P6.
Original path: `squid/core/permissions/infrastructure/models.py`.

> so many conditionally nullable stuff

### 48 Comment 3934460539

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3934460539) — **Satisfied**, P6.
Original path: `squid/core/permissions/application/ports.py`.

> if this doesn't get renamed back to RoleRecord in later commits...

### 49 Comment 3934465499

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3934465499) — **Satisfied**, P1.
Original path: `squid/core/permissions/application/services.py`.

> not sure whats the point of these two reverse mapping functions

### 50 Comment 3939283184

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3939283184) — **Satisfied**, P6.
Original path: `squid/core/permissions/infrastructure/models.py`.

> text is probably correct but we need to use a enum on the app level

### 51 Comment 3939326951

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3939326951) — **Satisfied**, P6.
Original path: `squid/core/permissions/infrastructure/models.py`.

> enum?

### 52 Comment 3939334832

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3939334832) — **Satisfied**, P6.
Original path: `squid/core/permissions/infrastructure/repository.py`.

> pretty sure this is defined more than once

### 53 Comment 3939363209

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3939363209) — **Satisfied**, P6.
Original path: `squid/core/permissions/infrastructure/repository.py`.

> what is this garbage. Do it in SQL.

### 54 Comment 3939395786

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3939395786) — **Satisfied**, P8.
Original path: `squid/core/persistence/wake_listener.py`.

> wtf is a "latency hint"

### 55 Comment 3939413064

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3939413064) — **Satisfied**, P8.
Original path: `squid/core/persistence/wake_listener.py`.

> hmm why do we need a cast here

### 56 Comment 3939413767

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3939413767) — **Satisfied**, P8.
Original path: `squid/core/persistence/wake_listener.py`.

> hmm the url being asyncpg is an unspoken assumption

### 57 Comment 3939501439

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3939501439) — **Satisfied**, P8.
Original path: `squid/core/permissions/application/cache.py`.

> why version when it is epoch elsewhere

### 58 Comment 3939513182

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3939513182) — **Satisfied**, P8.
Original path: `squid/runtime.py`.

> hmm maybe the names should be a strenum

### 59 Comment 3994767147

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3994767147) — **Satisfied**, P1.
Original path: `squid/bot/utils/permissions.py`.

> https://github.com/redstone-squid/Redstone-Squid/blob/4dde885e91911d7be9b4770a7a0058299f7f154d/squid/bot/utils/permissions.py#L102-L102
>
> this is ignoring member.guild?

### 60 Comment 4433039333

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r4433039333) — **Satisfied**, P3.
Original path: `squid/core/builds/domain/models.py`.

> This is still merging all categories into one and kinda ugly

### 61 Comment 4433060379

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r4433060379) — **Satisfied**, P9.
Original path: `squid/config.py`.

> why a class in a function...
