# Advanced API Fuzzing Architecture for the Rust CLI Contract

## Summary

Retain Schemathesis for OpenAPI-aware HTTP and stateful testing, and Atheris for fast coverage-guided fuzzing
of pure Python code.

Independent fuzzing, maintainability, and CI/security reviews agreed on one major constraint: do not build a
universal fuzzing framework. Share environment lifecycle and finding metadata only. Schemathesis, Hypothesis
state machines, Atheris, and race/chaos scenarios keep their native generators, shrinkers, corpora, and replay
formats.

The fuzzer hardens the backend contract consumed by the existing Rust CLI, but it is not reused as CLI runtime
infrastructure. This preserves the approaches proven by RESTler, OSS-Fuzz, and SQLite without reproducing those
systems inside this repository.

### Implementation status (2026-08-12)

The canonical contract, stable operation IDs, authentication alternatives, complete `x-squid-cli` audit,
capability endpoint, language-neutral fixtures, pinned Schemathesis dependency, and bounded Atheris launcher are
implemented. The first environment layer is also present: loopback-configurable upstream adapters, unguessable run
identity, live reset attestation, deterministic in-container Mojang/Discord fakes, fail-closed Docker cleanup guards,
a 20-second one-worker `st run` watchdog, sanitized NDJSON classification, and versioned redacted finding envelopes.

The concrete local Docker composition and deterministic reset layer are implemented: isolated PostgreSQL roles,
migrated template-database cloning, deterministic seed IDs, Redis ACL separation, an API container with only
target-visible synthetic app credentials, an isolated fake-upstream container behind an API-local loopback proxy, exact
resource-limit attestation, one non-fuzzing lifecycle integration test, and a bounded `just fuzz-api-smoke` recipe.
The first draft workflow layer is also present as committed OpenAPI producer links, an applicability manifest, and a
deterministic Alice/web draft lifecycle reducer/state-machine scaffold.

The next integration slice is to connect the draft lifecycle scaffold to a live Schemathesis/Hypothesis state machine,
then add the next persona/workflow only after the single lifecycle check is green on the intended Docker runner. No API
campaign should be run merely to validate harness mechanics on a resource-constrained development box.

## Architecture and Contract Boundaries

### Minimal shared architecture

```text
just / CI
├── Schemathesis CLI driver ──────────┐
├── Hypothesis state machines ────────┤
├── Race/fault scenario runner ───────┤──> ApiEnvironment ──> disposable API stack
├── Differential cassette runner ─────┘
└── Atheris subprocesses ────────────────────────────────> pure squid code

Native engine artifacts
        ↓
artifact translators
        ↓
FindingCandidateV1
        ↓ exact fresh-stack qualification
QualifiedFindingV1
        ↓
isolated GitHub triage
```

Use the existing repository layout rather than introducing a generic engine framework:

```text
contracts/
├── openapi.json
├── fixtures/
└── protocol/              # Added as CLI auth/event protocols land

tests/fuzz/
├── existing Atheris entry points and native corpora
├── artifacts.py           # Thin cross-engine finding envelope
└── api/
    ├── environment and deterministic seeding
    ├── Schemathesis campaigns and state machines
    ├── invariants
    └── explicit race, fault, and differential scenarios

scripts/
└── triage_fuzz_findings.py
```

Only three substantial concepts are shared:

- `ApiEnvironment`/`RunningApi`: owns the disposable stack through one `AsyncExitStack`.
- `SeededIds`: frozen typed identifiers and synthetic personas returned by deterministic Python seeding code.
- `FindingCandidateV1`/`QualifiedFindingV1`: redacted metadata pointing to an engine-native reproducer.

Do not introduce a universal `EngineAdapter`, `Step`, `ResourceRef`, corpus, global oracle registry, or campaign
god object. Hypothesis uses bundles, Atheris uses bytes, and only project-owned race/fault/differential cases use
a small versioned `ScenarioV1` JSON format.

### Canonical API and CLI capability contract

