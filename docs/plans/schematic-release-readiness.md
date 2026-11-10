# Schematic release readiness

## Content and publication contract

Uploaded schematic contents are preserved. There is no content-filtering feature, policy, certificate, or upstream
sanitizer dependency. Names, signs, books, inventories, commands, and entity data remain part of the submitted build.
Publication still requires explicit visibility, license, and rights attestation; withdrawn files are not publicly
downloadable. Native parsing remains isolated behind file-size, inflation, dimension, memory, and time limits.

Public REST downloads return the original bytes with the corresponding format extension. Rendering operates on the
original stored schematic. The Minecraft snapshot encoder preserves supplied NBT and validates structural consistency.

## Nucleation 0.12.0

The exact engine pin is `nucleation==0.12.0`, published on
[GitHub](https://github.com/Schem-at/Nucleation/releases/tag/v0.12.0) and
[PyPI](https://pypi.org/project/nucleation/0.12.0/) on 2026-10-23. The release satisfies the normal seven-day dependency
soak window. Native parsing, translation-invariant fingerprints, comparison, lattice detection, autostack,
format conversion, piston simulation, and worker recovery passed integration testing on this wheel.

## Remaining rollout work

- Verify the deployed synchronized-draft upload flow end to end. The upload endpoint and backend reader exist and are
  wired into finalization; Paper/Fabric finalization still depends on the world-extraction adapters below.
- Minecraft world capture and upload are planned in [minecraft-build-verification.md](minecraft-build-verification.md),
  [minecraft-schematic-upload.md](minecraft-schematic-upload.md),
  [minecraft-paper-world-capture.md](minecraft-paper-world-capture.md), and
  [minecraft-fabric-world-capture.md](minecraft-fabric-world-capture.md). The platform adapters report capture
  unavailable until those land.
- Verify actual resource-pack rendering and the deployed upload-to-publication flow.

## Deployment and stored data

Stop old application and worker containers, then apply Alembic migrations before starting the updated services.
Migration `c9d2e5f8a1b4` moves retained `waiting_sanitizer` uploads to pending analysis and reschedules drafts whose
only preparation issue was schematic processing. The worker's `submission-schematics` job validates original bytes
through the native job queue and retains the analysis. Invalid files become rejected attachments; infrastructure
failures remain pending with a stored error and are retried. Finalization attaches all retained ready schematics,
preserves the selected primary and explicit publication grant, and queues its preview in the build transaction.
Uploads are not content-filtered or rewritten. Existing source object keys remain valid, so recovery requires no reupload.

 The migrations remove retired certificate
columns, migrate stored draft answers to form revision 2, and migrate queued normalized payloads to payload schema 2.
Draft revisions advance to invalidate stale client edits; matching job revision fences and payload digests are updated.
Original artifact IDs and publication grants are preserved. Historical change logs and completed provenance remain audit
records. A downgrade cannot reconstruct deleted certificates or filtering choices; the payload migration refuses to
restore the previous contract for populated schematic artifact references.

## Fingerprints after the engine upgrade

Keep existing analyses labelled with their original analyzer version; never relabel old hashes as
`nucleation-0.12.0`. New analyses use the new version automatically. Byte-identity lookup works across versions,
and metric-shortlisted candidates are compared from bytes with the current engine. Direct fingerprint matches exclude
older analyses until they are recomputed.

Inventory existing analyses by `analyzer_version`. Re-analyze retained artifacts with 0.12.0, replacing their metrics,
fingerprints, and version together through `record_analysis` while preserving attachment identity, primary status,
filename, uploader, and publication facts. Verify the remaining old-version row count and duplicate detection after
the backfill. There is no automated backfill command in this change.

## Validation of content preservation

277 focused Python tests passed, including native worker operations, schematic storage/publication, synchronized
submissions, PostgreSQL migrations, and architecture boundaries. BasedPyright found no errors in the affected Python
code. Alembic has one head and its upgrade/downgrade schema-drift test passes.

10 Kotlin tests passed in an isolated build of the snapshot and Fabric capture sources, with the pinned Kotlin/JUnit
versions and production compiler flags. They cover preservation of nested inventories, text, commands, and identity
fields, structural limits, deterministic output, and the updated golden schematic. The normal Minecraft build stops
before compilation because its existing verification metadata lacks 11 dependency checksums. The isolated build kept
verification enabled, adding only missing checksums verified against Maven Central to its temporary metadata; repository
verification settings were not changed. Full Paper/Fabric assembly remains unverified.
