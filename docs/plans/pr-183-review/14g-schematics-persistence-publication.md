# PR #183 Review 14G: Schematic Persistence and Publication

## Scope

This plan covers ten later-review threads in schematic application ports/read models, the PostgreSQL repository,
render-job migration, public API schema, and repository integration tests. Native engine safety and worker protocol
hardening are complete under plans 6–8; this plan changes storage/publication ownership without reopening them.

## Findings and decisions

### “Projection/project” obscures concrete actions

Runtime APIs still expose `project_render`, `_replace_projected_render`, and “projection” docstrings, while the
migration filename/table comments use the same term. The operation actually publishes one generated preview as a
build link while preserving manually managed render links.

Use `publish_cached_preview`, `replace_generated_preview_link`, and “generated preview link” throughout runtime code.
Rename the migration file before merge only if its revision has never shipped; otherwise leave the historical
filename/revision and add a forward migration for physical table names. Never rewrite an applied Alembic revision.

### The repository owns two aggregates

`SchematicRepository` stores content/analysis and also locks a build row, mutates `build_links`, increments build
revision, and registers preview objects. The locking currently prevents a stale render from winning primary
replacement, but its ownership is difficult to see.

Split persistence into:

- `PostgresSchematicStore`: file bytes/analysis/publication/simulation/duplicate queries;
- `PostgresSchematicPreviewPublisher`: recipe records, preview jobs, primary fencing, and the generated build-link
  transaction;
- `SchematicPreviewService`: application orchestration over renderer, artifact store, and publisher.

The publisher keeps the build-row lock and transaction because the cross-table invariant is atomic: a generated link
must refer to the primary attachment observed under the same lock. Do not replace this with eventually consistent
events unless the build API can tolerate stale preview links and exposes repair semantics.

### Primary is correct for featured previews, not for attachment listing

Current `list_public_for_build` includes every publicly downloadable schematic, and `public_download` addresses one
explicit attachment. Only featured rendering/simulation defaults use `get_primary`. Retain that policy and name it:
`get_featured` may replace `get_primary` at the application surface while the database field remains `is_primary`.
Add a two-public-attachment test proving both list/download while only the featured one supplies the build preview.

### Manual row mapping was already extracted

Analysis and simulation conversion now lives in `squid/schematics/infrastructure/mapping.py`; repository fetches use
`to_stored_schematic`. Keep that boundary and make its decode exhaustive. A malformed stored enum/JSON shape raises
`DataIntegrityError` with schematic identity, not a raw `ValueError`.

### Read models contain several unrelated histories

`application/queries.py` mixes publication policy, duplicate candidates, render preparation/outcomes, skip messages,
and simulation evidence. Split it into `attachments.py`, `duplicates.py`, and `previews.py` under the application
package; export only the public types consumers need. This is the requested historical-baggage cleanup, not a rename
of every dataclass.

### Public links must come from routing/configuration

`SchematicSummary.from_domain` hard-codes `/v1/builds/.../content`. Domain-to-DTO conversion lacks the mounted root,
proxy prefix, and route name. Have the route pass a link built through FastAPI/Starlette reverse routing (or a shared
API link builder) and keep the Pydantic model responsible only for validation. Test a non-default `root_path`.

### Limits have two authorities in tests

The integration test spells `16 * 1024 * 1024` while model/domain values already expose the upload bound. Create one
application/config limit supplied to preflight, object storage, persistence, and tests. A database check necessarily
embeds the migration-time numeric value; a schema-totality test compares it to the declared deployment constant so a
future change requires an explicit migration.

## Planned work

1. **Pin current invariants.** Cover two public attachments, featured-only preview, manual link preservation, primary
   replacement races, and revision increments.
2. **Replace runtime projection vocabulary.** Rename methods/types/docs and decide migration-file treatment from
   deployment history before editing Alembic artifacts.
3. **Split preview publication persistence.** Move render/job/link transactions behind
   `SchematicPreviewPublisher` while preserving the exact row-lock order.
4. **Split read models and harden mapping.** Create focused modules, exhaustive decoders, and structured corruption
   failures.
5. **Centralize the upload limit.** Share the runtime value and add migration/schema drift coverage; remove test
   literals.
