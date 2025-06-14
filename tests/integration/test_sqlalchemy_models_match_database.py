"""Tests for checking database sanity checks functions correctly."""
# noinspection SqlResolve
from collections.abc import Generator
from typing import cast

import pytest
from sqlalchemy import Engine, engine_from_config, Column, Integer, String, ForeignKey, text, Boolean, Float, BigInteger, SmallInteger, JSON, ARRAY, \
    Table
import sqlalchemy
from sqlalchemy.exc import NoSuchTableError
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import DeclarativeBase, Mapped, sessionmaker, relationship, declarative_base, Session
from sqlalchemy.sql.type_api import TypeEngine

from squid.db.schema import is_sane_database


@pytest.fixture
def base_and_sane_model() -> tuple[type[DeclarativeBase], type[DeclarativeBase]]:
    """Fixture providing a base class and a simple test model."""
    Base = declarative_base()

    class SaneTestModel(Base):
        """A sample SQLAlchemy model to demonstrate db conflicts."""
        __tablename__ = "sanity_check_test"
        id = Column(Integer, primary_key=True)
        name = Column(String(50), nullable=False)

    return Base, SaneTestModel


@pytest.fixture
def base_and_relation_models() -> tuple[type[DeclarativeBase], type[DeclarativeBase], type[DeclarativeBase]]:
    """Fixture providing base class and related test models."""
    Base = declarative_base()

    class RelationTestModel(Base):
        __tablename__ = "sanity_check_test_2"
        id: Mapped[int] = Column(Integer, primary_key=True)

    class RelationTestModel2(Base):
        __tablename__ = "sanity_check_test_3"
        id: Mapped[int] = Column(Integer, primary_key=True)
        test_relationship_id: Mapped[int] = Column(ForeignKey("sanity_check_test_2.id"))
        test_relationship = relationship(RelationTestModel, primaryjoin=test_relationship_id == RelationTestModel.id)

    return Base, RelationTestModel, RelationTestModel2


@pytest.fixture
def base_and_declarative_model() -> tuple[type[DeclarativeBase], type[DeclarativeBase]]:
    """Fixture providing base class and a model with declarative attributes."""
    Base = declarative_base()

    class DeclarativeTestModel(Base):
        __tablename__ = "sanity_check_test_4"
        id = Column(Integer, primary_key=True)

        @declared_attr
        def _password(self):
            return Column('password', String(256), nullable=False)

        @hybrid_property
        def password(self):
            return self._password

    return Base, DeclarativeTestModel


@pytest.fixture
def ini_settings() -> dict[str, str]:
    """Fixture providing a dictionary of ini settings."""
    return {
        'sqlalchemy.url': 'sqlite:///:memory:'
    }


@pytest.fixture
def db_engine(ini_settings: dict[str, str]) -> Generator[Engine, None, None]:
    """Fixture providing a database engine."""
    engine = engine_from_config(ini_settings, 'sqlalchemy.')
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine: Engine) -> Generator[Session, None, None]:
    """Fixture providing a database session."""
    Session = sessionmaker(bind=db_engine)
    session = Session()
    yield session
    session.close()


@pytest.mark.integration
def test_sanity_check_passes_with_valid_tables(db_engine: Engine, db_session: Session, base_and_sane_model: tuple[type[DeclarativeBase], type[DeclarativeBase]]):
    """Test that database sanity check passes when tables and columns are properly created."""
    Base, SaneTestModel = base_and_sane_model
    
    try:
        Base.metadata.drop_all(db_engine, tables=[cast(Table, SaneTestModel.__table__)])
    except sqlalchemy.exc.NoSuchTableError:
        pass

    Base.metadata.create_all(db_engine, tables=[cast(Table, SaneTestModel.__table__)])

    try:
        assert is_sane_database(Base, db_session) is True, "Database should be considered sane with valid tables"
    finally:
        Base.metadata.drop_all(db_engine)


