# What each file does

`pre-push` is a git hook that runs before pushing to a remote repository. It checks if the code is formatted correctly. If not, it will prevent the push from happening and reformats the code automatically. To use it, copy the `pre-push` file to the `.git/hooks` directory. Make sure the file is executable by running `chmod +x .git/hooks/pre-push`.

`deploy.sh` as the name suggests is a script that deploys the project. It is not meant for external use as it assumes some commands and files are present in the project. A docker image may or may not be provided in the future.

The scripts in `migrations/` are used to provide additional information accompanying the migrations. For example, to backfill data when a new column is added to a table. These scripts are meant to be run manually before or after the migration is applied (whether before or after depends on the migration itself, see the top docstring of the script). These scripts are only guaranteed to work at the commit they are created in. They may not work in future commits.

`setup-claude-web-env.sh` bootstraps `just`, `uv`, project dependencies, and `prek` hooks in a Linux agent sandbox. It assumes the checkout is at `/home/user/Redstone-Squid`; override that with `REPO_DIR` when needed.

`setup-codex-cloud-env.sh` prepares a new Codex Cloud environment. Configure it as the setup script with `bash scripts/setup-codex-cloud-env.sh`. Configure `codex-cloud-maintenance.sh` with `bash scripts/codex-cloud-maintenance.sh` as the maintenance script to refresh the locked dependencies when a cached environment resumes.

`just dependency-report` exports a CycloneDX 1.5 software bill of materials from `uv.lock`. `just gha-analysis` analyzes GitHub Actions workflows with `zizmor` and requires an authenticated `gh` CLI. `just visualize-dependencies` writes an SVG dependency graph to `docs/dependencies.svg`.

## Historical build-log imports

`populate_db_with_logs_historical_messages.py` imports consenting authors' messages through
retained inference and submission drafts. It uses the normal bot process configuration for
Discord, the database, inference, and artifact storage. Without `--channel-id`, it reads the
configured build-log channels; repeat the option to select multiple channels. The bot needs
access to those channels and their message history, with Message Content enabled in Discord.

Start with a bounded preview (replace the channel ID and dates):

```sh
uv run --locked python scripts/populate_db_with_logs_historical_messages.py \
  --channel-id 123456789012345678 \
  --after 2026-01-01T00:00:00Z --before 2026-02-01T00:00:00Z \
  --limit 50 --concurrency 2 --dry-run
```

A dry run calls the configured model and logs inferred candidates, so it incurs inference
costs, but saves no accounts, inference inputs, candidates, or drafts. `--no-images` disables
visual inference inputs. Authors without current consent are skipped in both modes by default.
For channels whose posting policy already permits collection, pass `--channel-consent` to
use that policy for this import only. This works in dry runs and real imports, without
recording account-wide consent or changing live ingestion. Real imports still create author
identities as needed; dry runs do not.

Remove `--dry-run` to retain candidates and request shared finalization. Run the normal worker
to finish queued submissions; ambiguous files or missing fields remain available for review.
The `processed` count means intake returned successfully, not that a build was finalized.
The script does not start the bot gateway or post progress messages to Discord.

History is streamed oldest first. Consecutive messages from one author are grouped up to eight
messages with gaps of at most five minutes; author changes and replies outside the group split
it. The three preceding messages and reply ancestors provide context. `--group-window` and
`--group-max-messages` adjust grouping. `--limit` counts all messages read, including bot posts.
Bounds are exclusive and accept timezone-aware ISO timestamps or message snowflakes.

Use fixed date bounds and the same grouping options and limit when retrying: ordered primary
message IDs identify retained runs. Completed inference results are reused, failed inference is retried, and edited drafts are preserved. Changing bounds or grouping can create different bundles. Runs are subject to the
normal retention period and capacity limits. When no retained run exists, already imported
bundles are skipped; partially imported bundles fail for manual inspection and narrower bounds.
On any bundle failure (including exhausted model credits), the importer stops scheduling
new work and lets in-flight groups finish. It exits nonzero and prints the earliest failed
bundle's channel and first message ID, plus resume flags:

```text
--before <original-upper-bound> --resume-channel <channel-id> --resume-from <message-id>
```

Add those flags to the original command after fixing the failure. Keep the original channel
order, `--after`, `--limit`, and grouping options. The importer freezes and logs `--before`
when none is supplied. Resume skips completed channels, re-reads the stopped channel's
history to reconstruct identical groups and context, and starts processing at the reported
message (inclusive). Earlier groups make no model calls. Later channels run normally.
Real imports reuse retained results for groups that completed out of order; dry runs have
no retained results, so replaying those later groups calls the model again. The script
reports the resume point in its output; it does not write a checkpoint file or recover a
cursor after a process crash or forced termination.

## Termux

From the repository root, run `sh scripts/termux.sh` to install the locked dependencies.
Pass a command to use the same build environment for other operations:

```sh
sh scripts/termux.sh uv sync --locked --extra schematics
sh scripts/termux.sh just typecheck
```

The wrapper uses the checkout location and Termux's `$PREFIX`, so the repository can live
anywhere. It keeps uv's cache in `.cache/uv`, uses copy installs, sets Android API 24, and
disables release LTO to reduce Rust build memory use. `UV_CACHE_DIR`, `UV_LINK_MODE`,
`ANDROID_API_LEVEL`, and `CARGO_PROFILE_RELEASE_LTO` can override those defaults.

`android-link-with-libpython3.sh` keeps the Python library linked into aarch64 Android
Rust extensions. The wrapper also supplies Nucleation's feature and CMake settings; see
[the upstream issue record](../docs/plans/completed/nucleation-android-build.md).
