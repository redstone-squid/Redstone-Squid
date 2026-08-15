# User Identity and Persistence

## Findings

- The former `users` context has already become a provider-neutral `accounts` context. Discord, Java, and Bedrock identities share one model, and Discord-specific error naming is now limited mainly to Discord entry-point methods.
- Re-linking the same Java UUID refreshes its display name and can claim the newly verified IGN. Previously claimed creator aliases remain attached, so the basic Minecraft rename path works when the user links again. There is no automatic Mojang name-change refresh or explicit rename history policy.
- The old collection of normalization helpers has collapsed to one `normalize_ign`; PostgreSQL independently computes the same normalized value. This duplication is an invariant worth pinning, not a reason to create a generic utility module prematurely.
- Explicit domain/SQLAlchemy conversions remain extensive. SQLAlchemy cannot populate the immutable domain objects automatically without coupling the domain to persistence, but mapping code can be consolidated and batch-loading behavior audited.
- The old “upgrade this row into a full account” concern is superseded by external identities and stable creator profiles; no polymorphic row upgrade is required.

## Intended changes

- Specify the Java rename lifecycle: when names are refreshed, whether the previous name is retained as a claimed alias, and how collisions enter staff review. Prefer refresh on successful Java authentication/linking first; schedule background Mojang polling only if a product requirement justifies it.
- Add a repository operation that atomically refreshes the Java display name and reconciles old/new creator aliases according to that policy, rather than scattering rename behavior across callers.
- Keep normalization in the account domain, document its equivalence with the database expression, and test Unicode/case/whitespace assumptions against valid Minecraft-name rules.
- Audit repository mappings by aggregate. Extract shared row-to-domain loaders and query shapes where they remove duplication, while retaining explicit mappings and preventing per-row relationship queries.
- Close provider-neutral and row-upgrade comments as already addressed after verifying all public/service interfaces use account or external-identity vocabulary.

## Tests

- Integration tests cover unchanged names, rename retention, unclaimed new aliases, conflicting new aliases, and relinking the same UUID.
- Persistence tests pin Python/database normalization equivalence and query counts for account/profile lists.
- Type and architecture tests ensure domain models remain independent of SQLAlchemy.
