# PR 185 review follow-up plan

## Scope and audit baseline

Collect all 61 inline comments by `Glinte` on
[PR 185](https://github.com/redstone-squid/Redstone-Squid/pull/185), including repeated concerns and the
two comments submitted on 2026-10-31. The two review summaries contain an empty body and `.`; neither
adds a request. There are no general PR conversation comments by `Glinte`.

Audit date: 2026-10-31. PR head: `64aeb5611e7c969f989460ac76b17eebd0dcf662`
(`schematics-phase-0-stack-01`). Implementation baseline: `ef445bcdee2dadf4d23f6def7b0e78b4e7f61bd5`
on `schematics-phase-0-stack-19`, which descends from that head. Status below describes this checkout,
including later stack work, rather than claiming fixes are already in PR 185's isolated diff.
GitHub reports all 61 threads unresolved and none outdated; neither flag establishes whether later
code fixed a concern. REST comment pagination and GraphQL thread pagination were exhausted.

The inventory at the end preserves every comment's text and link once. **Satisfied** means the
requested behavior is present or the criticized code was removed with its responsibility replaced.
**Partial** means related improvements landed but the concern remains. **Open** includes design
questions that need an explicit decision, rather than assuming a suggested implementation is mandatory.
This is a planning change; implementation and GitHub thread updates are separate work.

## Verified dispositions requiring no new implementation

| Comment | Current evidence |
| --- | --- |
| 3886689809 | `squid/api/request_body.py` uses `streams_own_body` endpoint metadata and registered route regexes. `squid/api/app.py` passes the routes; media, schematic, and intake endpoints carry the marker. The hardcoded upload path is gone. Coverage: `tests/unit/api/test_transport_boundaries.py`. |
| 3886726334 | `ApiConfig._validate_idempotency_encryption` in `squid/config.py` calls `_require_active_idempotency_key`; field validators validate the key ID and keys. It no longer constructs and discards `IdempotencyEncryptionConfig` to validate. |
| 3888395244 | `squid/api/v1/capabilities.py:capabilities` already uses `CURRENT_SUBMISSION_PROTOCOL` for both interval endpoints. This was also true at the linked commit; no additional constant replacement is needed. |
| 3894201080 | `squid/voting/infrastructure/discord_rest.py` derives `DEFAULT_DISCORD_API_URL` from discord.py's `Route.BASE`, rather than repeating a URL literal. |
| 3904310414 | `base_session.py` and `log_reaction_failures(Sequence[object])` are gone. Reaction reconciliation and failure handling now live in `squid/bot/voting/reactions.py`; the criticized untyped outcomes helper has no remaining call sites. |

## Implementation sequence

Each numbered section is a work package, not necessarily one commit. Commit independently valid
steps within each package. Start with the guild boundary in P1, then P2 and P3. P4 establishes the
permission types used by P5 and P6; implement those in that order. P7, P8, and P9 can follow separately.
Only unfinished portions of partial items need work.

### P1 Permission subjects and guild boundaries

**Evidence:** `squid/bot/utils/permissions.py:build_subject` takes roles and Manage Server authority
from a `discord.Member`, but independently accepts `guild_id`. It never checks `member.guild.id`
against that context. `Subject` in `permissions/domain/resolution.py` still embeds Discord fields,
and `PermissionService.assemble(records, subject)` accepts independently supplied records and subject.

- Define the contract for a member whose guild differs from the requested context. Reject mismatches
  or resolve the member in the target guild before collecting roles; never carry administrative
  authority from one guild into another. Treat an absent guild as a global/DM context explicitly.
- Move Discord membership and Manage Server translation to the Discord adapter. Give the permission
  core typed external principals and contextual grants while preserving account, owner, and bridge
  precedence. An unauthenticated lookup must still avoid creating an account.
- Bind loaded permission inputs to their subject/context so assembly cannot pair one user's records
  with another user. Keep durable rule data separate from transport objects and time-varying identity.
- Consolidate `_role_specs`, `_grant_rule`, and bridge/assignment assembly into a single explicit
  conversion boundary after P4's types are available. Document which conversions remain necessary
  between database values and resolver inputs.

**Acceptance:** extend `tests/unit/bot/utils/test_permissions.py` for matching guild, different guild,
DM, plain user, and owner cases. Extend permission service/cache tests for subject binding, role
membership changes, and bridge precedence. Existing account-only/API authorization must remain valid.

### P2 Route-owned private response policy

**Evidence:** `squid/api/private_responses.py` still maintains `PRIVATE_API_PATH_PREFIXES`, separately
from route declarations. Request-body exemptions are already fixed and provide a local precedent.

- Declare privacy/cache policy with the endpoint or router and derive middleware matching from the
  registered routes. Remove the duplicate path list.
- Preserve `Cache-Control: no-store` and `Pragma: no-cache` on private success and error responses,
  including errors produced before endpoint execution. Account explicitly for the existing prefix
  behavior on unrecognized paths below private routers.
- Cover route renaming/prefix changes and a newly registered private route without middleware edits;
  public endpoints must retain their own caching policy.

**Acceptance:** use `tests/unit/api/test_transport_boundaries.py` and the relevant API response tests;
verify authentication, validation, and oversized-body failures as well as successful responses.

### P3 Submission terminology, payload schema, and category drafts

**Evidence:** the original builds-service wording moved, but `provenance` remains in submission
errors, `_submission_provenance`/`_sponsor_provenance` in `submissions/infrastructure/build_target.py`,
and the `submission_provenance` document key. The codec moved to
`submissions/infrastructure/finalization_payloads.py` and still chooses V1/V2 by sponsor attribution.
`builds/domain/models.py:BuildDraft` still has flat door and extender fields despite typed final builds.

- Replace the banned terminology in active build/submission APIs, errors, identifiers, and docs with
  concrete names such as source submission, submitter, or sponsor attribution. Audit serialized keys
  and consumers when renaming; moving the old wording is not a fix. Historical migrations and quoted
  review text are evidence, not current naming guidance.
- Collapse `FinalizationPayloadV1 | FinalizationPayloadV2` to one current schema with
  `payload_schema = 1`, including optional sponsor data and its consistency validation. Remove the
  V2 discriminator, branching encoder/decoder, and compatibility fixtures. This follows the explicit
  request to reset the schema rather than preserve backward compatibility. Specify a one-time rewrite
  or reset for retained development payloads and update their digests together; do not silently relabel
  stored data without updating its integrity checks.
- Split common draft fields from typed category details. Support an undecided category during
  inference, then select door/extender/other details explicitly. Define how changing category treats
  previously entered details, and update guided submission, inference, and finalization consumers.
  Keep the existing category-specific final build classes.

**Acceptance:** round-trip sponsored and unsponsored schema-1 payloads; reject inconsistent sponsor
data and unsupported versions. Run finalization repository integration tests for retained payloads
and digest handling. Extend `tests/unit/builds/domain/test_models.py` and submission/inference tests
for undecided category, category changes, and rejection of mixed category details.

### P4 Typed permission catalogue and explanations

**Evidence:** `Catalogue` has lookup/length methods but iterates values rather than mapping keys;
`expand` returns names. `RoleDefinition` and `RoleSpec` hold string patterns. `Rank` is an undocumented
nested tuple alias; `TraceStep.lost_on` and `_lost_on` are unconstrained strings.

- Make `Catalogue` a `Mapping[str, PermissionNode]` with key iteration and explicit `values()` use
  at node-iteration sites. Preserve deterministic ordering and the domain-specific unknown-node error.
- Return permission nodes from typed expansion; turn them into names at presentation or persistence
  boundaries. Parse include/exclude strings once into `Pattern` values. Keep role identifiers distinct
  from patterns and nodes; do not force all three into one type.
- Replace the opaque rank tuple with a named, ordered representation and explain each component,
  comparison direction, and stable tie behavior. Use a closed enum or literal type for loss reasons,
  including `tie`, shared by `TraceStep`, `_lost_on`, and trace rendering.
- Review the `rules_from_role` overload suggestion at its actual call sites. Its current arguments
  produce the same return type; add overloads only if the redesigned API has distinct valid input
  shapes, otherwise record that conclusion and simplify the signature where possible.

**Acceptance:** catalogue iteration/expansion tests and permission resolution property tests preserve
specificity, scope, owner, forbid, deny ties, and deterministic traces. Update callers and exports in
one coherent series; Pyrefly must accept the domain types without broad casts.

### P5 Role expansion correctness and cost

**Evidence:** `Pattern.matches` has no memoization. `expand_role` recursively revisits shared child
roles, repeatedly expands exclusions, and silently returns empty results for missing roles, cycles,
or exhausted depth. There is a subject-rule cache, but it does not eliminate repeated expansion within
a cache miss. Each generated rule already shares the same immutable exclusion set; do not claim it
copies that set or replace constant-time membership with a slower representation.

- Define distinct failures for missing roles, cycles, and excessive depth. Reject invalid graphs on
  mutation and surface corrupt persisted graphs with actionable diagnostics; permission evaluation
  must not silently turn an incomplete exclusion calculation into a broader grant.
- Compile the role graph once per relevant snapshot/epoch and reuse shared child expansions. Ensure
  cycle/depth detection stays correct when memoizing; an expansion that depends on a traversal path
  cannot be cached by role ID alone without validating the graph first.
- Measure pattern matching and catalogue expansion with representative shared-role graphs. Cache
  parsed patterns or immutable catalogue expansions where measurements justify it, with bounded
  storage and correct catalogue/epoch invalidation. Preserve dynamic wildcard behavior.
- Document subtraction semantics and verify that a grant from an independent role can still confer
  a node excluded by another role.

**Acceptance:** missing-node, cycle, depth-limit, diamond-graph, exclusion, and epoch-change tests;
compare decisions/traces before and after on valid graphs. Use call counts or a small benchmark to
demonstrate reduced repeated work without imposing timing-sensitive unit assertions.

### P6 Permission persistence and naming

**Evidence:** `AssignmentRecord` still exists alongside the distinct `RoleRecord`. `PermissionStore`
and `PermissionAdminStore` are implemented by repositories. Built-in roles combine code patterns and
database overrides. Grants/assignments still represent account versus Discord-role subjects with
conditionally nullable columns. Audit action/subject kinds are strings. Include/exclude constants are
duplicated in `application/administration.py` and `infrastructure/repository.py`. `_load_roles` still
fetches three complete tables and joins them with Python dictionaries.

- Name definitions and memberships unambiguously: retain the definition concept and use an explicit
  role-assignment name for memberships. The requested `RoleRecord` name already denotes a different
  object, so resolve both names together rather than rename into a collision.
- Inventory repository/store protocols and implementations across `squid/`, agree on one vocabulary
  based on responsibilities, and update names/imports/docs without compatibility aliases. This comment
  asks for a full naming pass; do not stop after renaming the permission service parameter.
- Make a concrete decision on built-in ownership: preserve code-owned default patterns with explicit
  persisted overrides, or move definitions into persistence with a defined update mechanism. Document
  whether built-ins can be modified and how deploys affect grants. Preserve owner-only exclusions and
  live catalogue expansion while eliminating ambiguous duplicate authority.
- Replace invalid application-level nullable combinations with tagged account/external-role subject
  types. Evaluate a shared subject table or separate assignment tables for persistence; choose and
  document the schema after mapping constraints, query costs, and account-merge behavior.
- Define enums for audit action and audit subject kind at the application boundary; persist their
  values as text. Give include/exclude mode one domain definition used by all adapters.
- Move `_load_roles` grouping into SQL using separately aggregated pattern and edge subqueries, then
  outer join them to roles. Avoid multiplying patterns by edges; preserve empty roles, deterministic
  ordering, and distinct include/exclude lists. Reuse the query for administration reads where suitable.

**Acceptance:** permission service/administration tests plus PostgreSQL integration tests for empty
roles, multiple patterns and edges, built-ins, both subject types, uniqueness, account merges, and
transactional audit writes. Any schema change needs migration coverage and `alembic heads` validation.

### P7 Fuzz harness clarity and diagnostics

**Evidence:** `tests/fuzz/api/schemathesis.py` still calls the committed OpenAPI file `canonical_path`,
discards exceptions in `run_campaign`, uses raw event/status strings, and probes alternative error
shapes in `_error_type`. `container_entrypoint.py` now centralizes port validation but still defaults
missing `FAKE_PORT_ENV` to `8101`.

- Rename the schema argument to describe the committed contract file, and state why the live schema
  must match it. Update CLI callers and docs together.
- Capture exception type, message, cause/traceback, and failing stage in bounded campaign artifacts
  for attestation, reset, contract preflight, launch, and malformed-event failures. Preserve terminal
  state classification and redact credentials.
- Decode the pinned Schemathesis event format into typed events/status enums at one boundary, then
  simplify classification over those types. Check the pinned implementation's actual error envelope
  before removing `_error_type` fallbacks; retain only documented or reproduced shapes.
- Require `FAKE_PORT_ENV` at the container entrypoint, validate it once, and pass the parsed setting to
  startup/readiness code. Ensure the parent always supplies it; missing configuration must fail before
  launching children.

**Acceptance:** `tests/unit/fuzz/test_api_schemathesis.py` and
`tests/unit/fuzz/test_api_container_entrypoint.py` cover missing/invalid ports, each failure stage,
unknown/malformed events, wrong tool version, transport errors, product findings, and bounded artifacts.

### P8 Concurrency, listeners, and runtime names

**Evidence:** `core/concurrency.py` now documents failure policies and adds `run_all_settled`, but
retains cast-filled result buffers and exception-as-value results. Starboard tests still enter
`BackgroundTaskSupervisor().running()` inline. `PostgresWakeListener` still says “latency hint” and
casts `asyncpg.connect`; `asyncpg_dsn` now names and explains conversion but rewrites any URL's driver
without validating the backend. `observe_epoch` still calls its argument `version`; runtime job names
remain strings and a formatted listener suffix.

- Audit fan-out call sites against cancel-on-failure, settle-then-raise, and best-effort requirements.
  Decide whether explicit task groups or typed outcome objects make each clearer. Address the
  ambiguity between an exception returned as data and one raised by an operation. Keep anyio ownership
  and cancellation semantics; do not introduce bare tasks or change the asyncio backend.
- Investigate supervisor setup under the installed pytest-asyncio version with a minimal lifecycle
  reproducer. Provide reusable test support that enters/exits the task group in its owning task, or
  document why lexical `async with` is required if a fixture cannot preserve that invariant. Do not
  move cancel scopes between tasks just to shorten tests.
- Explain LISTEN notifications as waking a poller sooner; polling remains authoritative when a
  notification is missed. Document that cancellation ends the listener's run.
- Inspect the installed asyncpg annotation/stub before removing the connection cast. Prefer a precise
  annotated connection or correctly typed connection-class argument; if an upstream stub forces a
  local suppression, explain it at that boundary.
- Validate PostgreSQL URL schemes in `asyncpg_dsn` and document accepted SQLAlchemy driver forms.
  Test conversion, credentials, query parameters, and rejection of non-PostgreSQL URLs.
- Rename the cache argument/local vocabulary to `epoch`; handle any database column rename explicitly.
  Define stable enum values for built-in runtime job names while allowing genuinely dynamic job names
  where callers need them.

**Acceptance:** concurrency and supervisor tests cover ordering, sibling failures, cancellation,
capacity limits, and cleanup; starboard tests retain coalescing and shutdown checks. Add listener URL,
reconnect/cancellation, and permission epoch tests appropriate to the changed behavior.

### P9 Remaining configuration, SQL, and vote setup questions

**Evidence:** `_get_versions` in `builds/infrastructure/repository.py` still computes missing names
in Python and performs a second query for entities. `load_worker_log_config` still defines
`WorkerLogSettings` inside the function; neighboring narrow loaders do the same. Vote session classes
were removed and publication moved to reconciliation, but `ensure_build_review` still deduplicates
channels and loads presets sequentially per guild.

- Compare the current version lookup with an input relation/anti-join that reports missing names and
  returns matched entities. Preserve duplicate/empty input behavior and useful errors. Adopt SQL if it
  simplifies the lookup or reduces measured round trips; the comment explicitly leaves this a design
  choice, so record the result if the small Python set difference remains preferable.
- Move reusable narrow settings models to module scope and keep environment loading at call time.
  Apply the decision consistently to neighboring loaders; verify imports do not read environment or
  dotenv values and worker-only configuration still works without unrelated process secrets.
- Finish the vote setup review around the remaining channel/guild normalization and preset loading.
  Establish uniqueness at the configuration/service boundary, then give setup a small typed input.
  Consider one batch preset read if it removes the per-guild loop. Keep the existing advisory-lock
  idempotency, reconciler publication, and no-configured-channel behavior.

**Acceptance:** version lookup tests for duplicates, empty input, and unknown names; configuration
tests that change environment between loads; `tests/unit/bot/voting/test_sessions.py` for duplicate
channels, multiple guilds, missing configuration, and retry-safe creation/publication.

## Validation and completion

For implementation, run the smallest relevant test set with `uv run --locked pytest --no-cov`, then
rerun that set after the final edit. Use the existing verified pre-commit hooks for changed-file
formatting/linting and run `just typecheck` once after code changes. Run `git diff --check`; check
`alembic heads` when migrations change. Defer the full suite to CI unless resolver/core concurrency
changes have a broad or uncertain impact that warrants a local full run.

Complete this plan when each inventory item has either implementation and focused validation, or an
explicit recorded design disposition supported by the investigation requested above. A source-file
rename, an existing cache elsewhere, or an unresolved GitHub flag alone is not a disposition.

## Complete comment inventory

The section references assign each comment once. Duplicate comments remain separate source records
but share implementation work. Comment text is quoted verbatim, including links and spelling.

Totals: 5 satisfied, 5 partial, 51 open.

### 01 Comment 3886685322

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3886685322) — **Open**, P9.
Original path: `squid/builds/infrastructure/repository.py`.

