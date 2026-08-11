# Redstone Squid Minecraft clients

This workspace contains the shared submission client and its Paper and Fabric
adapters. It deliberately does not use Stick or bundle Nucleation: commands are
native Brigadier trees, capture produces a small typed snapshot, and only the
backend's released Nucleation sanitizer may turn imported files into durable
submission artifacts.

## Modules

- `protocol`: bounded JSON parsing and versioned form, draft, and device-auth
  DTOs.
- `safe-snapshot`: vanilla-only capture values, selection/budget validation,
  safe-NBT checks, and a deterministic write-only Sponge Schematic v3 encoder.
- `core`: platform-neutral submission services and the shared Brigadier tree.
- `platform:paper`: a Paper 26.1.2 plugin entrypoint using lifecycle command
  registration.
- `platform:fabric-common`: client capture/routing ports that contain no game
  version classes.
- `platform:fabric-26_1`: the Fabric 26.1.2 entrypoint and first version adapter.

The shared core now contains a real JDK HTTP transport, Paper installation and
player authorization, Fabric S256 PKCE authorization, and synchronized-draft
operations. The transport accepts a base URI ending at the backend's `/v1/`
prefix, refuses plaintext remote HTTP, never follows redirects, applies bounded
timeouts/body sizes, and requires `no-store` on successful auth responses.

Secret persistence is deliberately a port instead of a plaintext config file.
Both entrypoints use a bounded ephemeral store for player grants, pending device
codes, and Fabric PKCE verifiers; everything is lost on restart. Paper reads its
installation ID and secret only from an environment variable or JVM system
property and keeps them in memory. An audited OS-vault adapter remains required
before either platform may persist credentials.

The platform entrypoints now wire `/squid link`, bounded server-side draft discovery,
category draft creation/resume, status, cancellation, and manifest-aware `set`/`unset` edits. Device polling runs
off the game thread and every result is dispatched back to the Paper/Fabric game
thread. No command claims final submission, media, world capture, or schematic
support. The native Brigadier tree remains a better fit than Stick here because
the same command shape is registered against Paper and Fabric source types and
all workflow behavior already lives in the platform-neutral core.

## Runtime configuration

The clients refuse to construct a transport unless both public endpoints are
explicit, absolute HTTPS URLs. The API base must end in `/v1/`. A backend-provided
`verification_uri_complete` is shown first, then `verification_uri`, with the
configured approval URI serving only as the compatibility fallback.

| Purpose | JVM system property | Environment variable |
| --- | --- | --- |
| API base | `redstonesquid.apiBaseUri` | `SQUID_MINECRAFT_API_BASE_URI` |
| Public approval page | `redstonesquid.approvalUri` | `SQUID_MINECRAFT_APPROVAL_URI` |
| Paper installation ID | `redstonesquid.installationId` | `SQUID_MINECRAFT_INSTALLATION_ID` |
| Paper installation secret | *(not accepted)* | `SQUID_MINECRAFT_INSTALLATION_SECRET` |

System properties take precedence where offered. The Paper secret is
environment-only because JVM `-D` arguments are commonly exposed by process
listings and start scripts. Do not put it in `plugin.yml`, a checked-in server
configuration, command arguments, or process logs. Fabric does not use the two
Paper-only settings.

Available synchronized-draft commands are:

- `/squid link`
- `/squid submit`, `/squid submit <category>`, and `/squid submit <draft-id>`
- `/squid set <field> <value>` and `/squid unset <field>`
- `/squid status` and `/squid cancel`

List values use comma-separated stable values. Durations require an explicit
unit such as `20t`, `10rt`, or `1.5s`. This text editor is intentionally small;
the backend remains authoritative for validation.

`/squid submit` reads at most ten compact active-draft summaries from the backend.
It resumes a sole compatible editable draft, lists multiple drafts without guessing,
and accepts a full draft UUID for an unambiguous choice. Category submission resumes
the only active draft in that category or creates a new one when none exists. Creation
retries first reconcile the authoritative list, then reuse the exact request body and
idempotency key only while the original grant and 24-hour replay window remain valid.
This recovers draft sessions after restart once the player links again; credential
persistence still requires the planned OS-vault adapter.

## Build

Install JDK 25, then run:

```shell
./gradlew check
./gradlew assemble
```

Dependency versions are centralized in `gradle/libs.versions.toml`, Gradle
dependency locking is enabled for every module, and dependency verification is
strict. When intentionally changing dependencies, regenerate both lockfiles and
verification metadata and review their diff.

Paper's runnable shaded JAR is produced in `platform/paper/build/libs`. The
Fabric JAR is produced in `platform/fabric-26_1/build/libs` and requires Fabric
Loader, Fabric API, and Fabric Language Kotlin at runtime.

## Safety invariants

- A selection must be positive, no more than 512 blocks on an axis, and no more
  than 20 million cells.
- Built-in snapshots accept only `minecraft` resource IDs and reject players,
  absolute position/world fields, commands, UUIDs, and ownership fields.
- Inventory and free-text disclosure are explicit snapshot policy values and
  default to included; the UI must show the warning before capture.
- The writer emits only Sponge v3, uses sorted palettes/compounds, and fixes the
  gzip header so identical snapshots produce identical bytes.
- This encoder is defense in depth, not a substitute for backend sanitization.
- Paper draft calls send both the installation credential and the short-lived
  player bearer; Fabric sends only its PKCE-issued player bearer. Draft origin is
  derived from that authenticated session, and client APIs never accept account
  IDs.