- Keep `contracts/openapi.json` as the one contract consumed by Astro, Schemathesis preflight, Rust CLI contract
  checks, and any generated private transport models.
- Continue fuzzing the live `/openapi.json`, but fail preflight when it differs from the committed contract.
- Replace FastAPI-derived operation IDs with explicit stable identifiers suitable for generated Rust methods and
  permanent CLI mappings.
- Add a tested OpenAPI postprocessor for:
  - Anonymous access: `{}`.
  - Service credentials: `ApiCredential`.
  - Browser sessions: `WebSession`.
  - Unsafe browser writes: combined `WebSession` and `CsrfToken`.
  - CLI devices: a separate `DeviceSession` scheme.
  - API-key scopes through `x-required-api-scopes`, since OpenAPI API-key schemes cannot express scopes.
- Add `x-squid-cli` to every operation with exactly one classification:
  - `command`
  - `browser-only`
  - `transport-only`
  - `internal`
  - `compatibility-alias`
- Keep the extension small:
  - `command`: unique command path, required API-feature identifiers, and
    `interaction: direct|browser-continuation`.
  - `compatibility-alias`: canonical operation ID only.
  - Other classifications: optional rationale, but no command path.
- Audit unique command mappings, non-chained aliases, valid feature references, stable operation IDs, and complete
  classification. The fuzzer still exercises all applicable operations; `x-squid-cli` is not a dispatch mechanism.
- Add typed `/v1/capabilities` namespaces for API version, API features, protocol intervals, upload limits, renderer
  controls, and sanitization. Do not conflate API SemVer, submission protocols, renderer controls, or schematic
  engine capabilities.
- Test protocol behavior below minimum, at minimum, at maximum, and above maximum. A contract fingerprint is
  diagnostic, never the compatibility gate.
- Generate language-neutral fixtures under `contracts/fixtures/`, keyed by operation ID. Each CLI `command`
  operation gets a representative success and Problem Detail; binary and streaming operations also get
  header/boundary fixtures.
- The existing Rust CLI keeps handwritten output models. Generated Rust models, when introduced, remain private
  transport types behind them; generation drift and command-registry coverage are now active work because `cli/`
  exists.

### Production/test seam

- Keep all fuzz code outside `squid`; add no test endpoints or fuzz branches.
- Run the ordinary production composition root in a persistent Uvicorn container.
- Replace currently hard-coded Mojang and Discord endpoints with validated infrastructure configuration having
  secure production defaults and explicit loopback-only test values.
- Exercise the real HTTP adapters against deterministic loopback fake services instead of injecting a broad test
  service locator or `ApiAdapterOverrides` bag.
- Existing configurable embedding and storage adapters use the same approach.

## Campaigns and Disposable Environment

### Environment ownership and reset

`ApiEnvironment` creates and exclusively owns the API, PostgreSQL/pgvector, Redis, fake upstreams, object storage,
Toxiproxy, and Docker network.

Safety requirements:

- Generate an unguessable run ID, resource labels, disposable database prefix, and sentinel nonce.
- Bind published ports only to `127.0.0.1`; use a Docker internal network for target dependencies.
- Pass an allowlisted environment containing synthetic credentials rather than inheriting developer secrets.
- Use separate migrator/chaos-admin, application, and SELECT-only observer database roles.
- Verify labels, network ID, database prefix, sentinel, and PostgreSQL `application_name` before reset,
  termination, migration, observation, or cleanup.
- Never accept arbitrary remote target URLs and never run Docker prune or broad session termination.
- Refuse production-looking URLs, unlabeled resources, incorrect sentinels, or externally resolving hosts before
  sending any request.
- Apply CPU, memory, PID, log, request, response, binary, corpus, and artifact limits.

Each worker receives its own database and Redis namespace. Before every Hypothesis state-machine example, quiesce
requests, reset and reseed PostgreSQL, clear Redis, reset fake-adapter state through a harness-only control channel,
and verify the baseline checksum. No parallel mutation scenarios share a database.