> I feel like there is a way to do this in SQL directly, though idk whether it would be better.

### 02 Comment 3886688164

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3886688164) — **Open**, P2.
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

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3886742891) — **Partial**, P9.
Original path: `squid/bot/voting/build_session.py`.

> not a fan of this

### 06 Comment 3886755107

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3886755107) — **Partial**, P9.
Original path: `squid/bot/voting/build_session.py`.

> we are definitely doing something wrong in this function

### 07 Comment 3888309632

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3888309632) — **Partial**, P3.
Original path: `squid/builds/application/services.py`.

> https://github.com/redstone-squid/Redstone-Squid/blob/efd55aca6ad8dabc72905d66a9bb358ef39abb3d/squid/builds/application/services.py#L139-L139
>
> ban "provenance"

### 08 Comment 3888328584

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3888328584) — **Open**, P3.
Original path: `squid/submissions/infrastructure/finalization_repository.py`.

> move the schema back to v1, we don't care about backwards compatibility

### 09 Comment 3888395244

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3888395244) — **Satisfied**, verified disposition above.
Original path: `squid/api/v1/capabilities.py`.

> definitely should be CURRENT_SUBMISSION_PROTOCOL here

### 10 Comment 3894201080

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3894201080) — **Satisfied**, verified disposition above.
Original path: `squid/voting/infrastructure/discord_rest.py`.

