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

Build writers open `async with uow.builds.editing(actor=account_id, source="api")` before mutations.
Use a named string actor for system work. Optional operation and correlation keywords describe the reason for the edit.
Repository methods and tracked entities need no history arguments. PostgreSQL captures the first state under the build
lock and records the final state at commit, producing at most one entry per changed build per transaction.
Direct SQL uses the same triggers and must first call `begin_build_edit(jsonb)` with attribution.

Only one editing scope is allowed per transaction; nested scopes and attribution replacement fail. Scope exit flushes
pending ORM changes and closes the scope. Commit may happen inside the scope or afterward; exceptions roll back.
Meaningful writes outside an open scope fail, including direct SQL. Multi-build operations should acquire their complete
build set in ID order. History and build mutations commit or roll back together.

Consecutive edits reuse their shared state. If the stored history no longer matches the actual pre-edit state, the
recorder preserves that state separately rather than attributing the discrepancy to the editor. No-op saves and changes
limited to leases, embeddings, timestamps, or revision counters create no history. Snapshots include attachment identity
and publication metadata, but not binary files or recomputable schematic analyses. Decimal tag values are strings to
preserve precision. Extend the explicit snapshot serializer when adding build fields; retain old payload formats.

Pause application writers before applying the build-history migration and deploy the recording code before restarting
them. The initial history migration takes one baseline of each existing build; it cannot recover prior edits.
The editing-scope migration preserves existing history and requires all writers to use scopes before restarting.
Retain history indefinitely.
Disabling the append-only guards is an explicit maintenance operation, not an application write path.

Persisted editor changes use `EditCommand` and `EditExecutor` in `squid/edits`. A command carries the revisions the
caller read. Its handler checks current authority and captures the values needed to reverse it before writing.
`FieldCommand` supplies the common projection, validation, and write flow from an explicit Pydantic value schema;
`Aggregate` supplies the domain's locking and revision policy. Override those steps for domain rules, rather than
adding history callbacks to each UI. Permission edits use the permission epoch as one shared graph revision.

Discord's `EditController` retains inverse receipts in the live workspace. Bind forms with `controller.bind()` when
they open so refreshing the screen cannot silently rebase a stale modal. Undo and redo execute ordinary authorized
commands against the controller's last committed revisions. Final commands discard history only for their targets.
The database retains operation identity and result metadata for 24 hours to resolve uncertain commits; it does not
persist the session's inverse values. HTTP mutations using these commands share the executor's operation identity.

Attachment uploads stage hidden references before a membership command accepts them. Removal keeps shared source IDs
and processing results available for undo. A mounted parent renews resource leases every 30 seconds; leases expire
after 120 seconds without renewal. History eviction, branching, finalization, and closure release those leases.
Cleanup reclaims resources only after both draft references and history leases cease to retain them. Processing an
already-declared file does not advance the draft revision; changing its membership does.

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