Use narrow invariant queries kept beside their check, such as build count/status, draft revision/owner, vote effects,
idempotency state, event count, and session revocation. Do not create a second canonical read model of the whole
database.

### Schemathesis

Use two supported integration paths:

- `st run` subprocess campaigns for independent examples, schema coverage, native crash artifacts, and replay.
- Python `schema.as_state_machine()` subclasses for dependency-aware workflows and per-example reset.

Pin and explicitly enable the Schemathesis 4.24.2 checks currently selected:

- `not_a_server_error`
- `status_code_conformance`
- `content_type_conformance`
- `response_headers_conformance`
- `response_schema_conformance`
- `negative_data_rejection`
- `positive_data_acceptance`
- `missing_required_header`
- `unsupported_method`
- `use_after_free`
- `ensure_resource_availability`
- `ignored_auth`
- Configured maximum response time

Maintain an audited applicability manifest. Every check is enabled suite-wide, but semantic exemptions name the
exact operation and reason—for example, intentional soft deletion, asynchronous creation, or genuinely public
authentication.

Persona shards cover anonymous, consent-pending, two unrelated accounts, administrator, scoped service credentials,
and eventually CLI device sessions.

State machines cover:

- Draft create/change/replay/conflict/delete/submit.
- Build idempotency, ETag editing, visibility, ownership, withdrawal, and staff actions.
- Consent, CSRF, logout, device/session revocation, and cross-user isolation.
- Notification preferences, subscriptions, durable inbox, read state, and cursor binding.
- Voting eligibility, retries, changes, aggregation, privacy, and closure.
- Pagination tampering, search visibility, schematics, uploads, and async processing.

Schemathesis/Hypothesis keeps its native crash and shrinking format. Once fixed, a finding becomes an ordinary
regression test or explicit `ScenarioV1`, rather than checking in a version-dependent Hypothesis database.

### Atheris and explicit scenarios

- Keep independent Atheris corpora for version parsing, search parsing, signed cursors, ETags, idempotency
  fingerprints, request/domain conversion, draft reduction, and response normalization.
- Atheris owns mutation, coverage feedback, crash minimization, and `-merge=1` corpus pruning.
- Targets remain deterministic, database-free, network-free, and explicitly bounded.
- Exact race replay uses PostgreSQL advisory-lock/trigger barriers and barrier-capable fake adapters. Merely recording
  task launch order is insufficient.
- Database chaos covers unreachable DB, latency, mid-query connection termination, stale connections after restart,
  readiness, rollback, and recovery.
- Model an ambiguous HTTP outcome with an outer test-only response-discarding proxy after the API commits; do not
  pretend a random database disconnect proves ambiguous commit handling.
- Redis chaos covers timeout, disconnect, restart, local rate-limit fallback, recovery, and cross-principal isolation.
- Differential testing begins with curated HTTP cassettes against independently migrated/seeded base and candidate
  environments—not a general `DualTargetSession`.
- If needed after bootstrap, use a small versioned JSON Lines target-agent protocol with `hello`, `reset`,
  `execute_scenario`, `snapshot`, and `shutdown`. Each checkout uses its own lockfile, migration code, seeder, and
  runtime.
- Normalize volatile identifiers and localized text, but preserve Problem Detail `code`, status, resource, safe
  context, and durable side effects. Removing or reassigning a stable error code is blocking.

### CLI-oriented security campaigns

Add these only as their backend APIs land:

- Device authorization:
  - Publish a versioned normative signed-byte document and deterministic Ed25519 test vectors; OpenAPI is not the
    signing specification.
  - Use an injected clock, not sleeps.
  - Exercise mutated keys/signatures, verifier substitution, nonce replay, origin/audience/generation binding,
    concurrent approval/polling, expiry boundaries, revocation, rate-limit recovery, browser account switching, and
    continuation allowlists.
  - Assert fragments, codes, verifiers, private keys, signatures, sessions, and comparison codes never enter logs or
    artifacts.
