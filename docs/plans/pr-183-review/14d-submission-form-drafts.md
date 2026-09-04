# PR #183 Review 14D: Submission Forms and Drafts

## Scope

This plan covers seventeen threads concerning revisioned form manifests, account-owned drafts, API schemas,
idempotent field operations, persistence typing, user vocabulary, and their contract tests. Durable finalization and
canonical build creation begin only after a draft is valid and belong to 14E. Upload processing belongs to 14F.

## Findings and decisions

### Ambient localization and Pydantic DTOs already landed

The reviewed form builder threaded locale state manually. Current `build_submission_manifest` enters
`localization_scope`, whose context is ambient for nested field construction. API responses and requests are already
strict Pydantic models with `from_domain`/`to_domain` boundaries.

Retain both changes. Add concurrency coverage proving two manifests localized in different tasks do not leak locale,
and an architecture check preventing Pydantic models from entering the domain/application packages.

### Manifest ownership is still split awkwardly

`SubmissionFormService.manifest_revision` constructs `CheckedInFormManifestRegistry` ad hoc while the draft service
accepts a registry port. The fixed builder, revision lookup, and dynamic option catalog therefore have two composition
paths.

Introduce one injected `FormManifestRegistry` that owns current/revisioned checked-in manifests and one
`FormOptionCatalog` for dynamic choices. `SubmissionFormService` delegates to those collaborators and never creates a
concrete registry internally. The registry must return immutable revisions and fail closed when a binary can no
longer validate a pinned revision.

### User-facing field vocabulary exposes implementation terms

The current form still publishes a `provenance` section, a “sponsoring server” control, and bare “Opening width”
labels. Draft and error text still says “synchronized,” “normalization,” and sometimes “media.” Those are internal
storage/pipeline terms rather than actions a submitter understands.

Adopt this public vocabulary:

- section `submission_context`, displayed as “About this submission,” replaces `provenance`;
- “Credit the Minecraft server this submission came from” replaces “sponsoring server” and remains Paper-only;
- “Clear opening width/height/depth” makes door dimensions unambiguous;
- “attachments” describes images/videos/schematics collectively; “processing” describes work the server performs;
- “draft” replaces “synchronized draft” except in protocol documentation that explains synchronization;
- `DraftValidationError` replaces `DraftIncompleteError`, because the same error carries invalid as well as missing
  fields.

Field IDs are a versioned wire contract. Do not silently rename `provenance` in revision 1. Publish revision 2 with
the new ID, retain the revision-1 registry/decoder, and add an explicit v1-to-v2 draft upgrade operation. Labels and
help text can improve in both revisions where the wire meaning is unchanged.

### Enum work is partly complete

`SubmissionOrigin`, `ControlKind`, `ValueKind`, `VisibilityOperator`, `DraftStatus`, and `FieldOperationKind` are now
`StrEnum`s in the domain. Persistence still stores several as `Mapped[str]` and scatters `.value` conversion.

Map text columns through the domain enums using the project's non-native enum convention while keeping PostgreSQL
check constraints. Add a totality test that compares enum values to each check constraint/migration declaration.

### Draft change keys are validated twice

The Pydantic `IdempotencyKey` alias and `DraftChange.__post_init__` repeat the same regex/length rule. This key is a
draft-operation identifier, not the optional HTTP `Idempotency-Key` from 14B.

Introduce `DraftChangeKey`, an immutable string value validated once in the domain. Pydantic uses a `TypeAdapter` or
annotated validator that constructs it; persistence serializes `str(key)`. Keep its 8–255 visible-ASCII contract
distinct from HTTP replay keys and rename the API alias accordingly.

### Attachment-retention behavior is no longer a TODO

Draft deletion deliberately retains content-addressed artifacts and marks jobs discarded; reference-aware cleanup
now exists. Replace the historical explanatory comment with a direct link/name for the cleanup contract and add an
integration test that deletion releases the draft without deleting an object still referenced by another upload.

### Contract fakes should implement the contracts they replace

`tests/unit/submissions/test_api_contract.py` still uses duck-typed `FakeForms`, `FakeDrafts`, and
`FakeFinalization`. Make them explicit subclasses of the relevant protocols/application services with `@override`,
or use shared typed recorders where the concrete service is intentionally final. This should surface endpoint tests
that model methods production does not have.

## Planned work

1. **Pin the current wire contract.** Snapshot manifest revisions, origin visibility, strict Pydantic rejection, locale
   isolation, and the existing revision-1 field IDs.
2. **Unify manifest composition.** Inject the registry and option catalog; remove internal concrete construction and
   cover unsupported pinned revisions.
3. **Create domain-owned `DraftChangeKey`.** Route API parsing and persistence through it; delete duplicate regexes.
4. **Type persistence boundaries.** Map draft status/origin/operation kinds as enums and add schema-totality tests.
5. **Publish vocabulary revision 2.** Update labels/help text, add the renamed section ID only in v2, retain v1, and
   implement deterministic draft upgrade with conflict/idempotency handling.
6. **Rename structured errors.** Move from incomplete/synchronized/media language to validation/draft/attachment
   language while preserving stable error codes for clients through a documented deprecation alias if needed.
7. **Close lifecycle and test seams.** Pin reference-aware cleanup after draft deletion and type every API contract
   fake.

