# Converge submission orchestration

Status: implementation in progress. Approved 2026-09-07.

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
sanitizer gate blocks the schematic and Discord cutover; it does not itself block independent
API, persistence, and policy work.
