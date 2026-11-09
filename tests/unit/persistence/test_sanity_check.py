"""Exercise schema inspection with temporary SQLite tables and SQLAlchemy mappings.

These tests check generic inspection behavior without a database server. The ARRAY
case requires PostgreSQL and is skipped with this SQLite fixture.
"""

import contextlib
from collections.abc import Generator
from typing import Any

import pytest
import sqlalchemy
from sqlalchemy import (
    ARRAY,
    JSON,
    BigInteger,
    Boolean,
    Column,
    Engine,
    Float,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    engine_from_config,
    text,
)
from sqlalchemy.exc import NoSuchTableError
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker
from sqlalchemy.sql.type_api import TypeEngine

from squid.persistence.inspection import is_sane_database
from tests.support.schema import model_table


@pytest.fixture
def base_and_sane_model() -> tuple[type[DeclarativeBase], type[DeclarativeBase]]:
    """Create an isolated registry containing one simple mapped table."""

    class Base(DeclarativeBase):
        pass

    class SaneTestModel(Base):
        __tablename__ = "sanity_check_test_1"
        id: Mapped[int] = mapped_column(Integer, primary_key=True)
        name: Mapped[str] = mapped_column(String(50), nullable=False)

    return Base, SaneTestModel


@pytest.fixture
def base_and_sane_relation_models() -> tuple[type[DeclarativeBase], type[DeclarativeBase], type[DeclarativeBase]]:
    """Create two tables joined by a foreign key and an ORM relationship."""

    class Base(DeclarativeBase):
        pass

    class RelationTestModel(Base):
        __tablename__ = "sanity_check_test_1"
        id: Mapped[int] = mapped_column(Integer, primary_key=True)

    class RelationTestModel2(Base):
        __tablename__ = "sanity_check_test_2"
        id: Mapped[int] = mapped_column(Integer, primary_key=True)
        test_relationship_id: Mapped[int] = mapped_column(ForeignKey("sanity_check_test_1.id"))
        test_relationship: Mapped[RelationTestModel] = relationship(
            RelationTestModel, primaryjoin=test_relationship_id == RelationTestModel.id
        )

    return Base, RelationTestModel, RelationTestModel2


@pytest.fixture
def base_and_sane_declarative_model() -> tuple[type[DeclarativeBase], type[DeclarativeBase]]:
    """Create a model whose declared column is exposed through a hybrid property."""

    class Base(DeclarativeBase):
        pass

    class DeclarativeTestModel(Base):
        __tablename__ = "sanity_check_test_1"
        id: Mapped[int] = mapped_column(Integer, primary_key=True)

        @declared_attr
        def _password(self):
            return Column("password", String(256), nullable=False)

        @hybrid_property
        def password(self):
            return self._password

    return Base, DeclarativeTestModel


@pytest.fixture
def base_and_sane_many_to_many_models():
    """Create two mapped tables connected through an association table."""

    class Base(DeclarativeBase):
        pass

    class ManyToManyModel1(Base):
        __tablename__ = "many_to_many_test_1"
        id: Mapped[int] = mapped_column(Integer, primary_key=True)
        name: Mapped[str] = mapped_column(String(50), nullable=False)
        model2s: Mapped[list[ManyToManyModel2]] = relationship(
            "ManyToManyModel2", secondary="many_to_many_association", back_populates="model1s"
        )

    class ManyToManyModel2(Base):
        __tablename__ = "many_to_many_test_2"
        id: Mapped[int] = mapped_column(Integer, primary_key=True)
        name: Mapped[str] = mapped_column(String(50), nullable=False)
        model1s: Mapped[list[ManyToManyModel1]] = relationship(
            "ManyToManyModel1", secondary="many_to_many_association", back_populates="model2s"
        )

    association_table = sqlalchemy.Table(
        "many_to_many_association",
        Base.metadata,
        Column("model1_id", Integer, ForeignKey("many_to_many_test_1.id")),
        Column("model2_id", Integer, ForeignKey("many_to_many_test_2.id")),
    )

    return Base, ManyToManyModel1, ManyToManyModel2