## Interface sketch

```python
@dataclass(frozen=True, slots=True)
class DraftChangeKey:
    value: str

    def __post_init__(self) -> None:
        if _DRAFT_CHANGE_KEY.fullmatch(self.value) is None:
            raise ValidationError(...)


class FormManifestRegistry(Protocol):
    async def current(self, *, locale: str | None) -> FormManifest: ...
    async def get(
        self, schema_id: str, revision: int, *, locale: str | None
    ) -> FormManifest | None: ...
    async def upgrade(self, draft: DraftSnapshot, *, target_revision: int) -> DraftSnapshot: ...
```

The implementation may use a `str` subclass instead of a dataclass if Pyrefly and Pydantic preserve the type. The
acceptance criterion is one validator and a distinct type from the HTTP replay key.

## Test matrix

- Manifest: exact v1/v2 snapshots, stable option values, locale isolation under concurrent tasks, origin-specific
  fields, missing required client capability, and unavailable revision.
- Upgrade: v1 provenance answer mapped to v2 submission context, unknown/removed fields, stale revision race,
  idempotent retry, and rollback leaving the v1 draft unchanged.
- Domain/API: one `DraftChangeKey` rule, strict extra-field rejection, JSON value normalization, enum round trips, and
  exhaustive operation mapping.
- Repository: replay under concurrent equal/different keys, optimistic revision conflict, lifecycle lock namespace,
  typed status/origin persistence, deletion with shared artifacts, and expiry.
- User presentation: no internal vocabulary in form labels, public error titles/details/actions, OpenAPI descriptions,
  or Discord/web render snapshots.
- Architecture/type checks: Pydantic remains in API serialization, injected ports are explicitly subclassed, and test
  doubles implement their real contracts.

## Thread dispositions

| Thread | Disposition |
|---|---|
| [`squid/submissions/application/forms.py`: “use a contextvar, hopefully done in later commits?”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790829849) | **Already addressed.** `localization_scope` supplies ambient task-local context; milestone 1 adds isolation proof. |
| [`squid/submissions/application/forms.py`: “why.”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790834570) | **Fix in milestone 2.** Remove ad hoc concrete registry construction and make manifest ownership explicit. |
| [`squid/api/v1/schemas/submissions.py`: “why are we not using Pydantic for this”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790911109) | **Already addressed.** Strict Pydantic DTOs own HTTP validation and domain conversion. |
| [`squid/submissions/application/forms.py`: “many confusing docstrings”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790828133) | **Fix in milestones 2 and 5.** State manifest, option, revision, and renderer ownership directly. |
| [`squid/submissions/application/forms.py`: “ban provenance”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790831496) | **Fix in milestone 5.** Publish `submission_context` in revision 2 and retain a v1 compatibility decoder. |
| [`squid/submissions/application/forms.py`: “this is mainly for our internal inference pipeline, not for users”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790832051) | **Fix in milestone 5.** Separate internal source evidence from fields a submitter can see or edit. |
| [`squid/submissions/application/forms.py`: “a *Sponsor Server* is an internal concept. Change the wording.”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790832509) | **Fix in milestone 5.** Ask whether to credit the originating Minecraft server. |
| [`squid/submissions/application/forms.py`: “not sure about calling these "opening width"”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790833459) | **Fix in milestone 5.** Use “clear opening” labels and pin translations. |
| [`squid/submissions/application/drafts.py`: “DraftIncompleteError doesn't read like the correct error here”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790886467) | **Fix in milestone 6.** Rename it to the validation outcome it actually carries. |
| [`squid/submissions/errors.py`: “dont mention synchronized”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790889517) | **Fix in milestone 6.** Keep synchronization in protocol docs, not end-user errors. |
| [`squid/submissions/errors.py`: “using the word media here is confusing. We should change it everywhere”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3803768419) | **Fix in milestone 6.** Use attachment/artifact terms according to audience. |
| [`squid/submissions/domain/forms.py`: “enum?”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790880885) | **Already addressed.** The closed form vocabulary is `StrEnum`; milestone 4 completes persistence mapping. |
| [`squid/submissions/infrastructure/models.py`: “enum”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790897637) | **Fix in milestone 4.** Type the mapped text columns with domain enums. |
| [`squid/api/v1/schemas/submissions.py`: “another definition for idempotency key”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790909256) | **Fix in milestone 3.** Pydantic constructs `DraftChangeKey` instead of restating its rule. |
| [`squid/submissions/domain/drafts.py`: “don't like the fact that we are having multiple places to validate idempotency key format”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3790838994) | **Fix in milestone 3.** One domain-owned validator becomes authoritative. |
| [`squid/submissions/infrastructure/repository.py`: “is this a TODO?”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3803760966) | **Already addressed by reference-aware cleanup.** Milestone 7 turns the comment into a pinned lifecycle contract. |
| [`tests/unit/submissions/test_api_contract.py`: “subclass”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3803659673) | **Fix in milestone 7.** Test doubles explicitly implement the ports/services they replace. |

## Sequencing and delivery

Land the single-key validator and persistence typing before manifest v2 so the upgrade operation uses the final
mutation contract. V1 retention and v2 publication are one atomic commit. Error aliases, if API clients rely on the
old code, remain for one version and are removed only with an OpenAPI compatibility note.
