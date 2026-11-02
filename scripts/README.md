# What each file does

`pre-push` is a git hook that runs before pushing to a remote repository. It checks if the code is formatted correctly. If not, it will prevent the push from happening and reformats the code automatically. To use it, copy the `pre-push` file to the `.git/hooks` directory. Make sure the file is executable by running `chmod +x .git/hooks/pre-push`.

`deploy.sh` as the name suggests is a script that deploys the project. It is not meant for external use as it assumes some commands and files are present in the project. A docker image may or may not be provided in the future.

`add-tests-supabase-submodule.sh` is a script that adds [Supabase](https://github.com/supabase/supabase) as submodule to  `tests/`. We use sparse checkout to get the docker compose files, which is used to spin up a Supabase instance in containers to run integration tests against.

The scripts in `migrations/` are used to provide additional information accompanying the migrations. For example, to backfill data when a new column is added to a table. These scripts are meant to be run manually before or after the migration is applied (whether before or after depends on the migration itself, see the top docstring of the script). These scripts are only guaranteed to work at the commit they are created in. They may not work in future commits.

`setup-claude-web-env.sh` bootstraps `just`, `uv`, project dependencies, and `prek` hooks in a Linux agent sandbox. It assumes the checkout is at `/home/user/Redstone-Squid`; override that with `REPO_DIR` when needed.

`setup-codex-cloud-env.sh` prepares a new Codex Cloud environment. Configure it as the setup script with `bash scripts/setup-codex-cloud-env.sh`. Configure `codex-cloud-maintenance.sh` with `bash scripts/codex-cloud-maintenance.sh` as the maintenance script to refresh the locked dependencies when a cached environment resumes.

`just dependency-report` exports a CycloneDX 1.5 software bill of materials from `uv.lock`. `just gha-analysis` analyzes GitHub Actions workflows with `zizmor` and requires an authenticated `gh` CLI. `just visualize-dependencies` writes an SVG dependency graph to `docs/dependencies.svg`.

`android-link-with-libpython3.sh` is not meant to be run directly; it's a cargo linker override (wired in via `CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER` in `pyproject.toml`'s `[tool.uv.extra-build-variables]`) that gets PyO3 extension modules (`cryptography`, `jsonschema-rs`) to actually link against Termux's `libpython3.so` when built from source on Android. See the script's own header comment for why this is needed.
