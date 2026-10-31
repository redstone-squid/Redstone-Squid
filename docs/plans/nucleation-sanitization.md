# Nucleation schematic sanitization gate

Redstone Squid must not retain or publish a player-provided schematic until a format-aware sanitizer has produced the
canonical artifact. The required upstream capability is tracked in
[Schem-at/Nucleation#10](https://github.com/Schem-at/Nucleation/issues/10).

## Verified upstream behavior

### Rechecked on 2026-10-31 with Nucleation 0.12.0

The pin is now `nucleation==0.12.0`, published on both
[GitHub](https://github.com/Schem-at/Nucleation/releases/tag/v0.12.0) and
[PyPI](https://pypi.org/project/nucleation/0.12.0/) on 2026-10-23. The old August package cutoff was removed;
this release satisfies the normal seven-day dependency soak window.

**Not ready for public schematic import under the existing release contract.** Re-running the self-contained
reproducer in issue #10 in an isolated CPython 3.12 environment containing only Nucleation 0.12.0 still yields
`sanitizer symbols []`. Chest names, nested book author/title/pages, armor-stand names, and entity UUIDs survive the
Sponge round-trip. The current upstream Python API reference and source tree expose no sanitizer. Issue #10 was
closed on 2026-08-14; its closure did not deliver the requested API. This is the already-reported feature gap,
not a new parsing defect.

The remaining application work is also concrete:

- `nucleation_adapter.py` has no sanitize operation or cross-format sanitization fixtures.
- `bootstrap.py` still wires `FailClosedDraftSchematicReader`, which cannot issue a sanitized artifact.
- `SchematicService.ingest()` stores original bytes before analysis. It does not implement the short-lived
  quarantine and sanitized canonical storage required below.
- Public REST downloads and rendered previews require completed sanitization facts. Passing those gate tests does
  not establish a working upload-to-publication flow.
- Discord's schematic download/convert commands use `primary_for_build()` and `convert()` without the REST
  publication checks. These paths must enforce the same publication contract before public rollout.

Compatibility validation passed: 168 focused tests covering `tests/integration/schematics`,
`tests/unit/schematics`, attachment classification, worker rendering orchestration, and draft artifact readiness.
The native tests cover parsing, translation-invariant fingerprints, comparison, lattice detection, autostack,
format conversion, piston simulation, timeouts, crashes, and worker recovery. PostgreSQL repository and durable
job tests passed. These ran on the local Python 3.14 environment; the isolated feature-gap probe used Python 3.12.
BasedPyright reported no errors in the changed test, with two existing protected-member warnings. Alembic has
one head. Actual GPU/resource-pack rendering and a deployed end-to-end publication flow were not verified.

### Earlier verification

On 2026-08-11, a clean CPython 3.12 environment containing only `nucleation==0.10.4` was tested with a Sponge
schematic self-round-trip. Chest custom names, written-book author/title/page content, armor-stand custom names, and
entity UUIDs all survived. This agrees with Nucleation's documentation that block-entity and entity SNBT is preserved;
it is expected fidelity, not a parsing bug. The installed API and current upstream source/documentation expose no
sanitization or redaction operation.

## Fingerprints after the upgrade

Keep existing analyses labelled with their original analyzer version; never relabel old hashes as
`nucleation-0.12.0`. New analyses use the new version automatically. Byte-identity lookup still works across
versions, and metric-shortlisted candidates are compared from bytes with the current engine. Direct fingerprint
matches intentionally exclude older analyses until they are recomputed.

Before enabling public import, inventory existing analyses by `analyzer_version`. Re-analyze eligible retained
artifacts with 0.12.0, replacing their metrics, fingerprints, and version together through `record_analysis` while
preserving attachment identity and publication facts. Quarantined or unsanitized legacy files must first follow
the release contract below; a backfill must not invent sanitization facts. Verify the remaining old-version row
count and duplicate detection after the backfill. There is no automated backfill command in this change.

## Release gate

- Keep arbitrary imported bytes in private, short-lived quarantine and delete them after processing succeeds or fails.
- Store only a sanitized canonical Sponge `.schem` v3 artifact; serialization or format conversion alone is not
  sanitization.
- Do not ship schematic import or the public Paper/Fabric submission beta until a released, pinned Nucleation version
  provides the required sanitizer. Do not add a local arbitrary-file or SNBT-string rewriter as a substitute.
- Backend sanitization remains mandatory even for schematics produced by trusted Redstone Squid clients.

## Required policy contract

The adapter needs a deterministic, idempotent `sanitize(policy) -> report` capability implemented in Nucleation's
format-aware core and exposed by the bindings. The policy must cover:

- allowed namespaces and block/entity types;
- player entities, UUID/profile/owner fields, and unsafe references;
- optional inventories and recursively nested item contents;
- optional user-authored text, including names, signs, and books;
- commands and other executable or dangerous data;
- recursive block entities, entity passengers, and equivalent format-specific structures.

Reports may contain counts and stable reason codes, but must never include removed values. Applying the same policy
twice must produce the same artifact, and equivalent fixtures must receive equivalent treatment across supported input
formats. Sanitization must remain an explicit operation separate from serialization.

## Updating this gate

Remove the release block only after an upstream release provides the capability and Redstone Squid has pinned it,
wrapped it behind the existing Nucleation adapter, and passed cross-format integration fixtures for nested inventory,
text, identity, player, and executable data. Keep this document and issue link until those tests demonstrate both
determinism and sanitize-twice idempotence. If the upstream API deliberately narrows the requested scope, update this
plan with the remaining gap before adding any local policy layer.