@pytest.mark.integration
def test_sanity_check_fails_with_missing_table(db_engine: Engine, db_session: Session, base_and_sane_model: tuple[type[DeclarativeBase], type[DeclarativeBase]]):
    """Test that database sanity check fails when a required table is missing."""
    Base, SaneTestModel = base_and_sane_model
    
    try:
        Base.metadata.drop_all(db_engine, tables=[cast(Table, SaneTestModel.__table__)])
    except sqlalchemy.exc.NoSuchTableError:
        pass

    assert is_sane_database(Base, db_session) is False, "Database should not be considered sane with missing tables"


@pytest.mark.integration
def test_sanity_check_fails_with_missing_column(db_engine: Engine, db_session: Session, base_and_sane_model: tuple[type[DeclarativeBase], type[DeclarativeBase]]):
    """Test that database sanity check fails when a required column is missing."""
    Base, SaneTestModel = base_and_sane_model
    
    try:
        Base.metadata.drop_all(db_engine, tables=[cast(Table, SaneTestModel.__table__)])
    except sqlalchemy.exc.NoSuchTableError:
        pass
    
    Base.metadata.create_all(db_engine, tables=[cast(Table, SaneTestModel.__table__)])
    with db_engine.connect() as connection:
        connection.execute(text("ALTER TABLE sanity_check_test DROP COLUMN name"))

    assert is_sane_database(Base, db_session) is False, "Database should not be considered sane with missing columns"


@pytest.mark.integration
def test_sanity_check_passes_with_relationships(db_engine, db_session, base_and_relation_models):
    """Test that database sanity check correctly handles relationship tables."""
    Base, RelationTestModel, RelationTestModel2 = base_and_relation_models
    
    try:
        Base.metadata.drop_all(db_engine, tables=[cast(Table, RelationTestModel.__table__), (Table, RelationTestModel2.__table__)])
    except sqlalchemy.exc.NoSuchTableError:
        pass

    Base.metadata.create_all(db_engine, tables=[cast(Table, RelationTestModel.__table__), (Table, RelationTestModel2.__table__)])

    try:
        assert is_sane_database(Base, db_session) is True, "Database should be considered sane with valid relationships"
    finally:
        Base.metadata.drop_all(db_engine)


@pytest.mark.integration
def test_sanity_check_passes_with_declarative_attributes(db_engine: Engine, db_session: Session, base_and_declarative_model):
    """Test that database sanity check correctly handles models with declarative attributes."""
    Base, DeclarativeTestModel = base_and_declarative_model
    
    try:
        Base.metadata.drop_all(db_engine, tables=[cast(Table, DeclarativeTestModel.__table__)])
    except NoSuchTableError:
        pass

    Base.metadata.create_all(db_engine, tables=[cast(Table, DeclarativeTestModel.__table__)])

    try:
        assert is_sane_database(Base, db_session) is True, "Database should be considered sane with declarative attributes"
    finally:
        Base.metadata.drop_all(db_engine)


@pytest.fixture
def base_and_many_to_many_models():
    """Fixture providing base class and many-to-many related test models."""
    Base = declarative_base()

    class ManyToManyModel1(Base):
        __tablename__ = "many_to_many_test_1"
        id = Column(Integer, primary_key=True)
        name = Column(String(50), nullable=False)

    class ManyToManyModel2(Base):
        __tablename__ = "many_to_many_test_2"
        id = Column(Integer, primary_key=True)
        name = Column(String(50), nullable=False)

    # Association table
    association_table = sqlalchemy.Table(
        'many_to_many_association',
        Base.metadata,
        Column('model1_id', Integer, ForeignKey('many_to_many_test_1.id')),
        Column('model2_id', Integer, ForeignKey('many_to_many_test_2.id'))
    )

    # Add relationships
    ManyToManyModel1.model2s = relationship(
        "ManyToManyModel2",
        secondary=association_table,
        back_populates="model1s"
    )
    ManyToManyModel2.model1s = relationship(
        "ManyToManyModel1",
        secondary=association_table,
        back_populates="model2s"
    )

    return Base, ManyToManyModel1, ManyToManyModel2


def alter_table_sqlite(table_name: str, column_name: str, new_type: str) -> list[str]:
    """Generate SQLite ALTER TABLE statement to change column type."""
    return [
        f"ALTER TABLE {table_name} RENAME TO {table_name}_old",
        f"CREATE TABLE {table_name} (id INTEGER PRIMARY KEY, {column_name} {new_type})",
        f"INSERT INTO {table_name} (id, {column_name}) SELECT id, {column_name} FROM {table_name}_old",
        f"DROP TABLE {table_name}_old",
    ]


