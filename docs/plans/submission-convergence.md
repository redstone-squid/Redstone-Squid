# Converge submission orchestration

Status: implementation in progress. Approved 2026-09-07.

Execution clarification: the sanitizer is an unavailable attachment processor, not a reason to
defer orchestration or Discord integration. Supplied schematics remain private and pending;
continue the rest of this plan around that boundary.

## Accepted design

All submission entry points will use persisted drafts. Discord renders the shared manifest,
returns queued/processing feedback, and updates to the saved build. Delete `POST /v1/builds`;
coordinated changes to draft/finalization contracts are authorized.

Separate editable drafts and their issues, attachment processing, immutable submission attempts,
and committed receipts. Preparation distinguishes readiness, pending artifacts, and correction.
Use narrow application ports and pure normalization rather than a generic workflow framework.
Commit build changes, attachment associations, receipt, and database events atomically. Reuse
database-clock queue claims, anyio task ownership, domain-event delivery, and Discord reconciliation.

Attachments use durable normalization or quarantine/sanitization/analysis. Support several
schematics with primary selection; keep declared dimensions and duplicate evidence. Failed
attachments require explicit retry/discard. Preserve submitted/shared artifacts during expiry.
Public distribution needs an explicit owner attestation; inferred defaults are reviewer-only.

Persist inference runs and stable candidate identities. Complete inferred drafts submit
automatically; incomplete or ambiguous drafts wait for author or authorized staff correction.
Ambiguous multi-build bundles require attachment assignment. Source-message controls show minimal
status and reopen a private editor; owned drafts and a staff inbox provide recovery after restart.
Staff edits retain the original owner and record the actual actor.

Recalculation creates a linked revision proposal with a diff and expected build revision. Never
auto-apply it. Existing edit policy controls approval: owners of pending builds and authorized
staff. Preserve build identity, owner, category, and moderation status. Changed candidate counts
require target matching; concurrent build changes require renewed review.

Inferred drafts have a configurable global ceiling of 1,000 active drafts and seven-day expiry.
Keep the existing manual per-account limit. Capacity checks are atomic and full intake is visible;
never silently evict work or create duplicate attempts on delivery retries.

Replace job-shaped HTTP finalization with explicit create/list/read attempt operations beneath
`/v1/submissions/drafts/{id}/attempts`, separating draft issues from execution progress and receipts.
Generalize attachment upload/list/retry/discard/primary operations. Coordinate consumers and schemas.
Migrate retained work and preserve schema provenance; missing information requires correction.
Stop old workers during cutover and extend account merge/expiry handling for all new references.

## Milestones

- [ ] Characterize recovery, verify sanitizer gate, and remove deprecated endpoint.
- [ ] Introduce draft/attempt/receipt persistence, access policy, migrations, and API contracts.
- [ ] Complete durable attachments and transactional build commit.
- [ ] Migrate Discord editor and persistent status/reopen behavior.
- [ ] Migrate inference, correction inbox, capacity, and revision proposals.
- [ ] Remove superseded orchestration and update the structural-debt plan.

Commit each independently valid milestone in reviewable pieces. Do not confuse successful
preparatory extractions with completion of the workflow migration.

## Validation

Cover equivalent inputs across transports, retries and restart boundaries, claim fencing,
concurrent creation, stale approvals, permission revocation, consent and private artifacts,
ambiguous bundles, explicit attachment resolution, capacity races, expiry, historical migration,
and account merges during pending work. Retain existing build/Discord behavior until cutover.

Run focused tests with `--no-cov`, relevant integration/architecture tests, project-wide Pyrefly,
Alembic heads when persistence changes, and changed-file lint/format/whitespace checks. Use CI for
the full suite unless central changes leave unresolved risks.

## Execution record

### Sanitizer gate: blocked, 2026-09-07