- Resumable uploads:
  - Treat API control and presigned transfer data planes separately.
  - Schemathesis never follows arbitrary presigned URLs; disposable URLs target only the controlled loopback store.
  - Verify no API credential reaches the transfer host and no transfer capability becomes API authorization.
  - Cover changed files, missing/overlapping/reordered parts, duplicate completion, checksum mismatch, expired
    capability, cross-account reuse, quota limits, interrupted resume, and orphan cleanup.
  - Use the deterministic fake store for state exploration and a pinned S3-compatible service for smaller weekly
- Inbox/WebSockets:
  - Keep HTTP inbox as the durable source of truth.
  - Publish a sibling versioned JSON Schema or AsyncAPI contract for event envelopes, cursors, close codes, and
    ordering; do not force WebSockets into OpenAPI.
  - Exercise Authorization-header handshake, expiry/revocation, cursor recovery, duplicates, gaps, slow consumers,
    backpressure, invalid/oversized frames, reconnect, and server restart.
- Keep future Rust `cargo-fuzz` targets separate for signing, cache/resume state, event parsing, and CLI output. Do
  not run the full Python API fuzz stack on all four Rust release targets.

## Corpus, CI, and Privileged Triage

### Native artifacts and qualification

Maintain separate stores:

- Atheris: raw per-target corpora and minimized crashes.
- Schemathesis: pinned-version native crash archive and ephemeral Hypothesis database.
- Project scenarios: checked-in `ScenarioV1` regressions and differential HTTP cassettes.

Artifact translators emit a capped, schema-validated `FindingCandidateV1` containing engine, revision, profile,
checker/invariant, normalized root cause, affected operations, seed-builder hash, and native artifact reference.

Every campaign ends in exactly one state:

- `pass`
- `product_finding`
- `harness_error`
- `infrastructure_error`
- `budget_exhausted`
- `incompatible_replay`

Only `product_finding` enters qualification. Reproduce the exact minimized case twice on freshly reset environments
before creating `QualifiedFindingV1`; performance findings require 3/3 isolated healthy reruns.

Fingerprint by checker/invariant plus normalized traceback or state delta. Operations are occurrences, not part of
the primary fingerprint.

Regression expectations are explicit:

- `must_find`: unresolved issue/advisory reproducer.
- `must_pass`: fixed checked-in regression.

Only an exact `must_find` replay returning `pass` increments its clean streak. Infrastructure failures, timeouts,
incompatibility, or budget exhaustion never count as clean.

Redact at capture time. Sensitive comparison values use keyed hashes; artifacts never store bearer material or
presigned URLs. Plant synthetic canary secrets and fail the harness if any appear in logs, findings, exceptions,
snapshots, or artifacts.

### Budgets

- Local smoke: one worker and a 20-second exploration ceiling.
- PR: five-minute target, seven-minute workflow timeout including cleanup; curated replay only.
- Nightly: 30-minute campaign budget, 40-minute workflow timeout.
- Weekly: two-hour campaign budget, 140-minute workflow timeout.
- Use separate one-worker matrix jobs rather than putting two complete stacks on one small runner.
- Record total runner-minutes separately from wall-clock duration.
- Replace the existing implicit 600-second Atheris commands with bounded profiles; longer local execution requires
  `--allow-long-run`.

Commands:

- `just fuzz-api-smoke`
- `just fuzz-api-replay`
- `just fuzz-api campaign=<name>`
- `just fuzz-target target=<name> seconds=<bounded-value>`

Enable PR differential blocking only after its p95 cold-run time is below five minutes. Until then, candidate replay
and schema/security audits run on PRs, while full differential remains nightly.

### Workflow trust boundaries

Use separate workflows in accordance with GitHub's guidance for privileged `workflow_run` processing:

1. `api-fuzz-pr.yml`
   - Pull requests only.
   - `contents: read`, no secrets, writes, private corpus, or advisory access.
   - After bootstrap, the protected merge-base supplies mandatory coordinator, corpus, normalizers, and gates;
     candidate checkers are supplementary.
