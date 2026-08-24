## Project: Redstone Squid Discord Bot

This is a Discord bot for managing Minecraft redstone build submissions, built with Python 3.14+ and discord.py. The bot manages a database of records, handles voting on submissions, and provides automated moderation features.

### Code Style
- **Formatting**: 120-character lines and Python 3.14 target
- **Documentation**: Google-style docstrings with type information
- **Type Safety**: Full type hints. **Pyrefly is the type checker for local development** — run it
  with `just typecheck` (`uv run --locked pyrefly check --config pyproject.toml`); it is also a
  pre-push hook in `prek.toml`. BasedPyright still runs in CI at `--level=warning` and is not
  installed locally, so `# pyright: ignore[...]` comments already in the tree stay meaningful; write
  new suppressions as `# type: ignore` or `# pyrefly: ignore` unless the finding is BasedPyright's
  alone. Use your best judgement for when to suppress and when to fix the typing issue.
- **Don't use Python 3.8 typings**: Never import `List`, `Tuple` or other deprecated classes from `typing`, use `list`, `tuple` etc. instead, or import from `collections.abc`
- Do not `from __future__ import annotations` and do not quote forward references in type hints — Python 3.14 defers annotation evaluation by default (PEP 649/749), so plain unquoted names work everywhere.
- Add code comments sparingly. Focus on why something is done, especially for complex logic. For unintuitive code, explain until it is clear.
- **Lifetime in docstrings**: a type that defines a terminating verb (`close`, `detach`,
  `finish`, `cancel`, `discard`, `run`) or hands out expiring authority states what ends it,
  in one clause, in its first paragraph. Everything else states nothing — a frozen value has
  no lifetime to describe. The trigger is a method the class defines, not how it is spelled.
  See `docs/squid-layouts-architecture.md` for the verb table and the naming rules
  `tests/architecture/test_naming.py` enforces.

### Concurrency
- **Task lifetime and cancellation go through anyio** — `anyio.create_task_group()`,
  `CancelScope`, `fail_after`/`move_on_after`. Every task must have an owner; do not reach for
  a bare `asyncio.create_task`. Process background work belongs to `BackgroundTaskSupervisor`
  in `squid/runtime.py`.
- **Everything else stays on asyncio.** `asyncio.Lock`, `Event`, `Queue` and `to_thread`
  interoperate fine under anyio's asyncio backend, and swapping them buys nothing.
- **Trio is not an option and anyio must stay on the asyncio backend.** discord.py, asyncpg,
  SQLAlchemy's greenlet bridge, redis.asyncio, aiohttp and uvloop are all asyncio-only.
- Tests stay on pytest-asyncio with `asyncio_mode = "auto"`. anyio code runs correctly under a
  pytest-asyncio loop, so do not add `anyio_backend` fixtures or `@pytest.mark.anyio`.
- Prefer a task group over `asyncio.gather`. Reach for `gather(..., return_exceptions=True)`
  only when every branch must settle regardless of failure, and say why in a comment.

### Git Workflow
- Always commit changes unless the user explicitly asks not to. Commit early and at each coherent milestone;
  keep every commit small, cohesive, independently valid, and easy to review.
- Write commit messages in Mitchell Hashimoto's style: use a concise, imperative, component-scoped subject
  (for example, `builds: parse tick units in time strings`), then add a wrapped body for non-trivial changes
  that explains the problem and its impact before describing the solution. Include relevant testing, tradeoffs,
  or follow-up context when useful.

### Validation Workflow
- During development, run the smallest test set that directly covers the changed behavior. Use
  `--no-cov` for these iterative runs when coverage reporting is enabled by default.
- After the final edit, run the focused tests once more together with cheap relevant checks such
  as `alembic heads` and `git diff --check`.
- Run formatting and linting only over changed files or affected packages. If verified commit hooks
  already enforce formatting or linting, let the hooks perform those checks; do not assume hooks
  exist without confirming it.
- Type-check with `just typecheck`. Pyrefly is configured project-wide in `[tool.pyrefly]`, so it
  has no changed-file mode: run it once and read only the errors in files you touched. The tree is
  not at zero errors, so compare against a pre-change run before claiming one is yours.
- Defer the full test suite to CI unless the change affects central behavior with a broad or
  uncertain blast radius, CI is unavailable, or the user explicitly requests a full local run.
- Do not rerun an unchanged check after it has passed unless a subsequent edit could affect its
  result.

### Upstream Reporting
- **Report any nucleation bug or docs mismatch upstream** at
  [Schem-at/Nucleation](https://github.com/Schem-at/Nucleation/issues), as well as working around
  it here. The maintainer is responsive and has fixed every report so far, so a workaround left
  unreported upstream is a workaround we keep forever.
- Check the current upstream docs before filing — several early findings were doc bugs that have
  since been corrected, and re-reporting a fixed one wastes everyone's time.
- Include the exact version, a self-contained reproducer that runs against a clean install, and
  what the wrong behaviour actually cost us. Findings from integration tests are worth more than
  findings from reading, so say which it was.
- Record the issue number in the workaround's code comment or in `docs/plans/`, so the workaround
  can be removed when the fix lands.
