# PR #183 Review 14H: Accounts and Record Persistence

## Scope

This plan covers nine threads in account domain/model/repository vocabulary and computed-record persistence/API
ownership. It does not reopen the consent, verification-code, or identity-refresh behavior completed in plans 1–2.
Minecraft device authorization uses account facts but is handled in 14I.

## Findings and decisions

### An account can have several identities from one provider

`Account.identity(provider)` returns the first match even though repository behavior and unlink semantics permit more
than one identity from a provider. That silently invents a primary identity.

Replace it with `identities_for(provider) -> tuple[AccountIdentity, ...]`. Callers that truly require one identity
must state a selector: exact subject, most recently verified, public avatar source, or Discord gateway identity.
Preserve deterministic ordering from persistence and add multi-identity tests at every former singular call site.

### Claim/value docstrings describe one state or persistence mechanics

`AliasClaim` says “pending staff review” even when `ClaimStatus` is approved/rejected, and `VerificationCode` says it
is “returned by persistence.” Rewrite both around stable facts: a claim records a request and its resolution state; a
verification code identifies a currently redeemable Java identity. Docstrings must not promise a caller or state the
type does not enforce.

### UUIDv7 is repository-wide work

`accounts.public_creator_id` remains separate from the integer primary key. Merging it into `id` would require an
unplanned repository-wide UUIDv7 primary-key migration, including dozens of foreign keys and public URL
compatibility. Do not perform an account-only primary-key rewrite here. Retain the stable public/internal identity
split and record that no named implementation plan currently owns a revamp.

### Account merge SQL needs typed ownership, not cosmetic quoting

The merge repository holds a long ordered tuple of one-line SQL strings, including conflict-collapse rules and
payload rewrites. Merely triple-quoting it would remain hard to validate.

Model routine updates/deletes with SQLAlchemy Core tables and extract named merge phases: identities/profiles,
creator credit, permissions, notifications, submissions, and final source-row removal. Keep raw PostgreSQL SQL only
for conflict semantics Core cannot express cleanly; store each as a named triple-quoted statement with a focused
integration test. The entire merge remains one transaction under both account-row locks.

### Record vocabulary conflates stable series, versioned rules, and outcomes

Current `RecordCompetition`, `RecordDefinition`, and `RecordResult` are distinguishable only after reading their
docstrings. Adopt the following application/model names while initially retaining physical table names:

- `RecordSeries`: stable public category identity across rulesets;
- `RecordRule`: one ruleset-specific title/calculation definition in that series;
- `RecordStanding`: one computation run's resolved/unresolved/no-candidate outcome.

Keep user-facing “record” for the public concept. Inventory notifications, search projection, suggestions
catalogue/providers, API, and tests before renaming Python symbols; use temporary aliases so consumers can migrate in
reviewable stages. If physical names are later changed, use a separate forward migration after code and observability
have adopted the vocabulary; do not combine table renames with behavior.

### Record persistence should use domain enums

Map record class, build kind, version scope, materialization source, facet kind, computation status, and standing
status through `StrEnum` values. Reuse existing domain enums where semantics match and introduce infrastructure enums
only for persistence-only states. Retain explicit database checks and add totality tests so enum additions require a
migration.

### Public-holder filtering remains in the API route

`get_record` fetches holder builds and filters `Status.CONFIRMED` in the route, then treats hidden/missing holders as
integrity failure. Move this to a `PublicRecordQueryService` that returns a complete `PublicRecordDetail` or raises a
typed data-integrity error. The route should authorize, invoke, and serialize only. Add a build-owned
`get_public_summaries(ids)` read port so records do not import build persistence and private aggregate fields are not
loaded.

## Planned work

1. **Pin multiple-identity behavior.** Add accounts with two identities per provider and classify every singular
   caller's intended selector.
2. **Replace singular lookup and correct docs.** Introduce `identities_for`, migrate callers, remove `identity`, and
   rewrite state/mechanics docstrings.
3. **Decompose account merge persistence.** Introduce named phases, Core statements, retained raw-SQL constants, and
   phase-level plus end-to-end transaction tests.
