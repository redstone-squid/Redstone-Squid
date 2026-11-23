# What each file does


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

## Asset-only repair

`reindex_media_assets.py` reprocesses retained images and videos from checksum-verified
originals. It uses object storage when the raw file remains, otherwise fetches fresh
Discord attachment URLs from the retained source messages. It never calls inference,
submits drafts, changes build facts, or rewrites user-supplied external links.

Preview with `uv run --locked python scripts/reindex_media_assets.py --limit 100`.
Add `--execute --audit /durable/path/media-repair.jsonl` to apply the selected batch.
Use `--upload-id UUID` for a canary, and `--concurrency` to bound parallel downloads
and thumbnail processing (default two). Each replacement records its previous metadata
before publication; failures leave the current artifacts in place. Completed originals
are skipped by checksum, so rerunning resumes remaining work. Discarded uploads are
excluded to preserve review decisions. A nonzero exit means at least one repair failed;
the JSONL console output identifies the upload and error.

This repairs private retained artifacts. Public delivery of those artifacts is separate
from external build links and is not provided by this script.

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

## Reviewing deferred attachments

Staff with build edit permission can open `/build issues` or **Review attachments**
on a confirmed build card. Preview the original and check the source messages. Use
**Other candidate** to inspect another build that shares the attachment. Publishing
uploads the selected image or video to Catbox and saves its URL on the selected build.
Excluding requires a reason. Reopening the last decision restores its warning and
removes the link added by that decision.

For historical files, set `ATTACHMENT_REVIEW_ARCHIVE` to the directory containing
`retained-files.jsonl` and `retained-files/`. With Docker Compose, include
`-f deploy/compose.attachment-review.yml` alongside the deployment's usual Compose files.
The bot user needs read access to the manifest and files and traversal access to
their directories. Only those two archive entries are mounted, both read-only.

### Live Discord preview

Run `just ui-preview` and open <http://127.0.0.1:8000>. Use the navigation
to switch between the counter, catalogue, and review screens. In **Catalogue**,
switch from published standings to the build card. In **Review**, approve the
sample as Alice, undo the decision, or inspect the panel's state.

Each page load starts a fresh screen with disposable data. The preview supports
callback buttons and links. Idle previews expire after ten minutes. Ctrl-C stops
the server. Use `just ui-gallery` for static snapshots.

## Build media card refresh

Run `psql -X -v cutoff=<UTC-media-fix-deployment-time> -f scripts/refresh_build_media_cards.sql` against the
production database. The default preview uses a read-only transaction. It counts existing approved Discord
cards rendered before the cutoff whose builds retain media outputs. Use `-v only_ids=1574,10133,10134`
for a canary. Add `-v execute=on` to queue one batch through normal Discord reconciliation.

The batch defaults to 100 builds and can be adjusted with `-v batch_size=...` up to 500. Reuse the same
cutoff and pass `-v after_id=<next_after_id>` to continue after each batch. Finish with a preview from
the start to catch cards that were skipped because they already had queue entries. The script leaves
existing requests intact and excludes suppressed or deleted cards. Normal reconciliation still applies
the current channel configuration. Deferred attachments need their own recovery.