> don't duplicate constants

### 11 Comment 3894339673

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3894339673) — **Open**, P7.
Original path: `tests/fuzz/api/schemathesis.py`.

> what is a canonical path

### 12 Comment 3894438678

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3894438678) — **Open**, P7.
Original path: `tests/fuzz/api/schemathesis.py`.

> seems like we are losing important debugging information by not even storing the error

### 13 Comment 3894477298

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3894477298) — **Open**, P7.
Original path: `tests/fuzz/api/schemathesis.py`.

> too many string names. StrEnum.

### 14 Comment 3894480352

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3894480352) — **Open**, P7.
Original path: `tests/fuzz/api/schemathesis.py`.

> so many rules...

### 15 Comment 3894483154

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3894483154) — **Open**, P7.
Original path: `tests/fuzz/api/schemathesis.py`.

> do we really have to do this

### 16 Comment 3899722246

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3899722246) — **Open**, P7.
Original path: `tests/fuzz/api/container_entrypoint.py`.

> i think we should require FAKE_PORT_ENV and have no fallback to prevent subtle bugs

### 17 Comment 3904128562

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3904128562) — **Open**, P8.
Original path: `tests/unit/bot/test_starboard.py`.

> Seems like either we have a problem, or pytest have a problem that we should work around differently, because forcing running() inline doesn't seem like a good fix

