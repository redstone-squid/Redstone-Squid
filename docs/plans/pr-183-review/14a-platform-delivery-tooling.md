# PR #183 Review 14A: Platform, Delivery, and Tooling

## Scope

This plan dispositions six later-review threads that do not belong to one feature domain: runtime composition,
Discord error sanitization, the production image, deterministic OpenAPI export, the privileged screenshot workflow,
and one opaque voting test recorder. It does not redesign process ownership (completed in plan 12), media-worker
semantics (14F), or API enforcement (14B).

The current-state audit was performed against the production entry points in `squid/bootstrap.py`,
`squid/worker/app.py`, `Dockerfile`, and the `workflow_run` job—not merely their unit tests.

## Findings and decisions

### Runtime construction no longer exposes a general function-injection API

The reviewed shape accepted an arbitrary service factory at a public bootstrap boundary. Current code confines that
mechanism to private `_create_runtime`; the three public constructors are typed, concrete entry points:
`create_api_runtime`, `create_bot_runtime`, and `create_worker_runtime`. Keeping the private generic removes repeated
database/exit-stack ownership code without granting callers a runtime service-locator seam.

**Decision:** retain the private helper. Add an architecture assertion that process entry points call only the
concrete constructors, and document that test composition happens by constructing an `ApplicationRuntime` or service
bundle directly. Do not grow `_create_runtime` into a public plugin hook.

### Error-context recursion should be named at module scope

`_safe_log_context` still nests a recursive `sanitize` function. It is correct, but the nested definition hides a
security boundary that removes Discord identifiers from arbitrarily nested diagnostic context.

**Decision:** extract `_sanitize_log_value` at module scope, keep `_safe_log_context` responsible for enforcing a
dictionary result, and cover nested mappings, lists, tuples, non-string mapping keys, and every redacted key spelling.
This is a clarity refactor with no output change.

### The image has two independent feature axes encoded as shell branching

The runtime image repeats Debian snapshot setup and expands four `WITH_MEDIA`/`WITH_SOFTWARE_GPU` combinations in
one shell instruction. The exact FFmpeg pin and the unprivileged writable-directory contract are valuable; the
branching makes them difficult to inspect and has encouraged tests to read Dockerfile strings.

**Decision:** preserve digest and package pins, but split reusable package setup from final feature targets. Produce
named runtime stages for base, media, software-GPU, and media-plus-GPU, with the deployment workflow choosing a stage
instead of Boolean shell algebra. Verify installed packages and the runtime user by building/inspecting the target,
not by matching source text. Do not loosen `.dockerignore` or execute repository code during image-contract checks.

### OpenAPI export has an output constant but not a repository-root contract

`scripts/export_openapi.py` now centralizes `OUTPUT_PATH`, which partially addressed the comment. It still derives
the root inline and gives other contract scripts nothing to share.

**Decision:** add `PROJECT_ROOT` and derive `OUTPUT_PATH` from it. Keep the script's only mutation the canonical
`contracts/openapi.json` write. A test should invoke the exporter from a different working directory and compare the
result with `create_api_app().openapi()`.

### Screenshot delivery can be extracted only from a trusted revision

The long `actions/github-script` body deliberately avoids checking out or executing pull-request code while holding
`contents: write`. Blindly moving it to a script and checking out the PR would reintroduce the vulnerability the
inline form prevents.

**Decision:** extract pure artifact validation and Git-data planning into `.github/scripts/`, with unit tests, while
loading that code only from the trusted default-branch revision used to authorize the `workflow_run`. The privileged
workflow keeps the security-sensitive repository/ref checks visible and passes an inert plan to the GitHub API
write step. Fork PRs, changed head SHAs, symlinks, path traversal, extra files, non-PNG data, and oversize artifacts
must all fail before blob creation. No PR-controlled script, package install, or shell command runs with the token.

### Voting call records remain position-dependent

The exact reviewed nine-field tuple disappeared when Discord identity was removed from vote persistence, but
`FakeVoteRepository.cast_calls` is still a seven-field tuple whose assertions explain fields only by position.

**Decision:** replace it with a frozen `CastVoteCall` recorder (or assert directly against the typed command value if
plan 9 already exposes one). Creation call recorders should use the same rule once they exceed three heterogeneous
fields. This is test readability work; production voting contracts do not change.

## Milestones

1. **Make the existing composition boundary explicit.** Document the private runtime helper, add the process-entry
   architecture assertion, and extract the Discord log-context sanitizer.
2. **Give contract export a stable root.** Introduce `PROJECT_ROOT`, test CWD independence, and ensure generated
   OpenAPI remains byte-deterministic.
3. **Reshape image targets.** Factor Debian snapshot configuration once, create named feature targets, update Compose
   and deployment workflows, then replace source-string tests with image inspection.
4. **Extract screenshot validation safely.** Land and test the pure module first; only then shorten the privileged
   workflow while proving that the loaded revision is trusted.
5. **Name voting test records.** Introduce the recorder and update focused assertions without broad voting churn.

## Validation

- `tests/unit/bot/test_errors.py`: recursive redaction and unchanged presentation behavior.
- `tests/unit/test_bootstrap.py` and `tests/architecture/`: concrete process constructors and lifetime ownership.
- OpenAPI export test from a temporary working directory, followed by the repository's contract drift check.
- Docker build/inspect smoke tests for every deployed target; assert the non-root UID, writable-directory modes,
  exact FFmpeg package where enabled, and absence where disabled.
- Unit tests for the extracted screenshot artifact validator plus a workflow-policy test proving no PR checkout or
  PR-controlled execution occurs in the write-token job.
- Focused voting service tests using the named call record.
- `actionlint`, `git diff --check`, and the changed documentation/link audit.

## Thread dispositions

| Thread | Disposition |
|---|---|
| [`squid/bootstrap.py`: “no a fan of this function injection”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3796445586) | **Already addressed; retain the private helper.** Public process constructors are concrete. Milestone 1 records and enforces that boundary. |
| [`squid/bot/errors.py`: “why does this need to be nested”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3789562468) | **Fix in milestone 1.** Extract the recursive security helper and pin its behavior. |
| [`Dockerfile`: “don't like this. We can do better.”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3796045201) | **Fix in milestone 3.** Replace Boolean shell branching with named, inspectable targets while retaining pins. |
| [`scripts/export_openapi.py`: “really need a ROOT constant”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3789569542) | **Fix in milestone 2.** Add `PROJECT_ROOT` and a CWD-independent contract test. |
| [`.github/workflows/catalogue-screenshots-commit.yml`: “this is such a long script this should be extracted out maybe, if it can be done securely”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790822387) | **Fix in milestone 4, under the trust constraint above.** PR-controlled code must never execute with the write token. |
| [`tests/unit/voting/application/test_vote_service.py`: “nah, we are not storing a 9-tuple without explaining what each row is. Can we just not have this.”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791201333) | **Partly superseded, then fix in milestone 5.** The tuple is smaller but still opaque; use a named record. |

## Delivery

Commit milestones 1–2 independently from image/workflow work. Land the tested screenshot module before the workflow
starts loading it. Image target changes and their Compose/deployment callers form one atomic commit so no published
target points at a missing stage.
