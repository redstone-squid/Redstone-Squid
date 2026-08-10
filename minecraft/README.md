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
`FailClosedMinecraftSecretStore` is the default; a platform must provide an
audited OS credential-vault adapter before enabling persistence. The in-memory
adapter exists only in tests. Pending device codes and Fabric PKCE verifiers
remain in memory, and secret-bearing values redact their string forms.

The platform entrypoints still expose the current boundary: commands are
registered and report that the workflow is not connected. Wiring backend URL
configuration, an OS-vault adapter, player-facing device-code UI and polling,
form screens/dialogs, world capture, selection rendering, protection hooks,
schematic upload, and final submission are follow-up milestones. No placeholder
reports a submission as accepted.

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