### 18 Comment 3904310414

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3904310414) — **Satisfied**, verified disposition above.
Original path: `squid/bot/voting/base_session.py`.

> why is outcomes object...

### 19 Comment 3909670026

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3909670026) — **Partial**, P8.
Original path: `squid/core/concurrency.py`.

> TODO: rethink run_all and settle_all

### 20 Comment 3913994966

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3913994966) — **Open**, P4.
Original path: `squid/permissions/domain/catalogue.py`.

> looks like a Mapping

### 21 Comment 3914025743

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3914025743) — **Open**, P4.
Original path: `squid/permissions/domain/catalogue.py`.

> I feel like these should be Patterns or PermissionNodes

### 22 Comment 3914122595

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3914122595) — **Open**, P5.
Original path: `squid/permissions/domain/matching.py`.

> caching?

### 23 Comment 3914166345

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3914166345) — **Open**, P4.
Original path: `squid/permissions/domain/resolution.py`.

> lol we are just gonna not add docstring on the single thing that needs it the most

### 24 Comment 3914168367

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3914168367) — **Open**, P4.
Original path: `squid/permissions/domain/resolution.py`.

> i dont understand

### 25 Comment 3914219347

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3914219347) — **Open**, P1.
Original path: `squid/permissions/domain/resolution.py`.

