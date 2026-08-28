# Contributing

Thanks for your interest in the project. There are two distinct things you can contribute to
in this repository:

- **The Redstone Squid bot** — the Discord application for Minecraft redstone records.
  Contact `papetoast` on Discord before starting anything substantial; we are happy to onboard you.
- **The Squid UI framework** — the six publishable packages under `packages/`. Issues and pull
  requests are welcome directly; for larger changes, open an issue first so the design can be
  agreed before the work.

Please follow the [code of conduct](CODE_OF_CONDUCT.md) in all interactions.

## Development setup

The project targets Python 3.14+ and uses [uv](https://docs.astral.sh/uv/) with a locked
workspace, plus [just](https://github.com/casey/just) for common tasks:

```console
uv sync --locked          # or: just sync
uv run prek install       # installs the pre-commit and pre-push hooks
```

## Checks

- **Tests**: `uv run pytest` for the full suite. During development, run the smallest set that
  covers your change (`uv run pytest packages/squid-ui/tests --no-cov`, for example). Framework
  changes should pass `uv run pytest --no-cov packages tests/architecture`, which is exactly what
  the release workflow runs.
- **Types**: `just typecheck`. A clean tree reports zero errors, so any error it prints belongs
  to your change.
- **Formatting and linting**: ruff, enforced by the pre-commit hooks; `just lint` runs both.

## Framework ground rules

- Names in each package's `__all__` are the supported public surface and are snapshot-tested in
  `packages/*/tests/test_public_api.py`. Adding a public name means adding it there, documenting
  it, and covering it in the package's reference page under `docs/reference/`.
- Import boundaries between the packages are enforced by `tests/architecture/test_boundaries.py`;
  portable packages must not acquire transport or storage imports.
- All six distributions release in lockstep at one version. User-visible changes belong in
  `CHANGELOG.md` under the unreleased heading.

## Commits and pull requests

Write concise, imperative, component-scoped commit subjects (`builds: parse tick units in time
strings`), with a body explaining the problem and impact for non-trivial changes. Keep each
commit small and independently valid. Pull requests need the sign-off of a maintainer before
merging.
