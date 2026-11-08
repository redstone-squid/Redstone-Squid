# Fabric client world capture

## Goal

Replace the fail-closed `Minecraft261Adapter` so the Fabric client can capture a selection, feeding
[minecraft-schematic-upload.md](minecraft-schematic-upload.md).

## Why the client differs

A client sees only what the server has sent it. Chunks outside the render distance are absent, block entity data
may be partial, and some NBT is never sent to the client. Capture therefore uses
`CaptureCompleteness.CLIENT_VISIBLE`, and the player must be told that the schematic can lack content.

## What exists

`ClientCaptureController` checks the budget, calls `FabricGameAdapter.isFullyLoaded`, then
`captureVisibleSelection`. `Minecraft261Adapter.isFullyLoaded` always returns `false` and capture throws, on purpose,
until extraction has been tested against a real 26.1.2 client.

## Work

1. `isFullyLoaded`: return true only when every chunk containing the selection is loaded on the client. Report the
   number of missing chunks to the player rather than capturing partially.
2. Block states: read client-visible block states into `BlockStateSnapshot`. Treat unknown properties as an error
   rather than dropping them.
3. Block entities: read what the client holds. Record in the metadata which block entity kinds were seen, so the
   backend and reviewers can tell a client capture from a server one. Do not invent data the client never received.
4. Entities: capture loaded entities in the selection, excluding the player and other players.
5. Run capture on the client thread in bounded batches so the frame rate does not stall, and cancel it if the player
   disconnects or changes dimension.
6. Do not rely on any Minecraft-version class outside `fabric-26_1`; `fabric-common` must stay version-free.
7. Add fidelity tests: capture a fixed test world with a real 26.1.2 client (Fabric game test or a scripted client
   run), compare the encoded file with a server-side capture of the same region, and record every block, block
   entity, or entity that differs.

## Decisions needed

- Whether Fabric capture is allowed to feed a published build at all. A client-visible capture can differ from the
  world, so reviewers may want to see `client_visible` on the draft and require a server capture for publication.
- Whether to check a loaded selection by distance or by exact chunk status.

## Blocks

Needs [minecraft-build-verification.md](minecraft-build-verification.md) and a real 26.1.2 client for fidelity tests.