> discord being priviledged here, hopefully already fixed

### 26 Comment 3914240212

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3914240212) — **Open**, P4.
Original path: `squid/permissions/domain/resolution.py`.

> if "TIE" has a special meaing this prob shouldnt be a str

### 27 Comment 3914251156

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3914251156) — **Open**, P4.
Original path: `squid/permissions/domain/resolution.py`.

> shouldn't be str?

### 28 Comment 3914294269

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3914294269) — **Open**, P5.
Original path: `squid/permissions/domain/resolution.py`.

> no error, really?

### 29 Comment 3914301137

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3914301137) — **Open**, P5.
Original path: `squid/permissions/domain/resolution.py`.

> this feels horrifyingly inefficient

### 30 Comment 3914310052

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3914310052) — **Open**, P5.
Original path: `squid/permissions/domain/resolution.py`.

> this exclusion also feels inefficient

### 31 Comment 3914312246

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3914312246) — **Open**, P4.
Original path: `squid/permissions/domain/resolution.py`.

> overload?

### 32 Comment 3919902896

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3919902896) — **Open**, P4.
Original path: `squid/permissions/domain/catalogue.py`.

> why the names instead of PermissionNode?

### 33 Comment 3919906535

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3919906535) — **Open**, P4.
Original path: `squid/permissions/domain/catalogue.py`.

