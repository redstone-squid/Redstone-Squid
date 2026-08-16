# Nucleation schematic sanitization gate

Redstone Squid must not retain or publish a player-provided schematic until a format-aware sanitizer has produced the
canonical artifact. The upstream capability requested in
[Schem-at/Nucleation#10](https://github.com/Schem-at/Nucleation/issues/10) **shipped in `nucleation==0.10.14`**; the
gate now turns on the determinism defect recorded below rather than on a missing API.

## Verified upstream behavior

### Superseded findings

- On 2026-08-11 (`0.10.4`) a Sponge self-round-trip preserved chest custom names, written-book author/title/pages,
  armor-stand custom names, and entity UUIDs, and no sanitization operation existed. **Superseded:** `0.10.14` ships
  one, and it removes all of these (see below).
- On 2026-08-12 (`0.10.8`, `0.10.1`) tight bounds only ever grew, going stale after any block removal
  ([#12](https://github.com/Schem-at/Nucleation/issues/12)). **Fixed and re-verified on 0.10.14, 2026-08-17:**
  clearing one block of a 3x1x1 now reports `tight_dimensions() == (2, 1, 1)`. The "re-read the sanitized artifact
  from bytes before measuring it" workaround is no longer required for correctness. Note that `dimensions()` and
  `tight_dimensions()` now return a `Dimensions` object with `.x`/`.y`/`.z`, not a tuple.

### 0.10.14 sanitizer, measured 2026-08-17

The capability is `nucleation.processing`, re-exported at the package root: `TransformPlan.registry_safe()`,
`apply_transform(schematic, plan) -> TransformReport`, `inspect_transform(...)` for a dry run over the same core
path, `ContentPolicy`/`UuidPolicy` for custom policies, and `decode_bounded()`/`DecodeLimits` for bounded decoding of
untrusted bytes. `apply_transform` mutates the handle in place and returns only the report.

Measured against an armor stand carrying a custom name, a `Motion` vector, a UUID, and a written book nested in
`HandItems`:

- Recursive removal works. `CustomName`, and the book's `author`/`title` **nested inside the item's `tag`**, were all
  removed; `Motion` was removed as volatile; the UUID was rewritten, not preserved. Summary:
  `{'text.field_removed': 3, 'nbt.volatile_field': 1, 'uuid.rewritten': 1}`.
- The report satisfies the no-leak contract. Findings carry a reason code, severity, action, a structural path
  (`regions.Main.entities[0].nbt.HandItems[0].tag.author`) and the matched rule name — **never the removed value**.
- Determinism and idempotence hold for block-only schematics, and **fail for any schematic carrying entity or item
  NBT** (see the blocker below).

### Blocker: non-deterministic NBT compound key ordering

Nested NBT compound keys are emitted in a different order on every call, which looks like unordered-map iteration
order rather than anything policy-driven. Serializing one unchanged in-memory schematic with an entity to Sponge
`.schem` eight times produced **eight distinct byte strings**, reordering both the entity compound (`Motion` moves)
and the item compound (`id`, `tag`, `Count` swap):

```
{... HandItems:[{id:"minecraft:written_book",tag:{author:A,title:T,generation:0},Count:1B}] ...}
{... HandItems:[{tag:{generation:0,author:A,title:T},Count:1B,id:"minecraft:written_book"}] ...}
```

This is upstream of the sanitizer — it reproduces with no transform applied — but it defeats two clauses of the
policy contract below: `apply_transform` over identical input bytes yields differing artifacts, and sanitizing an
already-sanitized artifact does not reproduce it, so **sanitize-twice idempotence fails**. It equally defeats any
content-addressed dedup or storage key derived from serialized bytes. Not yet reported upstream as of 2026-08-17.

Separately, litematic embeds wall-clock `TimeCreated`/`TimeModified` in its metadata, so litematic bytes are never
reproducible by construction. Content-address the Sponge artifact or a fingerprint, never `to_litematic_b64()`.

## Release gate

- Keep arbitrary imported bytes in private, short-lived quarantine and delete them after processing succeeds or fails.
- Store only a sanitized canonical Sponge `.schem` v3 artifact; serialization or format conversion alone is not
  sanitization.
- Do not ship schematic import or the public Paper/Fabric submission beta until the pinned Nucleation version
  sanitizes **deterministically**. `0.10.14` provides the sanitizer but not the determinism; the key-ordering defect
  above is the sole remaining gap. Do not add a local arbitrary-file or SNBT-string rewriter as a substitute.
- Backend sanitization remains mandatory even for schematics produced by trusted Redstone Squid clients.

## Required policy contract

The adapter needs a deterministic, idempotent `sanitize(policy) -> report` capability implemented in Nucleation's
format-aware core and exposed by the bindings. `0.10.14` meets every clause below except determinism and
sanitize-twice idempotence. The policy must cover:

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

Remove the release block only after an upstream release makes serialization order stable and Redstone Squid has
pinned it, wrapped `apply_transform` behind the existing Nucleation adapter, and passed cross-format integration
fixtures for nested inventory, text, identity, player, and executable data. Keep this document and the issue links
until those tests demonstrate both determinism and sanitize-twice idempotence — the two clauses `0.10.14` still
fails. If the upstream API deliberately narrows the requested scope, update this plan with the remaining gap before
adding any local policy layer.