`scripts/check_sanitizer_gate.py` reproduces Schem-at/Nucleation#39 against both pinned 0.10.14
and released 0.10.23 installed in isolation. Blocks-only output is stable and idempotent; entity
and block-entity cases each produce eight distinct outputs from eight identical inputs and fail
sanitize-twice byte equality. Both runs exit 1. The current upstream docs still promise determinism.
Reported the latest-release measurements in [issue #39](https://github.com/Schem-at/Nucleation/issues/39#issuecomment-5565152080).

Do not upgrade the production pin or enable schematic cutover on this evidence. A passing release
must also satisfy the cross-format and content-policy checks in `nucleation-sanitization.md`.
Independent milestones remain authorized while this gate is closed.

### Completed foundations, 2026-09-07

- `9e34ebd8`: removed `POST /v1/builds`, its input types and service method, and the obsolete
  mapping tests. Regenerated OpenAPI; reads and edits retain their existing contracts.
- `fe2cc83b`: added the standalone sanitizer release gate and reported the released-version
  failure upstream.
- `0cc87c0e`: separated preparation from finalization execution and moved normalization to a
  pure domain function. Consumers use the existing public application exports.
- `549daafc`: resubmission appends numbered jobs instead of overwriting prior payloads and
  failures. Draft locking and a partial unique index permit one active attempt. Latest-status
  reads select the newest attempt. Repeated identical preparation issues reuse their row.
  Migration `c6e0a3b8d1f4` preserves existing IDs and assigns attempt number 1; downgrade refuses
  to discard multiple attempts. Expiry clears all uncompleted attempts.

Stop old API/bot/worker writers before applying the attempt-history migration and restart them
with the matching code. Old writers assume one job per draft. Existing account-merge behavior
still canonicalizes owners and payload digests across retained jobs and fences active claims;
this is not yet a separately immutable attempt-input model.

Validation: 43 focused API/build-service tests passed; 75 preparation/finalization/target and
architecture tests passed. The final PostgreSQL attempt/expiry run passed all 16 tests. The
historical migration/rollback test and the focused account-merge claim-fencing tests passed.
Project-wide Pyrefly reports zero errors; changed-file Ruff and whitespace checks pass. Alembic
has one head, `c6e0a3b8d1f4`. `just` is unavailable, so Pyrefly ran through the exact configured
`uv run --locked ... pyrefly check --config pyproject.toml` command with the existing environment.

Additional checks exposed failures outside these changes:

- The localization architecture check reports five missing catalog entries in unchanged bot code.
- Full Alembic drift checking reports the existing idempotency `method`/`state` column types and
  `principal` comment; it reports no attempt-schema drift.
- The submission/account ownership integration test fails while inserting its existing schematic
  fixture against `build_schematics_sanitization_complete`, before performing the account merge.

### Remaining work

These commits are foundations, not completion of the approved workflow. The existing HTTP
`/submission` contract and job-shaped application interfaces remain. Still implement explicit
attempt create/list/read APIs, draft issue storage, separate immutable inputs and receipts,
transactional build/attachment/event commit, access/actor policy, durable attachment operations,
Discord manifest rendering and restart recovery, and inference intake/correction/revision proposals.
Do not mark the milestone checkboxes complete until their full acceptance criteria pass. The
sanitizer gate blocks sanitized artifact production only; Discord and other orchestration work
continue using private pending sources.

### Orchestration implementation continued, 2026-09-07

- `d9960211`: added explicit attempt creation, bounded history, and attempt-ID reads; updated
  OpenAPI and lifecycle consumers. Current draft status is now read at `/drafts/{id}/status`.
- `a65aa4c7`: preparation issues live on drafts; incomplete preparation no longer creates an
  execution attempt. Attachment waits resume from durable reservations, without extending expiry;
  edits cancel automatic submission. Supplied schematics remain pending behind issue #39.
- `5730dfc7`: atomic quota admission separates manual account capacity from the configurable
  global inferred pool (`SQUID_SUBMISSIONS_INFERRED_DRAFT_CAPACITY`, default 1,000). Stable source-ID
  replays do not consume capacity. Migration and concurrent-intake tests pass.
- `793554fb`: the worker commits builds, normalized-media references, receipts, and database events
  in one claim-fenced transaction. PostgreSQL tests cover rollback after receipt failure, stale
  workers, replay, and receipt media references.
- `d30308f1`: shared live staff authorization, a paged correction inbox, actual editor/requester
  attribution, execution-time permission rechecks, and account-merge fencing for staff requesters.
  Ownership stays with the original submitter. Role-only Discord context is not treated as durable
  authority: background execution requires a currently resolvable account grant.

The active migration head is `a0c4e7f2b5d8`. Focused tests and project-wide Pyrefly pass for these
milestones. The broader submission run exposed an outdated test assuming manifest revision 2 did
not exist; that test now checks the supported revision and an actually unsupported revision.
Broader configuration checks also exposed an existing process-projection failure in the schematic
background-color validator; the new submission configuration projection passes its focused test.

Next: durable shared attachment intake around the sanitizer boundary, persisted inference and
candidate conversion, Discord manifest editing/recovery, revision proposals, and removal of the
superseded transport orchestration. The earlier remaining-work paragraph is a historical checkpoint;
use this execution record when determining what is still outstanding.

### Private schematic intake, 2026-09-07

Durable private source records precede upload/download side effects. Stable upload IDs support
retry; draft references support sharing, explicit primary selection, and discard. Failed sources
block preparation until resolved. The API streams bounded bytes into quarantine and exposes only
metadata. Preparation reads retained sources and never issues a sanitizer certificate. Cleanup
locks references before sources and rechecks after waiting, retaining sources shared by active or
submitted drafts. Account merging includes source ownership and uploader attribution. Migration
`b1d5f8a3c6e9` refuses to orphan retained private objects on downgrade.

Validation: 64 focused API, PostgreSQL, and worker tests passed; the final cleanup concurrency
change passed all three schematic integration tests. Pyrefly reports zero errors. Ruff and
whitespace checks pass; Alembic has one head. Architecture naming checks additionally reported
four failures in unchanged squid-ui-discord code (`Group`, `Scope`, `outcome`, `Decorator`).

### Shared prefill and persisted Discord editor, 2026-09-07

`7e99012f` projects inferred or transport facts into manifest answers. Approved labels resolve to
stable keys; unknown taxonomy stays proposed. Missing categories and dimensions are not fabricated,
and a compatibility range is not promoted to an exact source version. Invalid values are identified
for correction. Private schematic defaults do not imply a distribution attestation.

The private Discord editor reads the pinned manifest, saves optimistic field changes, and reloads
current status. Modal callbacks retain their original revision. `/build drafts` lists owned drafts
or the authorized correction inbox, using durable reopen controls that reauthorize after restart.
The existing submission and inference intake paths still require cutover; adding recovery does not
complete those milestones. Automatic status delivery and attachment controls remain outstanding.

Validation: shared prefill, editor recovery, staff attribution, stale modal, component rendering,
route registration, and command taxonomy tests pass. Project-wide Pyrefly reports zero errors.