@pytest.fixture
def ini_settings() -> dict[str, str]:
    return {"sqlalchemy.url": "sqlite:///:memory:"}


@pytest.fixture
def db_engine(ini_settings: dict[str, str]) -> Generator[Engine]:
    """Yield an in-memory SQLite engine and dispose it afterward."""
    engine = engine_from_config(ini_settings, "sqlalchemy.")
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine: Engine) -> Generator[Session]:
    """Yield a session and close it after the test."""
    Session = sessionmaker(bind=db_engine)
    session = Session()
    yield session
    session.close()


@contextlib.contextmanager
def fresh_schema(engine: Engine, base: type[DeclarativeBase]) -> Generator[None]:
    """Create all tables for `base` on `engine`, dropping any pre-existing ones first and after."""
    with contextlib.suppress(NoSuchTableError):
        base.metadata.drop_all(engine)

    base.metadata.create_all(engine)

    try:
        yield
    finally:
        base.metadata.drop_all(engine)


def test_sanity_check_passes_with_valid_tables(
    db_engine: Engine, base_and_sane_model: tuple[type[DeclarativeBase], type[DeclarativeBase]]
):
    Base, SaneTestModel = base_and_sane_model

    with fresh_schema(db_engine, Base):
        assert is_sane_database(Base, db_engine) is True, "Database should be considered sane with valid tables"


def test_sanity_check_fails_with_missing_table(
    db_engine: Engine, base_and_sane_model: tuple[type[DeclarativeBase], type[DeclarativeBase]]
):
    Base, SaneTestModel = base_and_sane_model

    with contextlib.suppress(NoSuchTableError):
        Base.metadata.drop_all(db_engine)

    assert is_sane_database(Base, db_engine) is False, "Database should not be considered sane with missing tables"


def test_sanity_check_fails_with_missing_column(
    db_engine: Engine, base_and_sane_model: tuple[type[DeclarativeBase], type[DeclarativeBase]]
):
    Base, SaneTestModel = base_and_sane_model

    with fresh_schema(db_engine, Base):
        with db_engine.connect() as connection:
            connection.execute(text("ALTER TABLE sanity_check_test_1 DROP COLUMN name"))

        assert is_sane_database(Base, db_engine) is False, "Database should not be considered sane with missing columns"


def test_sanity_check_passes_with_relationships(
    db_engine: Engine,
    base_and_sane_relation_models: tuple[type[DeclarativeBase], type[DeclarativeBase], type[DeclarativeBase]],
):
    Base, RelationTestModel, RelationTestModel2 = base_and_sane_relation_models

    with fresh_schema(db_engine, Base):
        assert is_sane_database(Base, db_engine) is True, "Database should be considered sane with valid relationships"


def test_sanity_check_passes_with_declarative_attributes(
    db_engine: Engine, base_and_sane_declarative_model: tuple[type[DeclarativeBase], type[DeclarativeBase]]
):
    Base, DeclarativeTestModel = base_and_sane_declarative_model

    with fresh_schema(db_engine, Base):
        assert is_sane_database(Base, db_engine) is True, (
            "Database should be considered sane with declarative attributes"
        )


def alter_table_sqlite(table_name: str, column_name: str, new_type: str) -> list[str]:
    """Rebuild the test table with id and one column of the requested type, preserving their values."""
    return [
        f"ALTER TABLE {table_name} RENAME TO {table_name}_old",
        f"CREATE TABLE {table_name} (id INTEGER PRIMARY KEY, {column_name} {new_type})",
        f"INSERT INTO {table_name} (id, {column_name}) SELECT id, {column_name} FROM {table_name}_old",
        f"DROP TABLE {table_name}_old",
    ]


