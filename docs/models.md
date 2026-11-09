# Entities and persistence

Persisted entities live in `squid/<context>/models.py` or a `models/` package. These SQLAlchemy classes also carry
entity behavior; there is no second domain representation of the same row. Domain policies, services, and transports
may use them, while queries and sessions stay in persistence adapters. Shared models depend on SQLAlchemy and pure
values, and must not import services, repositories, transports, or native engines.

Entities use object identity for equality and hashing. Compare IDs when deciding whether two separately loaded
objects refer to the same record. Enums and embedded values remain typed in Python; column adapters preserve their
existing integer, text, array, or JSON representation.

Application operations own short transactions through `async with self._uow_factory() as uow`. The factory is a
callable type alias; service-specific protocols expose the repositories each workflow needs. One concrete SQLAlchemy
UoW supplies those repositories with the same session. Repositories query and flush, while the application decides
when to call `await uow.commit()`.

Entities loaded inside a scope are tracked: mapped changes persist at commit without a separate save. Commit is
explicit and terminal. Leaving without it discards writes, and closing the scope detaches loaded entities. Services
return entities with the data their callers need already loaded, or return projections. Relationships reject implicit
SQL; cross-context facts such as a build's creators and versions are loaded in batches.

Helpers participating in an existing transaction receive its UoW explicitly. Permission checks within those workflows
bypass the process cache and never cache uncommitted rules. Independent concurrent tasks use separate scopes.
External API calls, uploads, inference, and rendering run outside database transactions; writes recheck relevant
permissions, revisions, and claim tokens inside the final scope. Worker claim and completion are separate transactions.
Infrastructure connection owners, such as listeners and queue-health monitoring, retain their own lifetimes.

Keep values and projections separate when they represent something other than a live entity: incomplete build drafts,
versioned submission and inference payloads, vote snapshots, compiled permission rules, delivery claims, schematic
analyses and download projections, search results, and record calculations. Public API schemas remain explicit.

Build previews use `copy_for_edit()` to copy application facts without copying SQLAlchemy state. Replace JSON and array
values when editing them. New fields belong on the entity; explicit input and output schemas change only when their
contracts need the field. See [Adding a build attribute](new-build-attribute.md).

Build history stores operations in `build_edit_history` and shared JSON snapshots in `build_history_states`.
Use `uow.build_history.list_history(build_id)` for timeline metadata and `get_history_entry(build_id, entry_id)`
for detached before/after states. These internal queries do not authorize readers and can inspect deleted builds.
Database triggers reject updates, deletes, and truncation of either history table.

Build writers supply a `BuildChange` identifying the authenticated account or named system actor. Repository writes
capture persisted state under the build lock and append history in the same transaction. To group nested repository
writes, enclose them in `async with uow.builds.changes(change)` and commit only after that scope exits. Its attribution
wins over nested contexts. New direct SQL or tracked-entity write paths must join a recording operation before mutation;
manual SQL is outside the capture guarantee. Multi-build operations must acquire their complete build set in ID order.

Consecutive edits reuse their shared state. If the stored history no longer matches the actual pre-edit state, the
recorder preserves that state separately rather than attributing the discrepancy to the editor. No-op saves and changes
limited to leases, embeddings, timestamps, or revision counters create no history. Snapshots include attachment identity
and publication metadata, but not binary files or recomputable schematic analyses. Decimal tag values are strings to
preserve precision. Extend the explicit snapshot serializer when adding build fields; retain old payload formats.

Pause application writers before applying the build-history migration and deploy the recording code before restarting
them. The migration takes one baseline of each existing build; it cannot recover prior edits. Retain history indefinitely.
Disabling the append-only guards is an explicit maintenance operation, not an application write path.

Architecture tests cover entity equality, explicit relationship loading, model dependencies, and transaction ownership.
Repository integration tests check detached access, round trips, revision guards, preview isolation, and transaction behavior.

Record persistence follows the rules document's vocabulary. A `BaseTitle` (first, fastest, smallest, fastest smallest,
or smallest fastest) combines with a build kind, version scope, and record category to form a stable `RecordTitle`
in `record_titles`. Its `title_id` survives recomputation and rules changes. `VersionScope.UP_TO_DATE` is stored as
`up_to_date`; `all_time` includes every qualifying version.

A `TitleDefinition` in `title_definitions` stores a title's wording under a `RulesRevision` in `rules_revisions`.
The revision identifies the rules document hash and calculator/formatter versions. `DefinitionFacet` rows in
`title_definition_facets` capture the definition's taxonomy. A `ComputationRun` in `record_computation_runs` produces
`TitleResult` rows in `title_results`, with tied holders in `title_holders` and chronology in `title_holder_history`.
Public queries return results from the active run, so `result_id` and `definition_id` have different lifetimes
from the stable `title_id`.
