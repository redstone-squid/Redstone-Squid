# Redstone Squid

Discord bot for Minecraft redstone build submissions, records, voting, and moderation.
Uses Python 3.14+ and discord.py.

This codebase is yours: what it's for and how it's built are your call, and you'll keep building on it.

## Code and Documentation

- When designing Discord screens, use [the bot screen guide](docs/bot-screens.md) to predict the
  message's reading order and control rows. Compare the result with the gallery for that screen.
- Do not use `from __future__ import annotations` or quote forward references in annotations.
  Python 3.14 defers annotation evaluation by default.
- Keep docstrings plain and concise, with Google-style sections where useful. Explain behavior and
  edge cases callers need to know. Don't write too defensively, assume the readers are nice and smart but
  not familiar with the codebase. Examples may help for extremely complicated situations. Use comments to explain non-obvious decisions.
- Prefer to make documentations shorter or equal length as before unless you added a huge feature. Don't treat existing documentation like an append-only log, rewrite them as needed.
- Pyrefly is the local type checker; BasedPyright also runs in CI. Keep existing `# pyright: ignore[...]`
  comments. Use `# type: ignore` or `# pyrefly: ignore` for new suppressions unless specific to BasedPyright.
- don't use local imports unless you have a very good reason
- We don't care backwards compatibility.
- In general im very suspicious of tiny helpers you write, think about whether they should be class methods, or extracted to a class. Also when you remove/refactor think about whether the functions should still exist.
- We have littered too much into documentations. Try to write clear understandable English for engineers. Read docs/code-documentation-style.md when needed
- Do NOT try to slap on everything that is added into docstrings. Having too much information makes it worse.
- Do a task to its Coherent Extrapolated Volition

## Concurrency

- Use anyio task groups and cancellation scopes. Do not use bare `asyncio.create_task`.
  Process background work belongs to `BackgroundTaskSupervisor`
  in `squid/runtime.py`.
- Continue using
  `asyncio.Lock`, `Event`, `Queue`, and `to_thread`.
- We use pytest-asyncio with `asyncio_mode = "auto"`, without `anyio_backend` fixtures or `@pytest.mark.anyio`.
- Prefer task groups over `asyncio.gather`. Use `gather(..., return_exceptions=True)` only when every
  branch must settle regardless of failure, and explain why in a comment.

## Git

- Commit changes unless the user asks otherwise. Make small, independently valid commits as work progresses.
- Use concise, imperative, component-scoped subjects, e.g. `builds: parse tick units in time strings`.
  For non-trivial changes, add a wrapped body explaining the problem, solution, and tradeoffs. Mitchell Hashimoto's style is good.
- Usually no need to mention how many tests you ran.

## Validation

- Run focused tests after the final edit, don't test during development.
- Format and lint changed files or affected packages. Let installed commit hooks handle checks they cover.
- Run `uv run --locked pyrefly check --config pyproject.toml` once for the whole project.
  It also runs on pre-push. Resolve every error; use a justified inline suppression when needed, never a baseline file.
- Leave the full test suite to CI unless the change has broad or uncertain impact, CI is unavailable,
  or the user requests a local run.
- Rerun passed checks only when later edits could affect their results.

## Nucleation issues

- Report bugs and documentation mismatches to [Schem-at/Nucleation](  https://github.com/Schem-at/Nucleation/issues)
  as well as working around them here. Check current upstream docs before filing.
- Include the exact version and a self-contained reproducer for a clean install.
- Record the issue number in the workaround's comment or in `docs/plans/` so we can remove it when fixed.
