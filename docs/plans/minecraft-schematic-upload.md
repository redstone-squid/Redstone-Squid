# Minecraft schematic uploads

Let linked players attach schematics to synchronized drafts from Minecraft. Use
the same draft API for [Paper world capture](minecraft-paper-world-capture.md)
and for files exported by Litematica on Fabric. Fabric players can already attach
these files to a draft on the web.

## Shared draft upload

Add schematic routes and `DraftSchematicResponse` parsing to `protocol` and
`SubmissionDraftClient`. The API lists, uploads, selects a primary file, and
discards files under `/submissions/drafts/{draft_id}/schematics`.

Send raw bytes as `application/octet-stream`. Enforce the 16 MiB limit before
uploading and reuse the same client-generated upload UUID when retrying the same
file. Keep file reads and HTTP requests off the game thread. Upload returns 202
while validation runs. Poll the draft's schematic list with a time limit and
report whether the upload is still validating, ready, or rejected. Draft
finalization readiness comes from the backend.

Test the Kotlin response against `DraftSchematicResponse` in `contracts/openapi.json`.
Use a fake transport to cover oversize files, upload retries, primary selection,
discard, and validation rejection.

## Fabric import

Players mark a region and save a `.litematic` in Litematica. Add a way to choose
that local file and upload it to the current Squid draft. The player explicitly
chooses which file to send. Litematica can only capture data available to the
client in multiplayer, so a saved file can be missing server-side data.

## Open decisions

- Which command starts capture on Paper and how players choose a file on Fabric.
- Whether Minecraft can attach several schematics to the same draft and when
  to mark a new attachment as primary.