4. **Adopt record vocabulary in Python.** Inventory all downstream imports, introduce temporary aliases, migrate
   notifications/search/suggestions/API/tests in stages, then remove aliases without changing physical tables/routes.
5. **Type record persistence.** Map enums, eliminate routine `.value`/string comparisons, and enforce schema totality.
6. **Move public record assembly into application queries.** Fetch only public holder summaries, retain order, and
   centralize integrity failure.
7. **Document the UUIDv7 non-plan.** State that repository-wide conversion has no current implementation owner; do no
   local schema rewrite or misleading delegation.

## Interface sketch

```python
@dataclass(frozen=True, slots=True)
class Account:
    identities: tuple[AccountIdentity, ...]

    def identities_for(self, provider: IdentityProvider) -> tuple[AccountIdentity, ...]: ...


@dataclass(frozen=True, slots=True)
class PublicRecordDetail:
    standing: RecordStanding
    holder_builds: tuple[PublicBuildSummary, ...]
```

Persistence must return identities in stable `(provider, id)` order, matching facts exposed by the current domain
value. A call site may not use `[0]` without a named policy and a test containing at least two matches; selecting by
`created_at` requires an explicit domain-contract change.

## Test matrix

- Account domain/application: zero/one/two identities per provider, exact subject lookup, public visibility, avatar
  selection, identity refresh, voting/profile callers, and removal of the singular helper.
- Merge repository: every named phase, collisions, restrictive permission effect, notification/subscription
  coalescing, retained payload owners, rollback on a late failure, concurrent reversed merge, and audit attribution.
- Record persistence: every enum value, unknown stored value, series/rule/standing round trip, ruleset replacement,
  stable public ID, computation state, and check-constraint totality.
- Record API/application: ordered public holders, missing/private holder integrity failure, empty/tied holders,
  pagination, and route serialization with no filtering logic.
- Migration/architecture: no unintended physical rename, UUIDv7 stays linked to the existing plan, and new raw SQL in
  account merge requires an explicit allowlist reason.

## Thread dispositions

| Thread | Disposition |
|---|---|
| [`squid/accounts/domain/models.py`: “why not identities(...)”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791071265) | **Fix in milestones 1–2.** Return all identities for a provider and make singular selection explicit. |
| [`squid/accounts/domain/models.py`: “docstring seem inaccurate when ClaimStatus exists”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791104476) | **Fix in milestone 2.** Describe the request and resolution state, not only pending claims. |
| [`squid/accounts/domain/models.py`: “returned by persistence is not necessary”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791106450) | **Fix in milestone 2.** Describe redeemable identity facts without naming a caller. |
| [`squid/accounts/infrastructure/models.py`: “merge this into id when we do the UUIDv7 revamp”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790796051) | **Retain.** No repository-wide UUIDv7 migration is currently planned; preserve the public/internal ID split and do not create an account-only conversion. |
| [`squid/accounts/infrastructure/repository.py`: “can we at least make this triple quoted string, and ideally using sqlalchemy”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791446925) | **Fix in milestone 3.** Use named SQLAlchemy merge phases; triple-quote only irreducible PostgreSQL statements. |
| [`squid/records/infrastructure/models.py`: “the concept of "Competition" vs "Definition" vs just a normal "Record" is confusing”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790793074) | **Fix in milestone 4.** Adopt series/rule/standing vocabulary and pin conversions. |
| [`squid/records/infrastructure/models.py`: “enum”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790791549) | **Fix in milestone 5.** Map record class/build kind/version scope/materialization values through enums. |
| [`squid/records/infrastructure/models.py`: “enum”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790792005) | **Fix in milestone 5.** Map computation/standing/facet states through closed enums. |
| [`squid/api/v1/records.py`: “api level filtering is bad design”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790725586) | **Fix in milestone 6.** Application queries return public-complete record details; the route only serializes. |

## Delivery and rollout

Identity lookup changes land before merge refactoring so tests use final account semantics. Record Python renames land
without schema changes, followed by enum mappings and then application query ownership. UUIDv7 remains outside this
series. Keep each account merge phase change independently reviewable while preserving one production transaction.