@pytest.mark.parametrize("column_type,db_type", [
    (String(50), "VARCHAR(50)"),
    (Integer, "INTEGER"),
    (Boolean, "BOOLEAN"),
    (Float, "FLOAT"),
    (BigInteger, "BIGINT"),
    (SmallInteger, "SMALLINT"),
    (JSON, "JSON"),
    (ARRAY(Integer), "INTEGER[]"),
])
def test_sanity_check_fails_with_column_type_mismatch(db_engine: Engine, db_session: Session, base_and_sane_model: tuple[type[DeclarativeBase], type[DeclarativeBase]], column_type: TypeEngine, db_type: str):
    """Test that database sanity check fails when a column type doesn't match the model."""
    Base, SaneTestModel = base_and_sane_model
    
    # Create a new model with the specified column type
    class TestModel(Base):
        __tablename__ = "sanity_check_test_mismatch_column"
        id = Column(Integer, primary_key=True)
        test_column = Column(column_type, nullable=False)
    
    try:
        Base.metadata.drop_all(db_engine)
    except sqlalchemy.exc.NoSuchTableError:
        pass
    
    Base.metadata.create_all(db_engine, tables=[cast(Table, TestModel.__table__)])
    # Change the type of the test_column to a different type
    with db_engine.begin() as connection:
        if db_engine.name == 'sqlite':
            # SQLite does not support ALTER COLUMN, so we need to recreate the table
            alter_sql = alter_table_sqlite(TestModel.__tablename__, 'test_column', db_type)
            for sql in alter_sql:
                connection.execute(text(sql))
        else:
            # For other databases, we can use ALTER TABLE directly
            connection.execute(text(f"ALTER TABLE {TestModel.__tablename__} ALTER COLUMN test_column TYPE {db_type}"))

    assert is_sane_database(Base, db_session) is False, f"Database should not be considered sane with mismatched column types: {column_type} vs {db_type}"


@pytest.mark.integration
def test_sanity_check_fails_with_missing_many_to_many_relationship(db_engine: Engine, db_session: Session, base_and_many_to_many_models: tuple[type[DeclarativeBase], type[DeclarativeBase], type[DeclarativeBase]]):
    """Test that database sanity check fails when a many-to-many relationship is missing from the model."""
    Base, ManyToManyModel1, ManyToManyModel2 = base_and_many_to_many_models
    
    try:
        Base.metadata.drop_all(db_engine)
    except sqlalchemy.exc.NoSuchTableError:
        pass
    
    # Create all tables including the association table
    Base.metadata.create_all(db_engine)
    
    # Create a new model without the many-to-many relationship
    class MissingRelationshipModel(Base):
        __tablename__ = "many_to_many_test_1"
        id = Column(Integer, primary_key=True)
        name = Column(String(50), nullable=False)
        # Intentionally missing the model2s relationship
    
    assert is_sane_database(Base, db_session) is False, "Database should not be considered sane with missing many-to-many relationship"


@pytest.mark.integration
def test_sanity_check_fails_with_missing_one_to_many_relationship(db_engine: Engine, db_session: Session, base_and_relation_models: tuple[type[DeclarativeBase], type[DeclarativeBase], type[DeclarativeBase]]):
    """Test that database sanity check fails when a one-to-many relationship is missing from the model."""
    Base, RelationTestModel, RelationTestModel2 = base_and_relation_models
    
    try:
        Base.metadata.drop_all(db_engine)
    except sqlalchemy.exc.NoSuchTableError:
        pass
    
    # Create all tables
    Base.metadata.create_all(db_engine)
    
    # Create a new model without the one-to-many relationship
    class MissingRelationshipModel(Base):
        __tablename__ = "sanity_check_test_2"
        id = Column(Integer, primary_key=True)
        test_relationship_id = Column(ForeignKey("sanity_check_test_3.id"))
        # Intentionally missing the test_relationship relationship
    
    assert is_sane_database(Base, db_session) is False, "Database should not be considered sane with missing one-to-many relationship"
