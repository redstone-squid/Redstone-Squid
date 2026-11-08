# Redstone Squid

Discord bot for Minecraft redstone build submissions, records, voting, and moderation.
Uses Python 3.14+ and discord.py.

## Code style

- Use 120-character lines and full type hints.
- Use built-in generics (`list`, `tuple`, etc.) and `collections.abc`, not deprecated `typing` aliases.
- Do not use `from __future__ import annotations` or quote forward references in annotations.
  Python 3.14 defers annotation evaluation by default.
- Keep docstrings plain and concise, with Google-style sections where useful. Explain behavior and
  edge cases callers need to know. Don't write too defensively, assume the readers are nice and smart but
  not familiar with the codebase. Examples may help for extremely complicated situations. Use comments to explain non-obvious decisions.
- Pyrefly is the local type checker; BasedPyright also runs in CI. Keep existing `# pyright: ignore[...]`
  comments. Use `# type: ignore` or `# pyrefly: ignore` for new suppressions unless specific to BasedPyright.
- don't use local imports unless you have a very good reason

## Concurrency

- Use anyio task groups and cancellation scopes for task lifetime, cancellation, and timeouts.
  Do not use bare `asyncio.create_task`. Process background work belongs to `BackgroundTaskSupervisor`
  in `squid/runtime.py`.
- Keep anyio on the asyncio backend; our dependencies do not support Trio. Continue using
  `asyncio.Lock`, `Event`, `Queue`, and `to_thread`.
- Use pytest-asyncio with `asyncio_mode = "auto"`, without `anyio_backend` fixtures or `@pytest.mark.anyio`.
- Prefer task groups over `asyncio.gather`. Use `gather(..., return_exceptions=True)` only when every
  branch must settle regardless of failure, and explain why in a comment.

## Git

- Commit changes unless the user asks otherwise. Make small, independently valid commits as work progresses.
- Use concise, imperative, component-scoped subjects, e.g. `builds: parse tick units in time strings`.
  For non-trivial changes, add a wrapped body explaining the problem, solution, and relevant testing or tradeoffs.
- There is generally no need to mention how many tests you ran in the commit message.

## Documentation

We have littered too much into documentations. Try to write clear understandable English for engineers.
Read @docs/code-documentation-style.md

## Validation

- Run focused tests during development with `--no-cov`, then again after the final edit.
  Run `git diff --check` and other relevant checks, such as `alembic heads` for migration changes.
- Format and lint changed files or affected packages. Let installed commit hooks handle checks they cover;
  confirm the hooks are installed before relying on them.
- Run `just typecheck` once for the whole project (`uv run --locked pyrefly check --config pyproject.toml`).
  It also runs on pre-push. Resolve every error; use a justified inline suppression when needed, never a baseline file.
- Keep every workspace member's `src` directory in `[tool.pyrefly] search-path`. Without it, Pyrefly treats
  the editable package as third-party and silently skips checking it.
- Leave the full test suite to CI unless the change has broad or uncertain impact, CI is unavailable,
  or the user requests a local run.
- Rerun passed checks only when later edits could affect their results.

## Nucleation issues

- Report bugs and documentation mismatches to [Schem-at/Nucleation](https://github.com/Schem-at/Nucleation/issues)
  as well as working around them here. Check current upstream docs before filing.
- Include the exact version, a self-contained reproducer for a clean install, and the impact on this project.
  Say whether the finding came from integration tests or code inspection.
- Record the issue number in the workaround's comment or in `docs/plans/` so we can remove it when fixed.
