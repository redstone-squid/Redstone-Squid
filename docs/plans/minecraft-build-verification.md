# Minecraft build verification

## Problem

The normal `./gradlew build` in `minecraft/` does not reach compilation. The project requires a JDK 25 toolchain, and
its dependency verification metadata (`gradle/verification-metadata.xml`) lacks checksums for 11 artifacts. The
release-readiness work ran the snapshot and Fabric capture tests only in an isolated build with a temporary copy of
the metadata, so full Paper and Fabric assembly has never been verified.

## Work

1. Install JDK 25 on the build machine and CI image. Gradle does not auto-provision toolchains here because no
   toolchain repository is configured; either install the JDK or add a foojay resolver and review its trust cost.
2. Run `./gradlew build --write-verification-metadata sha256` to list the missing artifacts. Check each new checksum
   against the Maven Central (or Fabric/Mojang/Paper repository) `.sha256` file before committing. Do not accept
   checksums computed from a single download.
3. Fix whatever fails once compilation starts: the Paper and Fabric modules have not been compiled together before.
4. Run the Kotlin tests for every module and the Paper and Fabric assembly tasks in CI, with verification strict.

## Done when

`./gradlew build --dependency-verification=strict` passes on a clean checkout in CI, and the README's module list
matches what builds.

## Blocks

The world-capture plans cannot be verified without this.
