"""Tests for checking database sanity checks functions correctly."""

import pytest
from sqlalchemy import Engine, engine_from_config, Column, Integer, String, ForeignKey, text
import sqlalchemy
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import sessionmaker, relationship, declarative_base

from squid.db.schema import is_sane_database


@pytest.fixture
def base_and_sane_model():
    """Fixture providing a base class and a simple test model."""
    Base = declarative_base()

    class SaneTestModel(Base):
        """A sample SQLAlchemy model to demonstrate db conflicts."""
        __tablename__ = "sanity_check_test"
        id = Column(Integer, primary_key=True)
        name = Column(String(50), nullable=False)

    return Base, SaneTestModel


@pytest.fixture
def base_and_relation_models():
    """Fixture providing base class and related test models."""
    Base = declarative_base()

    class RelationTestModel(Base):
        __tablename__ = "sanity_check_test_2"
        id = Column(Integer, primary_key=True)

    class RelationTestModel2(Base):
        __tablename__ = "sanity_check_test_3"
        id = Column(Integer, primary_key=True)
        test_relationship_id = Column(ForeignKey("sanity_check_test_2.id"))
        test_relationship = relationship(RelationTestModel, primaryjoin=test_relationship_id == RelationTestModel.id)

    return Base, RelationTestModel, RelationTestModel2


@pytest.fixture
def base_and_declarative_model():
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
def ini_settings():
    """Fixture providing a dictionary of ini settings."""
    return {
        'sqlalchemy.url': 'sqlite:///:memory:'
    }


@pytest.fixture
def db_engine(ini_settings):
    """Fixture providing a database engine."""
    engine = engine_from_config(ini_settings, 'sqlalchemy.')
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine):
    """Fixture providing a database session."""
    Session = sessionmaker(bind=db_engine)
    session = Session()
    yield session
    session.close()


@pytest.mark.integration
def test_sanity_check_passes_with_valid_tables(db_engine, db_session, base_and_sane_model):
    """Test that database sanity check passes when tables and columns are properly created."""
    Base, SaneTestModel = base_and_sane_model
    
    try:
        Base.metadata.drop_all(db_engine, tables=[SaneTestModel.__table__])
    except sqlalchemy.exc.NoSuchTableError:
        pass

    Base.metadata.create_all(db_engine, tables=[SaneTestModel.__table__])

    try:
        assert is_sane_database(Base, db_session) is True, "Database should be considered sane with valid tables"
    finally:
        Base.metadata.drop_all(db_engine)


@pytest.mark.integration
def test_sanity_check_fails_with_missing_table(db_engine, db_session, base_and_sane_model):
    """Test that database sanity check fails when a required table is missing."""
    Base, SaneTestModel = base_and_sane_model
    
    try:
        Base.metadata.drop_all(db_engine, tables=[SaneTestModel.__table__])
    except sqlalchemy.exc.NoSuchTableError:
        pass

    assert is_sane_database(Base, db_session) is False, "Database should not be considered sane with missing tables"


@pytest.mark.integration
def test_sanity_check_fails_with_missing_column(db_engine: Engine, db_session, base_and_sane_model):
    """Test that database sanity check fails when a required column is missing."""
    Base, SaneTestModel = base_and_sane_model
    
    try:
        Base.metadata.drop_all(db_engine, tables=[SaneTestModel.__table__])
    except sqlalchemy.exc.NoSuchTableError:
        pass
    
    Base.metadata.create_all(db_engine, tables=[SaneTestModel.__table__])
    with db_engine.connect() as connection:
        connection.execute(text("ALTER TABLE sanity_check_test DROP COLUMN name"))

    assert is_sane_database(Base, db_session) is False, "Database should not be considered sane with missing columns"


@pytest.mark.integration
def test_sanity_check_passes_with_relationships(db_engine, db_session, base_and_relation_models):
    """Test that database sanity check correctly handles relationship tables."""
    Base, RelationTestModel, RelationTestModel2 = base_and_relation_models
    
    try:
        Base.metadata.drop_all(db_engine, tables=[RelationTestModel.__table__, RelationTestModel2.__table__])
    except sqlalchemy.exc.NoSuchTableError:
        pass

    Base.metadata.create_all(db_engine, tables=[RelationTestModel.__table__, RelationTestModel2.__table__])

    try:
        assert is_sane_database(Base, db_session) is True, "Database should be considered sane with valid relationships"
    finally:
        Base.metadata.drop_all(db_engine)


@pytest.mark.integration
def test_sanity_check_passes_with_declarative_attributes(db_engine, db_session, base_and_declarative_model):
    """Test that database sanity check correctly handles models with declarative attributes."""
    Base, DeclarativeTestModel = base_and_declarative_model
    
    try:
        Base.metadata.drop_all(db_engine, tables=[DeclarativeTestModel.__table__])
    except sqlalchemy.exc.NoSuchTableError:
        pass

    Base.metadata.create_all(db_engine, tables=[DeclarativeTestModel.__table__])

    try:
        assert is_sane_database(Base, db_session) is True, "Database should be considered sane with declarative attributes"
    finally:
        Base.metadata.drop_all(db_engine)