> why not Pattern or PermissionNodes

### 34 Comment 3919914429

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3919914429) — **Open**, P4.
Original path: `squid/permissions/domain/resolution.py`.

> lol we are just gonna not have docstring only for the most confusing type ever

### 35 Comment 3919916097

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3919916097) — **Open**, P4.
Original path: `squid/permissions/domain/resolution.py`.

> kinda confusing

### 36 Comment 3919920324

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3919920324) — **Open**, P1.
Original path: `squid/permissions/domain/resolution.py`.

> discord is being privileged here, hopefully already fixed

### 37 Comment 3919924259

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3919924259) — **Open**, P4.
Original path: `squid/permissions/domain/resolution.py`.

> if we have some special strings expected then maybe this should be a literal?

### 38 Comment 3919928215

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3919928215) — **Open**, P4.
Original path: `squid/permissions/domain/resolution.py`.

> str is more convenient for users but the actual types is better for internal usage, not sure which one is beter here (or in other palces)

### 39 Comment 3919930158

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3919930158) — **Open**, P5.
Original path: `squid/permissions/domain/resolution.py`.

> this seems horrifyingly inefficient

### 40 Comment 3919938251

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3919938251) — **Open**, P4.
Original path: `squid/permissions/domain/resolution.py`.

> Seems like a literal should be returned

