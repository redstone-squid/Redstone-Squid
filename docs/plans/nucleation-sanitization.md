# Nucleation schematic sanitization gate

Redstone Squid must not retain or publish a player-provided schematic until a format-aware sanitizer has produced the
canonical artifact. The required upstream capability is tracked in
[Schem-at/Nucleation#10](https://github.com/Schem-at/Nucleation/issues/10).

## Verified upstream behavior

On 2026-08-11, a clean CPython 3.12 environment containing only `nucleation==0.10.4` was tested with a Sponge
schematic self-round-trip. Chest custom names, written-book author/title/page content, armor-stand custom names, and
entity UUIDs all survived. This agrees with Nucleation's documentation that block-entity and entity SNBT is preserved;
it is expected fidelity, not a parsing bug. The installed API and current upstream source/documentation expose no
sanitization or redaction operation.

On 2026-08-12, `nucleation==0.10.8` and `0.10.1` were both found to widen the tight bounds when a block is added but
never narrow them when one is cleared, so `tight_dimensions()` and `tight_bounds_*()` go stale after any removal while
`block_count()` stays correct; an emptied schematic still reports its historical box. Reported upstream as
[Schem-at/Nucleation#12](https://github.com/Schem-at/Nucleation/issues/12). Sanitization removes blocks, so measuring
the handle a sanitizer returns would record the pre-sanitization extent against the post-sanitization block count,
with no error raised. Until that issue is fixed, the adapter must re-read the sanitized artifact from bytes before
measuring it rather than measuring the mutated handle.

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
