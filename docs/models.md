# Entities and persistence

Persisted entities live in `squid/<context>/models.py` or a `models/` package. These SQLAlchemy classes also carry
entity behavior; there is no second domain representation of the same row. Domain policies, services, and transports
may use them, while queries and sessions stay in persistence adapters. Shared models depend on SQLAlchemy and pure
values, and must not import services, repositories, transports, or native engines.

Entities use object identity for equality and hashing. Compare IDs when deciding whether two separately loaded
objects refer to the same record. Enums and embedded values remain typed in Python; column adapters preserve their
existing integer, text, array, or JSON representation.

Repositories own short transactions and return loaded, detached entities. Mutations persist only through an explicit
repository write. Relationships reject implicit SQL; each repository selects the data its callers need. Cross-context
facts such as a build's creators and versions are loaded in batches.

The phase-two pilot moves server settings reads and writes into an application-owned unit of work. The service receives a
`UnitOfWorkFactory` and uses `async with self._uow_factory() as uow`. Its `settings` repository returns attached
entities, so mapped changes persist at `await uow.commit()` without a separate save. Commit ends the transaction;
leaving without it rolls back. Closing detaches successfully committed entities. Existing repository APIs outside
this pilot retain their transaction ownership and detached-read behavior.

Keep values and projections separate when they represent something other than a live entity: incomplete build drafts,
versioned submission and inference payloads, vote snapshots, compiled permission rules, delivery claims, schematic
analyses and download projections, search results, and record calculations. Public API schemas remain explicit.

Build previews use `copy_for_edit()` to copy application facts without copying SQLAlchemy state. Replace JSON and array
values when editing them. New fields belong on the entity; explicit input and output schemas change only when their
contracts need the field. See [Adding a build attribute](new-build-attribute.md).

Architecture tests cover entity equality, explicit relationship loading, and model dependencies. Repository integration
tests check detached access, round trips, revision guards, preview isolation, and transaction behavior.
