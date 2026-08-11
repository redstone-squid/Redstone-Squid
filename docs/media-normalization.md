# Media normalization deployment

Submission media processing is optional and disabled by default. The API streams an owned draft upload into private
object storage, and only the database worker claims the durable normalization job. Enabling the feature does not make
raw or normalized object keys public.

## Worker image contract

The normal application image has no media toolchain. Local Compose and the production release workflow build the
worker with `WITH_MEDIA=1`, which installs both `/usr/bin/ffmpeg` and `/usr/bin/ffprobe`. `WITH_MEDIA` only adds the
executables; it does **not** set `SQUID_MEDIA_ENABLED`.

The image pins all three inputs involved in installing FFmpeg:

- the Python/Debian base image by manifest digest;
- the Debian package universe to the `20260803T000000Z` snapshot; and
- `ffmpeg` to Debian version `7:7.1.5-0+deb13u1`.

The build verifies the installed Debian version and runs both executables. Updating FFmpeg security fixes is therefore
an explicit change to the base digest, snapshot date, and package version. The build still depends on the continued
availability of `snapshot.debian.org`; registry provenance and the published worker image digest are the durable
release artifacts.

The final container runs as unprivileged UID 10001. Compose drops all Linux capabilities and enables
`no-new-privileges` for the worker. FFmpeg input protocols are also restricted by the application to local files and
pipes.

## Enabling the feature

Production should configure the following exact names in the environment shared by the API and worker. This project
uses one underscore at the nested settings boundary, so `SQUID_MEDIA_ENABLED` is correct; `SQUID_MEDIA__ENABLED` is
rejected by strict configuration validation.

```dotenv
SQUID_MEDIA_ENABLED=true
SQUID_MEDIA_FFMPEG=/usr/bin/ffmpeg
SQUID_MEDIA_FFPROBE=/usr/bin/ffprobe
SQUID_MEDIA_WORKING_DIRECTORY=/var/lib/app/media-tmp
SQUID_WORKER_MEDIA_JOB_CONCURRENCY=1
SQUID_WORKER_MEDIA_CLEANUP_INTERVAL_SECONDS=60
```

The Compose worker supplies the three path settings, but keeping them explicit in a non-Compose deployment avoids a
PATH-dependent executable choice. Enabling must reach both processes: the API owns upload registration, while the
worker owns FFmpeg execution. No media-specific secret is required.

The worker retains the 60-second storage-cleanup loop even when `SQUID_MEDIA_ENABLED=false`, so disabling FFmpeg does
not strand raw objects or previously discarded normalized artifacts. Active normalization also performs opportunistic
pre/post cleanup; PostgreSQL row locks and idempotent object deletion make overlapping passes safe.

With the local artifact backend, `/var/lib/app/objects` must be the same durable volume in the API and worker. With the
S3 backend, both processes instead need the same bucket, prefix, endpoint, and credentials through the existing
`SQUID_STORAGE_*` settings.

## Writable storage

The image creates three non-root-owned locations:

| Path | Purpose | Persistence |
| --- | --- | --- |
| `/var/lib/app/tmp` | API upload staging through `TMPDIR` | Ephemeral |
| `/var/lib/app/media-tmp` | Worker input, normalized output, and poster staging | Ephemeral |
| `/var/lib/app/objects` | Raw and normalized artifacts for the local backend | Durable shared volume |

Keep the two temporary directories private to one container. Do not share them between replicas. A terminated job can
leave files only until its process-local temporary directory is cleaned up; durable retry state and raw input remain in
object storage.

The hard source and output budgets are each 500 MiB per draft. During a single video job, the worker can transiently
hold a 500 MiB source, a 500 MiB video, and a 500 MiB poster before the combined-output check rejects the excess.
Provision at least 1.5 GiB of scratch disk per concurrent job, plus filesystem overhead. The local artifact volume can
retain roughly 1 GiB per draft across raw and normalized objects, so size and monitor it independently.

## CPU and memory limits

The defaults below are subprocess backstops, not a substitute for a container or pod resource limit:

| Setting | Default | Scope |
| --- | ---: | --- |
| `SQUID_WORKER_MEDIA_JOB_CONCURRENCY` | 1 | Simultaneous normalization jobs in one worker |
| `SQUID_WORKER_MEDIA_CLEANUP_INTERVAL_SECONDS` | 60 s | Always-on raw and normalized object cleanup cadence |
| `SQUID_MEDIA_THREADS` | 2 | FFmpeg threads per child process |
| `SQUID_MEDIA_MEMORY_BYTES` | 2 GiB | Address-space limit per FFmpeg/ffprobe child |
| `SQUID_MEDIA_CPU_SECONDS` | 540 s | CPU-time limit per child |
| `SQUID_MEDIA_MAX_OPEN_FILES` | 128 | File descriptors per child |
| `SQUID_MEDIA_PROBE_TIMEOUT_SECONDS` | 15 s | Wall time for each probe |
| `SQUID_MEDIA_IMAGE_TIMEOUT_SECONDS` | 120 s | Wall time for image normalization |
| `SQUID_MEDIA_VIDEO_TIMEOUT_SECONDS` | 600 s | Wall time for video normalization |
| `SQUID_MEDIA_POSTER_TIMEOUT_SECONDS` | 120 s | Wall time for poster generation |
| `SQUID_MEDIA_JOB_MAX_ATTEMPTS` | 3 | Durable attempts before a job becomes dead |

At concurrency one, allow at least 3 GiB of container memory: the FFmpeg child can consume 2 GiB and the Python worker
may hold a verified output of up to 500 MiB while publishing it. A 4 GiB limit leaves more practical headroom. For each
additional concurrent job, budget another 3 GiB memory and 1.5 GiB scratch disk. Configure the host-level CPU, memory,
PID, and writable-layer limits in the deployment platform; the repository does not choose a one-size-fits-all machine
budget.

The submission contract separately rejects more than 10 images, 3 videos, five minutes per video, 33.2 megapixels per
frame, or 250 million decoded pixels per second. Source resolution and frame rate are otherwise preserved.

## Verification

The focused static contract test is:

```console
uv run pytest tests/deployment/test_media_worker_image.py --no-cov
```

To exercise the actual package snapshot and final user locally:

```console
docker build --build-arg WITH_MEDIA=1 --build-arg WITH_OBSERVABILITY=0 -t redstone-squid-media-worker .
docker run --rm --entrypoint sh redstone-squid-media-worker -ec \
  'test "$(id -u)" = 10001; test -w "$TMPDIR"; test -w /var/lib/app/media-tmp; ffmpeg -version; ffprobe -version'
```
