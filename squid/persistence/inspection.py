"""Database schema inspection and consistency checks."""

from __future__ import annotations

import logging
from typing import Any, cast

from sqlalchemy import Engine, Inspector, Table, inspect, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import ColumnProperty, DeclarativeBase, Mapper, RelationshipProperty

# noinspection PyProtectedMember
from sqlalchemy.orm.clsregistry import ClsRegistryToken, _ModuleMarker  # pyright: ignore[reportPrivateUsage]

logger = logging.getLogger(__name__)


# Handle some common type variations
type_mapping = {
    "integer": ["int", "integer", "int4"],
    "bigint": ["bigint", "int8"],
    "smallint": ["smallint", "int2"],
    "string": ["string", "varchar", "text"],
    "boolean": ["boolean", "bool"],
    "float": ["float", "real", "float4"],
    "double": ["double", "float8"],
    "json": ["json", "jsonb"],
}


def normalize_type(type_name: str) -> str:
    for base_type, variants in type_mapping.items():
        if any(variant in type_name for variant in variants):
            return base_type
    return type_name


class DatabaseSchema:
    """Table and column metadata read from the database once, at construction."""

    def __init__(self, inspector: Inspector):
        logger.info("Getting table names from database %s", inspector.engine.url)
        self.tables = inspector.get_table_names()
        self.columns: dict[str, dict[str, Any]] = {}

        for table in self.tables:
            logger.info("Loading information from table %s", table)
            self.columns[table] = {c["name"]: c for c in inspector.get_columns(table)}


def check_relationship_property(
    column_prop: RelationshipProperty, schema: DatabaseSchema, klass: type[DeclarativeBase], engine: Engine
) -> bool:
    """Return whether the relationship's target or secondary table is missing, logging each one.

    True means a mismatch was found; a relationship that does not resolve to a `Table` is skipped
    rather than reported.
    """
    errors = False

    if column_prop.secondary is not None:
        # Additional checks for many-to-many relationships
        if not isinstance(column_prop.secondary, Table):
            logger.info(
                "Skipping relationship %s in model %s because secondary is not a Table object", column_prop.key, klass
            )
            return errors

        # Check secondary table exists
        if column_prop.secondary.name not in schema.tables:
            logger.error(
                "Model %s declares many-to-many relationship %s with secondary table %s which does not exist in database %s",
                klass,
                column_prop.key,
                column_prop.secondary.name,
                engine.url,
            )
            errors = True

    if not isinstance(column_prop.target, Table):
        logger.info("Skipping relationship %s in model %s because target is not a Table object", column_prop.key, klass)
        return errors

    target_table = column_prop.target.name
    if target_table not in schema.tables:
        logger.error(
            "Model %s declares relationship %s to table %s which does not exist in database %s",
            klass,
            column_prop.key,
            target_table,
            engine.url,
        )
        errors = True

    return errors


def check_column_property(
    column_prop: ColumnProperty, schema: DatabaseSchema, klass: type[DeclarativeBase], engine: Engine
) -> bool:
    """Return whether the column's existence, type, nullability or foreign keys mismatch, logging each.

    True means a mismatch was found.
    """
    # TODO: unique constraints
    errors = False

    # A mapped column can belong to a parent's table under inheritance, so the table comes from the
    # column rather than from `klass.__tablename__`.
    for column in column_prop.columns:
        if column.table is None:
            logger.info(
                "Skipping column %s in model %s because it does not have a table associated with it",
                column.key,
                klass,
            )
            continue
        if not column.table._is_table:  # pyright: ignore[reportPrivateUsage]
            logger.info(
                "Skipping column %s in model %s because it does not originate from a Table object (%s)",
                column.key,
                klass,
                column.table,
            )
            continue
        assert isinstance(column.table, Table), "Expected column.table to be a Table instance"
        table = column.table.name
        # Check column exists
        if column.key not in schema.columns[table]:
            logger.error(
                "Model %s declares column %s which does not exist in database %s",
                klass,
                column.key,
                engine.url,
            )
            errors = True
            continue

        # Check column type
        db_column = schema.columns[table][column.key]
        model_type = column.type
        db_type = db_column["type"]

        # Compare type names, handling some common type variations
        model_type_name = str(model_type).lower()
        db_type_name = str(db_type).lower()

        if normalize_type(model_type_name) != normalize_type(db_type_name):
            logger.error(
                "Model %s column %s has type %s but database has type %s",
                klass,
                column.key,
                model_type,
                db_type,
            )
            errors = True

        # Check foreign key constraints
        if column.foreign_keys:
            for fk in column.foreign_keys:
                target_table = fk.column.table.name
                if target_table not in schema.tables:
                    logger.error(
                        "Model %s declares foreign key %s to table %s which does not exist in database %s",
                        klass,
                        column.key,
                        target_table,
                        engine.url,
                    )
                    errors = True
                else:
                    if fk.column.key not in schema.columns[target_table]:
                        logger.error(
                            "Model %s declares foreign key %s to column %s in table %s which does not exist in database %s",
                            klass,
                            column.key,
                            fk.column.key,
                            target_table,
                            engine.url,
                        )
                        errors = True

        # Check if the column is nullable
        if not column.nullable and db_column["nullable"]:
            logger.error(
                "Model %s declares column %s as non-nullable but database has it as nullable",
                klass,
                column.key,
            )
            errors = True

        if column.nullable and not db_column["nullable"]:
            logger.error(
                "Model %s declares column %s as nullable but database has it as non-nullable",
                klass,
                column.key,
            )
            errors = True

    return errors


