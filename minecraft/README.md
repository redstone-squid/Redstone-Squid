# Redstone Squid Minecraft clients

This workspace contains the shared submission client and its Paper and Fabric
adapters. It deliberately does not use Stick or bundle Nucleation: commands are
native Brigadier trees, capture produces a small typed snapshot, and only the
backend's released Nucleation sanitizer may turn imported files into durable
submission artifacts.

## Modules

- `protocol`: bounded JSON parsing and versioned form/capability DTOs.
- `safe-snapshot`: vanilla-only capture values, selection/budget validation,
  safe-NBT checks, and a deterministic write-only Sponge Schematic v3 encoder.
- `core`: platform-neutral submission services and the shared Brigadier tree.
- `platform:paper`: a Paper 26.1.2 plugin entrypoint using lifecycle command
  registration.
- `platform:fabric-common`: client capture/routing ports that contain no game
  version classes.
- `platform:fabric-26_1`: the Fabric 26.1.2 entrypoint and first version adapter.

The platform entrypoints intentionally expose the current boundary: commands
are registered and report that the network/draft workflow is not connected yet.
World capture, selection rendering, dialogs/screens, authentication, backend
transport, protection hooks, and encrypted local draft storage are follow-up
milestones. No placeholder reports a submission as accepted.

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
