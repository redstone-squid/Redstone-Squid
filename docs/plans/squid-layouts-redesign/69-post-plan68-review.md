# Plan 69: post-Plan 68 review fixes

Status: open

## Issues

1. **Replicated-set remove undo:** if two replicas remove the same add concurrently, undoing one removal must not resurrect the value against the other removal. Preserve remover identity for selective undo, or reject `remove` inverses as unsupported until that identity exists. Add concurrent remove/remove coverage.
2. **Replica ID reuse:** after restart, importing old operations does not advance the local sequence, so a reused replica ID can allocate an existing operation identity and have the canonical engine silently discard it. Restore the local clock or require incarnation-unique replica IDs.
3. **Outbound update overflow:** the bounded pending-update deque silently discards old deltas, allowing an application-owned transport to miss causal data permanently. Mark overflow and require a full resync, or provide a real outbox instead of presenting the deque as transport storage.
4. **History admission failure:** undo/redo marks an entry `REVERSING`/`REAPPLYING` before opening its fresh action. If admission raises `FreshActionError`, no inverse started but the entry keeps the transitional state; restore `READY`/`UNDONE` before re-raising.
5. **`strong_read()` documentation:** the action-ledger example still implies that an ordinary replicated read blocks an unrelated local write. Show the current policy: only a read of a written target or an explicit `strong_read()` creates a commit precondition.
6. **Session quota replacement:** quota is checked before the outgoing session is removed, so replacing one of the caller’s own sessions can count both sessions and reject a valid replacement. Calculate the prospective membership set before enforcing quota.
7. **ChallengeRunner bounds:** the approval queue is bounded, but every dequeued approval is immediately started in the task group, so queue capacity does not limit active execution. Add a separate worker/concurrency bound.

## Verification

- Add focused regression tests for each issue.
- Run focused tests, `just typecheck`, changed-file formatting/lint checks, `alembic heads`, and `git diff --check`.
