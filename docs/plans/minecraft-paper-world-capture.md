# Paper world capture

## Goal

Implement the Paper side of capture so `SELECT`, `POSITION_ONE`, `POSITION_TWO`, and `SELECTION` stop answering
"not available yet", feeding [minecraft-schematic-upload.md](minecraft-schematic-upload.md).

## Why Paper differs

A Paper plugin runs on the server and reads authoritative world data, so a capture can be
`CaptureCompleteness.SERVER_AUTHORITATIVE`. The cost is that it must read chunks and block entities safely:
world access belongs on the thread that owns the region, and a selection may span unloaded chunks.

## Work

1. Selection input: record `pos1` and `pos2` from the player's block position or targeted block, and support a
   selection wand only if review wants one. Reject selections in different worlds.
2. Loaded-chunk policy: decide whether capture loads missing chunks or refuses. Recommended: refuse unloaded chunks and
   tell the player how many are missing, to avoid a player forcing large chunk loads. Check this against
   `CaptureBudget` before touching the world.
3. Block states: read each block's data in Sponge order (`x + z * width + y * width * length`), converting Paper's block
   data string to `BlockStateSnapshot` (resource ID plus sorted properties) with the snapshot's validation.
4. Block entities: read block entity NBT for signs, containers, command blocks, and others. Upstream content is
   preserved as submitted; do not filter or rewrite it, matching the backend's content contract. Positions are
   relative to the selection minimum and must be unique.
5. Entities: capture entities whose position lies in the selection, with their NBT, excluding players.
6. Data version: take the server's world data version for `SchematicSnapshot.dataVersion`.
7. Work distribution: take the snapshot on the owning thread, in bounded batches if a large selection could stall the
   tick, then encode and upload off-thread. Define the batch size by measurement on a maximum-size selection.

## Decisions needed

- Whether to support Folia-style region threading or only standard Paper.
- Whether capture may include entities, given the volume of NBT they can carry.
- Permission node for the selection commands (the tree uses `redstonesquid.submit`).

## Tests

Use a mock-bukkit or Paper test harness if one fits the Paper 26.1.2 build; otherwise isolate the conversion code behind
an interface and test it with fabricated block data. Required cases: a single block, mixed properties, a sign with text,
a container with nested items, an entity at a boundary, and a selection over the budget. Then do one manual run on a real
Paper server and compare the uploaded file with a known-good schematic.

## Blocks

Needs [minecraft-build-verification.md](minecraft-build-verification.md).
