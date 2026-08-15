# 06 — Schematic Domain Contracts and Upload Safety

## Findings

- The Discord-specific uploader fields are still present end to end (`IngestRequest`, store port, row model). This is a real boundary leak, but its replacement must follow the shared account/user identity subplan rather than inventing a schematic-only identity.
- `RenderRequest.background` is an unlabelled four-float tuple. Introduce a validated RGBA value type (four finite channels in `[0, 1]`) and reuse it at config, wire, and adapter boundaries.
- Keep `RenderRequest.recipe_fields()` instead of defining `__hash__`: it deliberately states the cache recipe and avoids making a request with floats look like a generally safe hash key. Rename it to `cache_key_fields()` if that reads more clearly.
- `SimulationRequest` needs field-level documentation: manual input is optional, automatic selection is allowed only when unambiguous, watch positions are observations, and `max_ticks` is a safety budget.
- The `SchematicAnalyzer` name and raw resource-pack bytes are understandable but underspecified. Rename toward native operations/engine only if call sites become clearer; wrap pack bytes plus digest/media expectations in a small value object instead of passing anonymous bytes.
- `exclude_build_id` is correctly singular: duplicate lookup excludes only the submission currently being edited. Document that contract; do not broaden it to a list without a use case.
- The stdlib pre-parser is intentional security code, not needless duplication. A generic NBT library is acceptable only if it proves bounded streaming inflation and bounded prefix parsing before native code runs; otherwise retain the current implementation.

## Plan

1. Land the RGBA/resource-pack/request-contract cleanup without changing persistence.
2. Apply the identity decision from the user/account plan to schematic ingestion and migrate `uploaded_by_discord_id` only as part of that shared migration.
3. Audit `formats.py` against adversarial gzip/zlib/raw-NBT inputs. Keep content-first detection and filename hints only after valid bounded NBT; evaluate a library with an explicit memory-budget test before replacing anything.
4. Clarify port names and duplicate-exclusion documentation, avoiding churn where a rename does not improve the domain language.

## Interfaces and tests

- Likely additions: `RgbaColor`; a resource-pack payload/value carrying bytes and SHA-256; provider-neutral uploader identity chosen by the identity subplan.
- Unit-test RGBA validation and stable render cache recipes; test request/wire round trips using the value types.
- Retain decompression-bomb, truncation, zip rejection, ambiguous-root, extension-spoofing, and maximum-prefix tests. Add equivalent tests before accepting any third-party parser.
- Integration-test uploader attribution across Discord and the future non-Discord path after the shared migration.

## Disposition

- **Fix:** uploader coupling, RGBA typing, resource-pack contract, request documentation.
- **Clarify/no change:** explicit cache recipe and singular `exclude_build_id`.
- **Investigate before change:** replacing the bounded stdlib pre-parser or renaming the analyzer port.