2. `api-fuzz-scheduled.yml`
   - Protected default-branch schedule; tightly restricted manual dispatch.
   - No issue/advisory write token.
   - Produces capped redacted finding manifests.
   - Security bundles are encrypted with a committed age/X25519 public key before upload.
3. `api-fuzz-triage.yml`
   - Fresh runner triggered only by the named scheduled workflow.
   - Validate source repository, event, protected branch, head SHA, run ID, conclusion, and artifact identity.
   - Disable caches and never execute target code or replay content.
   - Decrypt and schema-validate capped bundles; sanitize Markdown/control characters.
   - Use `GITHUB_TOKEN` with only `issues:write`.
   - Mint the advisory App token only for the private advisory API step.
4. `api-fuzz-security-replay.yml`
   - Protected scheduled workflow for unresolved private cases.
   - Advisory-read and decryption secrets remain in host preparation steps.
   - Run the exact replay inside an internal-network container with no GitHub credentials, internet access, or host
     access beyond the read-only case.
   - Emit only redacted fingerprint/pass/fail results for triage.

Serialize triage with one concurrency group. Recheck fingerprints immediately before mutation, respect
`Retry-After`, apply bounded backoff, and cap GitHub mutations at 20 per run; queued findings carry forward.

Default unknown classifications to private. Public issues use an explicit safe-category allowlist. Automatically
close public issues and draft/triage advisories after the exact reproducer passes in two distinct scheduled runs;
recurrence reopens the same item. Published advisories are never automatically altered—recurrence creates a linked
private regression advisory.

## Delivery and Acceptance

Implement in independently valid milestones:

1. Wait for the current platform/authentication worktree to become clean.
2. Move and harden the canonical OpenAPI contract, stable operation IDs, authentication alternatives,
   `x-squid-cli`, capability registry/endpoint, and fixtures.
3. Add configurable loopback upstream endpoints, disposable environment, safety attestation, reset checks, and
   bounded `st run`.
4. Add one draft/build state machine, then extract only demonstrated shared environment code.
5. Add remaining persona state machines, narrow invariants, and Atheris targets.
6. Add deterministic races and adapter faults, followed by PostgreSQL and Redis chaos.
7. Add curated merge-base differential replay and benchmark it before gating PRs.
8. Add unprivileged scheduled campaigns, run report-only for seven consecutive clean nightlies and one weekly run,
   then enable automated triage.
9. Add device-auth, upload, and WebSocket campaigns as their CLI backend prerequisites land.

Acceptance requires:

- Architecture tests ensure `squid` never imports fuzz tooling, Schemathesis/Atheris remain test-only, and privileged
  triage cannot import or execute campaigns.
- Contract tests enforce canonical/live schema equality, complete CLI classification, unique mappings, declared
  features, response fixtures, and current/previous protocol compatibility.
- Unit tests cover seeding, reset checksums, artifact schemas, redaction, fingerprints, normalization, applicability
  exemptions, triage classification, and issue lifecycle.
- Integration tests cover real migrations, loopback fakes, process/container cleanup, egress refusal, database roles,
  Toxiproxy, Redis recovery, and no credential forwarding.
- A canary target proves detection, native minimization/replay, qualification, and deduplication for schema mismatch,
  500, authorization bypass, privacy leak, duplicate idempotent effect, partial write, and secret leakage.
- Every applicable operation, persona, producer link, CLI capability classification, and critical state transition
  is exercised.
- Local smoke stops within 20 seconds of exploration and leaves no process, container, network, or volume behind.
- No production or staging configuration can satisfy the disposable-target attestation.
- Any Nucleation defect or documentation mismatch found during integration is reported upstream with its exact
  version and minimized reproducer.

The Rust CLI consumes the contracts and fixtures produced here but does not import, embed, or depend on the Python
fuzz harness. New CLI protocols remain sequenced after their provider-neutral backend prerequisites and pilot APIs.
