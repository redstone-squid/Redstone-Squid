# Submission Behavior and Recovery

## Findings

- Invalid URLs are now rejected before changing the draft, fixing the earlier silent-mutation concern, but the error still does not list the offending values.
- Edit sessions still expire with “reopen the build” and no direct recovery control. Locally entered page changes can therefore require navigation back through the original command.
- Build-card colours still type the status as optional and silently map unknown/`None` states to green. A persisted build should not appear confirmed because its state is missing.
- Search missing-result handling is now explicit and user-friendly; treat that review thread as addressed. The multiline submission confirmation has also been rewritten since the reviewed commit.

## Intended changes

- Return structured URL validation failures grouped by field and render the actual invalid URLs within Discord limits. Preserve every valid and invalid value in the modal so correction does not require re-entry.
- Add an expiry recovery action that opens a fresh edit session for the same authorized build, reloading current state and clearly warning when unsaved edits were discarded. Keep authorization and optimistic-concurrency checks identical to normal editing.
- Make persisted build status an explicit rendering contract. Map every valid status exhaustively; render a neutral/error state and emit telemetry for legacy missing/unknown values rather than using confirmed green.
- Audit submission and edit error paths for hidden fallbacks. Expected validation/conflict outcomes should give actionable UI; unexpected failures should flow through centralized error handling without mutating the build.

## Tests

- URL tests cover multiple invalid values, mixed valid/invalid fields, Discord-safe rendering, and no draft mutation on failure.
- Expiry tests cover authorized restart, denied restart, state reloading, and stale concurrent edits.
- Rendering tests cover all statuses plus missing/unknown legacy data and assert it never receives confirmed styling.