### 41 Comment 3933780012

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3933780012) — **Open**, P6.
Original path: `squid/permissions/application/ports.py`.

> AssignmentRecord is such a bad name

### 42 Comment 3933843633

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3933843633) — **Open**, P1.
Original path: `squid/permissions/application/services.py`.

> not quite sure whats the point of these two reverse mapping

### 43 Comment 3933849494

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3933849494) — **Open**, P6.
Original path: `squid/permissions/application/services.py`.

> inconsistent naming we need a full pass on repository/store etc

### 44 Comment 3933860822

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3933860822) — **Open**, P1.
Original path: `squid/permissions/application/services.py`.

> this feels very roundabout

### 45 Comment 3933873719

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3933873719) — **Open**, P1.
Original path: `squid/permissions/application/services.py`.

> why do we have the Subject and its records? why doesnt the Subject carry the records or the records carry the subject?

### 46 Comment 3934049383

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3934049383) — **Open**, P6.
Original path: `squid/permissions/infrastructure/models.py`.

> is this really a good idea

### 47 Comment 3934356660

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3934356660) — **Open**, P6.
Original path: `squid/permissions/infrastructure/models.py`.

> so many conditionally nullable stuff

### 48 Comment 3934460539

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3934460539) — **Open**, P6.
Original path: `squid/permissions/application/ports.py`.

> if this doesn't get renamed back to RoleRecord in later commits...

### 49 Comment 3934465499

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3934465499) — **Open**, P1.
Original path: `squid/permissions/application/services.py`.

> not sure whats the point of these two reverse mapping functions

### 50 Comment 3939283184

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3939283184) — **Open**, P6.
Original path: `squid/permissions/infrastructure/models.py`.

> text is probably correct but we need to use a enum on the app level

### 51 Comment 3939326951

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3939326951) — **Open**, P6.
Original path: `squid/permissions/infrastructure/models.py`.

> enum?

### 52 Comment 3939334832

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3939334832) — **Open**, P6.
Original path: `squid/permissions/infrastructure/repository.py`.

> pretty sure this is defined more than once

### 53 Comment 3939363209

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3939363209) — **Open**, P6.
Original path: `squid/permissions/infrastructure/repository.py`.

> what is this garbage. Do it in SQL.

### 54 Comment 3939395786

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3939395786) — **Open**, P8.
Original path: `squid/persistence/wake_listener.py`.

> wtf is a "latency hint"

### 55 Comment 3939413064

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3939413064) — **Open**, P8.
Original path: `squid/persistence/wake_listener.py`.

> hmm why do we need a cast here

### 56 Comment 3939413767

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3939413767) — **Partial**, P8.
Original path: `squid/persistence/wake_listener.py`.

> hmm the url being asyncpg is an unspoken assumption

### 57 Comment 3939501439

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3939501439) — **Open**, P8.
Original path: `squid/permissions/application/cache.py`.

> why version when it is epoch elsewhere

### 58 Comment 3939513182

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3939513182) — **Open**, P8.
Original path: `squid/runtime.py`.

> hmm maybe the names should be a strenum

### 59 Comment 3994767147

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r3994767147) — **Open**, P1.
Original path: `squid/bot/utils/permissions.py`.

> https://github.com/redstone-squid/Redstone-Squid/blob/4dde885e91911d7be9b4770a7a0058299f7f154d/squid/bot/utils/permissions.py#L102-L102
>
> this is ignoring member.guild?

### 60 Comment 4433039333

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r4433039333) — **Open**, P3.
Original path: `squid/builds/domain/models.py`.

> This is still merging all categories into one and kinda ugly

### 61 Comment 4433060379

[Original comment](https://github.com/redstone-squid/Redstone-Squid/pull/185#discussion_r4433060379) — **Open**, P9.
Original path: `squid/config.py`.

> why a class in a function...