def is_sane_database(base_cls: type[DeclarativeBase], engine: Engine) -> bool:
    """Return whether every model in *base_cls*'s registry has its table, columns and relationships.

    Column types are compared through `normalize_type`, so equivalent spellings match. Extra
    tables and columns in the database are not reported: this checks one direction only. Every
    mismatch is logged at error level; the return value says only whether there was one.

    Raises:
        TypeError: If *engine* is an AsyncEngine rather than a synchronous Engine.

    References:
        https://stackoverflow.com/questions/30428639/check-database-schema-matches-sqlalchemy-models-on-application-startup
    """
    if isinstance(engine, AsyncEngine):
        msg = "The engine must be a synchronous SQLAlchemy Engine, not an AsyncEngine."
        raise TypeError(msg)

    logger.debug("starting validation")
    inspector = inspect(engine)
    schema = DatabaseSchema(inspector)

    # Errors here are not suppressed: if a trivial query fails, every later query fails too.
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))

    errors = False

    # noinspection PyProtectedMember
    for name, klass in base_cls.registry._class_registry.items():  # pyright: ignore[reportPrivateUsage]
        logger.debug("Checking model %s (%s)", name, klass)
        if isinstance(klass, _ModuleMarker):
            logger.debug("Skipping module marker %s", name)
            continue
        if isinstance(klass, ClsRegistryToken):
            logger.debug("Skipping ClsRegistryToken %s", name)
            continue
        if not issubclass(klass, DeclarativeBase):
            logger.warning(
                "Cannot determine whether %s is actually a model because it is not a subclass of DeclarativeBase. "
                "If you use the declarative_base(), it dynamically generates a new class that cannot be determined."
                "We are assuming it is a model, but this may not be the case.",
                klass,
            )
            klass = cast(type[DeclarativeBase], klass)

        table: str = klass.__tablename__
        if not table:
            logger.error("Model %s does not have a __tablename__ attribute", klass)
            errors = True
            continue
        if table not in schema.tables:
            logger.error("Model %s declares table %s which does not exist in database %s", klass, table, engine.url)
            errors = True
            continue

        mapper = inspect(klass)
        assert isinstance(mapper, Mapper), "Expected mapper to be an instance of Mapper (uncertain)"

        try:  # If any error occurs during inspection, it will be caught, and errors will be set to True
            for column_prop in mapper.attrs:
                if isinstance(column_prop, RelationshipProperty):
                    if check_relationship_property(column_prop, schema, klass, engine):
                        errors = True
                elif isinstance(column_prop, ColumnProperty):
                    if check_column_property(column_prop, schema, klass, engine):
                        errors = True
                else:
                    logger.info(
                        "Encountered unexpected property %s in model %s with type %s",
                        column_prop.key,
                        klass.__name__,
                        type(column_prop),
                    )

        except SQLAlchemyError:
            logger.exception("Error inspecting model %s", klass.__name__)
            errors = True

    return not errors
