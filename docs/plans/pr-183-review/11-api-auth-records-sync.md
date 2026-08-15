# PR #183 Review: API, Auth, Records, and Sync

## Findings

- Keep HMAC-SHA-256 for API-key verification. Tokens contain 32 random bytes (256 bits), are stored as a peppered keyed
  digest, use indexed public key IDs, and compare in constant time. A password KDF addresses low-entropy passwords and
  would add cost without meaningful protection here; document this rationale and suppress the CodeQL false positive at
  the narrow test/helper site. See NIST SP 800-57 Part 1 and SP 800-107 Rev. 1.
- The Discord REST concern is valid, but spawning a discord.py gateway client per lookup is not. The current adapter
  retries one 429 and does not coordinate Discord bucket/global rate limits. Prototype one lifespan-owned, login-only
  discord.py client (no gateway connection) and use its public fetch APIs if they provide supported rate-limit handling;
  otherwise choose a maintained REST client or a bot-owned member-resolution boundary. Discord's rate-limit headers are
  dynamic and must not be hard-coded: <https://docs.discord.com/developers/topics/rate-limits>.
- Provider-neutral account IDs and durable event delivery landed after the reviewed commit, resolving the old
  `owner_user_id`/Discord-only event concerns. The table remains named `discord_sync_queue`, correctly reflecting its
  Discord presentation consumer; rename application concepts to reconciliation jobs, not generic domain events.
- Route-level build authorization still leaks application policy. Generic `NotFoundError` remains in records/votes/users,
  while other resources already demonstrate useful typed subclasses. Schema `from_domain` methods are repeated, but a
  mandatory universal mixin would couple unrelated response shapes and signatures.
- `api/errors.py` still carries an unnecessary registrar protocol and lazy observability import. Its shared problem
  schema/response metadata and error mapping are useful and should remain. The legacy `/verify` alias comment was made
  after `5edfd3e` and is outside this review cutoff.
- Scope sorting is intentional canonicalization before persistence, not required for authorization. Scope strings are
  permission patterns rather than a closed enum, so validation/normalization should use the permission parser, not an
  enum. `numeric_quantum` is precise domain terminology but poor public API wording; prefer `numeric_step` if the API is
  not yet compatibility-bound. `ActiveRecord` means the winning result from the active computation run, but the name is
  ambiguous outside that implementation detail.

## Subplans

1. **Auth contracts and crypto documentation**
   - Introduce validated value types for API-key IDs, permission patterns, credential kind, and stored resource/action
     strings where the sets are actually closed; preserve arbitrary valid scope patterns and deterministic storage.
   - Add a security note and focused CodeQL suppression explaining random-secret entropy, peppered HMAC, rotation, and
     constant-time comparison. Add pepper rotation/versioning only if operational requirements demand it.
2. **Discord member resolution**
   - Benchmark/verify a single lifespan-owned discord.py login-only client and its shutdown behavior. Do not connect a
     second gateway and do not instantiate a client per vote.
   - Preserve 403/404 versus unavailable semantics, bounded caching, cancellation, and capability resolution. Test
     bucket/global 429s, malformed responses, repeated limits, network failures, and shutdown.
3. **API/application boundaries**
   - Move pending-build visibility and edit authorization into build application services returning typed outcomes;
     routes should validate transport input, invoke services, and map response DTOs.
   - Add resource-specific record, vote-session, and creator not-found errors with stable resource/context fields.
   - Simplify exception registration by accepting FastAPI directly and importing observability normally; retain RFC 9457
     responses, locale handling, safe public context, retry headers, and the reusable response metadata helper.
4. **DTO and naming consistency**
   - Use a lightweight generic mapping protocol/base only for response models whose sole contract is
     `from_domain(domain) -> Self`; leave mappings needing extra context explicit.
   - Rename `ActiveRecord` to describe the materialized/current result and expose `numeric_step` (with an alias only if
     compatibility is required). Keep build tag moderation-source data absent but replace vague “provenance” wording.
5. **Reconciliation queue**
   - Keep Discord specificity in the infrastructure adapter/table, while renaming application `SyncJob` concepts to
     presentation reconciliation work. Replace unchecked persistence casts with validated enum/value construction and a
     clear data-integrity failure; reuse the generic claimed-row queue where it already solved claim-token races.

## Tests

- API-key known-answer, malformed token, rotation/revocation/expiry, constant-time path, and scope-pattern validation.
- Discord resolver lifecycle plus dynamic bucket/global rate-limit, retry, cache, cancellation, and unavailable cases.
- Service-level build authorization and route tests proving status/problem mappings remain stable.
- Typed not-found errors, DTO mapping contracts, public naming/OpenAPI snapshots, and compatibility aliases if retained.
- Reconciliation invalid-row, concurrent claim, retry/dead-letter, coalescing, generation, and crash-reclaim tests.
