# `refactor` CLI issue log

This file is append-only. New observations are added at the end without revising earlier entries.

## 2026-07-27

- Python symbol operations consistently warn that dynamic references cannot be discovered reliably. This is useful disclosure, but it means `rg` is still required as a completeness check.
- `usages` found imports and direct construction of `MessageService`, but did not find dynamic instance calls such as `bot.db.message.track_message(...)`.
- A Python usages search during the build/UI audit returned a suspicious root-relative `/views.py` path rather than the project-relative path. The result had to be cross-checked with `rg`.
- One Python usages call produced complete JSON but exceeded the 10-second shell timeout.
- A dry-run rename returned process exit code 1 with JSON status/code `NEEDS_REVIEW`. The documented review-required exit code is 2, so callers cannot rely on the documented exit-code contract for dry runs.
- Renaming `VersionService.list` to `list_versions` incorrectly changed the unrelated built-in generic annotation `list[str]` to `list_versions[str]`.
- The same rename correctly updated the method and its `self.list(...)` call, but did not update external dynamically resolved call sites. An `rg` cross-check was required.
