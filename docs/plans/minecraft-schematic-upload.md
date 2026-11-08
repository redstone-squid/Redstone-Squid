# Minecraft schematic selection and upload

## Goal

Let a linked player select a cuboid, have the platform capture it, and upload it to their draft through the backend's
synchronized-draft schematic API. This plan covers the platform-neutral parts in `minecraft/core` and `protocol`.
World extraction itself is in [minecraft-paper-world-capture.md](minecraft-paper-world-capture.md) and
[minecraft-fabric-world-capture.md](minecraft-fabric-world-capture.md).

## What exists

- `SchematicSnapshot` and `SpongeV3Writer` turn captured data into deterministic Sponge Schematic v3 bytes.
- `CaptureBudget` limits a selection to 512 blocks per axis and 20 million blocks.
- The command tree already declares `select`, `pos1`, `pos2`, and `selection`. Paper answers all four with
  "not available yet".
- The backend routes live under `/submissions/drafts/{draft_id}/schematics`: `GET` lists sources,
  `PUT /{upload_id}?filename=` uploads raw bytes (202), `POST /{upload_id}/primary` selects the primary file, and
  `DELETE /{upload_id}` discards one. Uploads are capped at 16 MiB and the `upload_id` is a client-chosen UUID that
  makes retries safe.

## Work

1. Add the schematic paths to `SubmissionApiPaths` and a response DTO to `protocol` (`id`, `filename`, `sha256`,
   `byte_size`, `state`, `primary`, `discarded`), parsed with the same bounded JSON rules as the other DTOs.
2. Add `uploadSchematic`, `listSchematics`, `selectPrimary`, and `discardSchematic` to `SubmissionDraftClient`.
   `BackendTransport` currently sends JSON, so it needs a bounded binary body with
   `Content-Type: application/octet-stream`. Reuse one `upload_id` across retries of the same capture.
3. Hold per-player selection state (two corner positions and dimension) in the core workflow behind a small port, so
   Paper and Fabric supply only the world access. State is lost on restart, like credentials.
4. Implement `select`, `pos1`, `pos2`, and `selection` in `MinecraftSubmissionWorkflow`. `selection` reports
   dimensions, volume, and any budget violation before capture. A new `submit` step (name to be decided in review)
   captures, encodes, checks the 16 MiB limit, uploads, and selects the file as primary.
5. Capture and encoding run off the game thread only on detached snapshot data; the platform adapter takes the
   snapshot on the thread that owns the world, then hands the value to core.
6. Report upload states to the player: `uploaded`, still validating, rejected. Poll `GET` for the final state with a
   bound, and never claim a draft is ready from the upload response alone; the backend's finalization readiness is
   authoritative.

## Decisions needed

- The user-facing command that triggers capture and upload (`/squid upload`, or folding it into `/squid submit`).
- Whether a draft may hold several captures from one session or the command replaces the previous one.

## Tests

Core tests with a fake transport: retry reuses the upload ID, oversize snapshots fail before any request, discard and
primary selection call the right routes, and a rejected upload is reported rather than silently accepted. Add a
contract check that the Kotlin DTO matches the backend's `DraftSchematicResponse` in `contracts/openapi.json`.