6. **Build public URLs through the router.** Pass links into Pydantic response construction and cover mounted roots.
7. **Re-run race and worker integration.** Verify the separated publisher against real PostgreSQL and the real worker
   pool, including crash/retry after object upload and before link commit.

## Interface sketch

```python
class SchematicPreviewPublisher(Protocol):
    async def record_and_publish(
        self,
        *,
        schematic_id: int,
        recipe_hash: str,
        artifact: StoredPreviewArtifact,
    ) -> StoredRender | None: ...

    async def publish_cached(
        self,
        *,
        schematic_id: int,
        recipe_hash: str,
    ) -> bool: ...
```

Both operations return a negative result when the attachment is no longer featured. They must never delete a manual
`build_links.media_type == "render"` row; generated-link ownership is established by a registered render record, not
by URL shape.

## Test matrix

- Store: content-addressed idempotency/integrity, upsert, public rights/sanitization, all-attachment listing,
  duplicate tiers, typed mapping corruption, uploader attribution, and shared file rows.
- Publisher: current/non-current source, cached/fresh recipe, manual link coexistence, replacement, concurrent
  primary change, revision increments, missing build/schematic, and retry after ambiguous commit.
- Migration: upgrade/downgrade of preview jobs/object keys and any forward physical rename; never revise applied IDs.
- API: two downloadable attachments, one featured preview, explicit download ID, license facts, route-generated link,
  proxy `root_path`, and unavailable private attachment.
- Limits: exact boundary/over-boundary through domain, repository, database check, and API preflight using one value.
- Worker: permanent skip vs retry, object upload failure, publication failure, stale featured source, and cleanup.

## Thread dispositions

| Thread | Disposition |
|---|---|
| [`alembic/versions/2026_08_10_1900-e1f2a3b4c5d6_durable_schematic_render_projection.py`: “ban projection”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3796148680) | **Fix in milestone 2 subject to deployment history.** Rename an unshipped filename; never rewrite an applied revision. |
| [`squid/schematics/application/ports.py`: “ban project”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3796489853) | **Fix in milestones 2–3.** Name cached/fresh preview publication actions directly. |
| [`squid/schematics/application/ports.py`: “confusing docstirng”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3796491045) | **Fix in milestone 3.** State featured-source fencing, generated-link ownership, and return behavior. |
| [`squid/schematics/infrastructure/repository.py`: “wtf is this crap”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3796495596) | **Fix in milestone 3.** Separate schematic storage from preview/build-link publication without losing atomic fencing. |
| [`squid/schematics/infrastructure/repository.py`: “is this really a good design? actually unsure.”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3796530955) | **Resolve in milestone 3.** Keep the cross-table transaction in a purpose-named publisher and pin its invariant. |
| [`squid/schematics/application/queries.py`: “ok we need a single round of big cleanup of historical baggage”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791237158) | **Fix in milestone 4.** Split attachment, duplicate, and preview read models and narrow exports. |
| [`tests/integration/schematics/test_repository.py`: “use the constant”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791246495) | **Fix in milestone 5.** Remove the literal and enforce runtime/migration agreement. |
| [`squid/schematics/infrastructure/repository.py`: “only primary?”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3789550075) | **Already correct at current HEAD; retain.** All public attachments list/download; only the featured attachment supplies the generated preview. |
| [`squid/schematics/infrastructure/repository.py`: “doing this manually seem really error prone”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3796514203) | **Already addressed by `infrastructure/mapping.py`; strengthen in milestone 4.** Make decoding exhaustive and failures structured. |
| [`squid/api/v1/schemas/schematics.py`: “hmm, idk how to feel about hard coding an url that could be changed elsewhere in code”](https://github.com/redstone-squid/Redstone-Squid/pull/183#discussion_r3791235798) | **Fix in milestone 6.** Reverse the named route under the actual mounted root. |

## Delivery and rollout

Land invariant tests before splitting the repository. Runtime vocabulary and Python module moves are independent of
physical migration names. The publisher split must preserve lock ordering in one commit. URL generation is a small
API commit. Any database rename uses expand/contract and a new Alembic revision if the original revision shipped.
