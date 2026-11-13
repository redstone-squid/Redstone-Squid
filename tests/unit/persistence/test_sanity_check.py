"""Exercise schema inspection with temporary SQLite tables and SQLAlchemy mappings.

These tests check generic inspection behavior without a database server.
"""

import contextlib
from collections.abc import Generator

import pytest
import sqlalchemy
from sqlalchemy import (
    Column,
    Engine,
    ForeignKey,
    Integer,
    String,
    engine_from_config,
    text,
)
from sqlalchemy.exc import NoSuchTableError
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

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


def test_sanity_check_fails_with_column_type_mismatch(
    db_engine: Engine, base_and_sane_model: tuple[type[DeclarativeBase], type[DeclarativeBase]]
):
    Base, _ = base_and_sane_model
    with fresh_schema(db_engine, Base):
        assert is_sane_database(Base, db_engine) is True
        with db_engine.begin() as connection:
            connection.execute(text("DROP TABLE sanity_check_test_1"))
            connection.execute(
                text("CREATE TABLE sanity_check_test_1 (id INTEGER NOT NULL PRIMARY KEY, name INTEGER NOT NULL)")
            )
        assert is_sane_database(Base, db_engine) is False


def test_sanity_check_fails_with_missing_many_to_many_relationship(
    db_engine: Engine,
    base_and_sane_many_to_many_models: tuple[type[DeclarativeBase], type[DeclarativeBase], type[DeclarativeBase]],
):
    Base, _, _ = base_and_sane_many_to_many_models
    with fresh_schema(db_engine, Base):
        assert is_sane_database(Base, db_engine) is True
        Base.metadata.tables["many_to_many_association"].drop(db_engine)
        assert is_sane_database(Base, db_engine) is False


def test_sanity_check_fails_with_missing_relationship_target(
    db_engine: Engine,
    base_and_sane_relation_models: tuple[type[DeclarativeBase], type[DeclarativeBase], type[DeclarativeBase]],
    caplog: pytest.LogCaptureFixture,
):
    Base, target, _ = base_and_sane_relation_models
    with fresh_schema(db_engine, Base):
        assert is_sane_database(Base, db_engine) is True
        model_table(target).drop(db_engine)
        assert is_sane_database(Base, db_engine) is False
        assert "declares relationship test_relationship to table sanity_check_test_1" in caplog.text
