"""Alembic environment for the application-owned PostgreSQL schema."""

from collections.abc import MutableMapping
from logging.config import fileConfig
from typing import Literal

from alembic_utils.replaceable_entity import register_entities
from sqlalchemy import Connection, engine_from_config, make_url, pool
from sqlalchemy.engine import URL
from sqlalchemy.schema import SchemaItem

from alembic import context
from squid.config import load_database_config
from squid.persistence.alembic_entities import ALEMBIC_UTIL_ENTITIES
from squid.persistence.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
MANAGED_ENTITY_NAMES = {f"{entity.schema}.{entity.signature}" for entity in ALEMBIC_UTIL_ENTITIES}
MANAGED_ENTITY_TYPES = {type(entity) for entity in ALEMBIC_UTIL_ENTITIES}
ALEMBIC_UTIL_ENTITY_TYPE_NAMES = {
    "extension",
    "function",
    "grant_table",
    "materialized_view",
    "policy",
    "trigger",
    "view",
}

register_entities(ALEMBIC_UTIL_ENTITIES, entity_types=MANAGED_ENTITY_TYPES)


def database_url() -> URL:
    """Return the synchronous migration URL configured for this environment."""
    url = make_url(load_database_config().url.get_secret_value())
    return url.set(drivername=f"{url.get_backend_name()}+psycopg2")


def include_object(
    _object: SchemaItem,
    name: str | None,
    type_: str,
    _reflected: bool,
    _compare_to: SchemaItem | None,
) -> bool:
    """Exclude Alembic's bookkeeping table from application-schema drift checks."""
    return not (type_ == "table" and name == "alembic_version")


def include_name(
    name: str | None,
    type_: str,
    _parent_names: MutableMapping[
        Literal["schema_name", "table_name", "schema_qualified_table_name"],
        str | None,
    ],
) -> bool:
    """Limit replaceable-entity comparison to objects owned by this application."""
    if type_ == "schema":
        return name is None
    if type_ in ALEMBIC_UTIL_ENTITY_TYPE_NAMES and name is not None:
        return name in MANAGED_ENTITY_NAMES
    return True


def configure_context(connection: Connection | None = None) -> None:
    """Configure schema comparison consistently for online and offline operation."""
    context.configure(
        connection=connection,
        url=None if connection is not None else database_url(),
        target_metadata=target_metadata,
        include_object=include_object,
        include_name=include_name,
        include_schemas=False,
        compare_type=True,
        compare_server_default=True,
        version_table_schema="public",
        literal_binds=connection is None,
        dialect_opts={"paramstyle": "named"},
    )


def run_migrations_offline() -> None:
    """Emit migration SQL without opening a database connection."""
    configure_context()
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations through the configured synchronous database driver."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = database_url().render_as_string(hide_password=False)
    connectable = engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        configure_context(connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