@pytest.mark.parametrize(
    ("supposed_column_type", "db_type"),
    [
        (String(50), "VARCHAR(50)"),
        (Integer, "INTEGER"),
        (Boolean, "BOOLEAN"),
        (Float, "FLOAT"),
        (BigInteger, "BIGINT"),
        (SmallInteger, "SMALLINT"),
        (JSON, "JSON"),
        (ARRAY(Integer), "INTEGER[]"),
    ],
    ids=[  # Stable labels for both type classes and configured type instances.
        "String(50)",
        "Integer",
        "Boolean",
        "Float",
        "BigInteger",
        "SmallInteger",
        "JSON",
        "ARRAY(Integer)",
    ],
)
def test_sanity_check_fails_with_column_type_mismatch(
    db_engine: Engine,
    base_and_sane_model: tuple[type[DeclarativeBase], type[DeclarativeBase]],
    supposed_column_type: TypeEngine,
    db_type: str,
):
    if db_engine.name != "postgresql" and isinstance(supposed_column_type, ARRAY):
        pytest.skip("ARRAY type is only supported in PostgreSQL")

    Base, SaneTestModel = base_and_sane_model

    incorrect_column_type = Integer if db_type != "Integer" else Boolean

    class TestModel(Base):
        __tablename__ = "sanity_check_test_mismatch_column"
        id: Mapped[int] = mapped_column(Integer, primary_key=True)
        test_column: Mapped[Any] = mapped_column(incorrect_column_type, nullable=False)

    with contextlib.suppress(NoSuchTableError):
        Base.metadata.drop_all(db_engine)

    Base.metadata.create_all(db_engine, tables=[model_table(TestModel)])
    # Rebuild only this table; the original fixture model remains absent, so the
    # final failure does not isolate column-type checking.
    with db_engine.begin() as connection:
        if db_engine.name == "sqlite":
            # SQLite does not support ALTER COLUMN, so we need to recreate the table
            alter_sql = alter_table_sqlite(TestModel.__tablename__, "test_column", db_type)
            for sql in alter_sql:
                connection.execute(text(sql))
        else:
            connection.execute(text(f"ALTER TABLE {TestModel.__tablename__} ALTER COLUMN test_column TYPE {db_type}"))

    assert is_sane_database(Base, db_engine) is False, (
        f"Database should not be considered sane with mismatched column types: {incorrect_column_type} vs {db_type}"
    )


def test_sanity_check_fails_with_missing_many_to_many_relationship(
    db_engine: Engine,
    base_and_sane_many_to_many_models: tuple[type[DeclarativeBase], type[DeclarativeBase], type[DeclarativeBase]],
):
    Base, ManyToManyModel1, ManyToManyModel2 = base_and_sane_many_to_many_models

    Base.metadata.drop_all(db_engine)
    Base.metadata.create_all(db_engine)

    # Re-create ManyToManyModel1 without the relationship to ManyToManyModel2
    Base.metadata.remove(model_table(ManyToManyModel1))

    class ManyToManyModel1_MissingRelationship(Base):
        __tablename__ = "many_to_many_test_1"
        id = Column(Integer, primary_key=True)
        name = Column(String(50), nullable=False)
        # Intentionally missing the model2s relationship

    assert is_sane_database(Base, db_engine) is False, (
        "Database should not be considered sane with missing many-to-many relationship"
    )


def test_sanity_check_fails_with_missing_one_to_many_relationship(
    db_engine: Engine,
    base_and_sane_relation_models: tuple[type[DeclarativeBase], type[DeclarativeBase], type[DeclarativeBase]],
):
    Base, ManyToManyModel1, ManyToManyModel2 = base_and_sane_relation_models

    Base.metadata.drop_all(db_engine)
    Base.metadata.create_all(db_engine)

    # Create a new model without the one-to-many relationship
    Base.metadata.remove(model_table(ManyToManyModel1))

    class ManyToManyModel1_MissingRelationship(Base):
        __tablename__ = ManyToManyModel1.__tablename__

        id: Mapped[int] = mapped_column(Integer, primary_key=True)
        test_relationship_id: Mapped[int] = mapped_column(ForeignKey("sanity_check_test_2.id"))
        # Intentionally missing the test_relationship relationship

    assert is_sane_database(Base, db_engine) is False, (
        "Database should not be considered sane with missing one-to-many relationship"
    )
