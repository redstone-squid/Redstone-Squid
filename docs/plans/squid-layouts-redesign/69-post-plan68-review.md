# Plan 69: post-Plan 68 review fixes

Status: open

## Issues

1. **Replicated-set remove undo:** preserve remover identity for selective undo, or reject `remove` inverses as unsupported until that identity exists. Add concurrent remove/remove coverage.
2. **Replica ID reuse:** prevent operation-ID collisions after restart by restoring the local clock or requiring incarnation-unique replica IDs.
3. **Outbound update overflow:** stop silently dropping pending updates; mark overflow and require a full resync, or provide a real outbox.
4. **History admission failure:** restore `READY`/`UNDONE` state after undo or redo raises `FreshActionError`.
5. **`strong_read()` documentation:** update the action-ledger example to match the opt-in strong-read policy.
6. **Session quota replacement:** calculate the prospective membership set before enforcing quota when replacing a session.
7. **ChallengeRunner bounds:** bound approval execution concurrency independently from queue capacity.

## Verification

- Add focused regression tests for each issue.
- Run focused tests, `just typecheck`, changed-file formatting/lint checks, `alembic heads`, and `git diff --check`.
