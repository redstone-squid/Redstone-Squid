"""Check whether the database has the tables and columns required by mapped models.

These checks compare SQLAlchemy's model metadata with database inspection results.
They can explain common mismatches, but do not replace migration revision checks
or Alembic's more complete schema comparison.
"""

import logging
from typing import Any, cast

from sqlalchemy import Engine, Inspector, Table, inspect, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import ColumnProperty, DeclarativeBase, RelationshipProperty

logger = logging.getLogger(__name__)


# Treat these database type spellings as equivalent during the consistency check.
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
    """Return the first type family whose alias occurs in a lower-case type name.

    Matching uses substrings in type_mapping order, not exact type identities.
    For example, "varchar(100)" becomes "string", but "bigint" also becomes
    "integer" because "int" matches first. The consistency check therefore cannot
    distinguish every database type. Names with no matching alias are unchanged.
    """
    for base_type, variants in type_mapping.items():
        if any(variant in type_name for variant in variants):
            return base_type
    return type_name


class DatabaseSchema:
    """Table names and column metadata read from the database."""

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
    """Log missing relationship tables and return whether any were missing.

    Skip targets and secondary expressions that are not concrete tables.
    """

    errors = False

    if column_prop.secondary is not None:
        if not isinstance(column_prop.secondary, Table):
            logger.info(
                "Skipping relationship %s in model %s because secondary is not a Table object", column_prop.key, klass
            )
            return errors

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
    """Log column mismatches and return whether any were found.

    Compare column presence, normalized type names, nullability, and the existence
    of foreign-key targets. Unique constraints are not checked.
    """
    errors = False

    for column in column_prop.columns:
        # Inherited columns can belong to a parent table, so use each column's table.
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

        # Check that the column exists before comparing its metadata.
        if column.key not in schema.columns[table]:
            logger.error(
                "Model %s declares column %s which does not exist in database %s",
                klass,
                column.key,
                engine.url,
            )
            errors = True
            continue

        # Compare type families using the approximate spelling normalization above.
        db_column = schema.columns[table][column.key]
        model_type = column.type
        db_type = db_column["type"]

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

        # Check foreign-key targets, not the database's foreign-key constraints.
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

        # Nullability must agree in both directions.
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
    """Return whether the database passes the mapped models' schema checks.

    For each model registered on base_cls, check:

    - Its table and mapped columns exist.
    - Column type names agree after normalize_type groups known spellings.
    - Model and database columns allow NULL in the same places.
    - Relationship target tables and many-to-many secondary tables exist.
    - Foreign keys point to existing tables and columns.

    Extra database tables and columns are allowed. Type comparison is approximate,
    and the checks do not establish that foreign-key constraints, unique constraints,
    or indexes match the models.

    Log mismatches and SQLAlchemy errors raised while checking mapper attributes.
    Connection and initial schema inspection errors propagate to the caller.

    Raises:
        TypeError: If `engine` is asynchronous.

    References:
        https://stackoverflow.com/questions/30428639/check-database-schema-matches-sqlalchemy-models-on-application-startup
    """
    if isinstance(engine, AsyncEngine):
        msg = "The engine must be a synchronous SQLAlchemy Engine, not an AsyncEngine."
        raise TypeError(msg)

    logger.debug("starting validation")
    inspector = inspect(engine)
    schema = DatabaseSchema(inspector)

    # Connection errors should reach the caller rather than become schema mismatches.
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))

    errors = False

    for mapper in base_cls.registry.mappers:
        klass = mapper.class_
        logger.debug("Checking model %s (%s)", klass.__name__, klass)
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

        try:
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
