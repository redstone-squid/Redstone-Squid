## Project: Redstone Squid Discord Bot

This is a Discord bot for managing Minecraft redstone build submissions, built with Python 3.12+ and discord.py. The bot manages a database of records, handles voting on submissions, and provides automated moderation features.

### Code Style
- **Formatting**: 120-character lines and Python 3.12 target
- **Documentation**: Google-style docstrings with type information
- **Type Safety**: Full type hints with BasedPyright for static analysis. Use your best judgement for when to `# type: ignore` and when to fix the typing issue.
- **Don't use Python 3.8 typings**: Never import `List`, `Tuple` or other deprecated classes from `typing`, use `list`, `tuple` etc. instead, or import from `collections.abc`
- Do not `from __future__ import annotations`, use forward references in type hints instead.
- Add code comments sparingly. Focus on why something is done, especially for complex logic. For unintuitive code, explain until it is clear.

### Git Workflow
- Commit completed changes unless the user explicitly asks not to.
- Keep commits small, cohesive, and easy to review.

### Validation Workflow
- During development, run the smallest test set that directly covers the changed behavior. Use
  `--no-cov` for these iterative runs when coverage reporting is enabled by default.
- After the final edit, run the focused tests once more together with cheap relevant checks such
  as `alembic heads` and `git diff --check`.
- Run formatting, linting, and BasedPyright only over changed files or affected packages. If
  verified commit hooks already enforce formatting or linting, let the hooks perform those checks;
  do not assume hooks exist without confirming it.
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
