"""Exercise constructor defaults and writes that bypass the ORM."""

import pytest
from sqlalchemy import CheckConstraint, Integer, UniqueConstraint, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from whenever import Instant

from squid.permissions.models import PermissionRole
from squid.persistence.mixins import AuditTimestampsMixin, CreatedAtMixin
from tests.support.schema import model_table


class MixinTestBase(DeclarativeBase):
    pass


class MutableRecord(AuditTimestampsMixin, MixinTestBase):
    __tablename__ = "mutable_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    value: Mapped[int] = mapped_column(Integer)


class CreationRecord(CreatedAtMixin, MixinTestBase):
    __tablename__ = "creation_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)


def test_timestamps_are_available_before_flush_and_allow_imports() -> None:
    before = Instant.now()
    record = MutableRecord(id=1, value=1)
    after = Instant.now()
    assert before <= record.created_at <= after
    assert before <= record.updated_at <= after
    assert record != MutableRecord(id=1, value=1)
    assert hash(record) == object.__hash__(record)

    imported_at = Instant.from_utc(2020, 1, 1)
    imported = MutableRecord(id=2, value=1, created_at=imported_at, updated_at=imported_at)
    assert imported.created_at == imported.updated_at == imported_at
    assert isinstance(CreationRecord(id=1).created_at, Instant)
    assert "updated_at" not in CreationRecord.__table__.c


def test_database_defaults_and_explicit_update_ownership() -> None:
    engine = create_engine("sqlite://")
    MixinTestBase.metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO mutable_records (id, value) VALUES (1, 1)"))
            connection.execute(text("INSERT INTO creation_records (id) VALUES (1)"))
        with Session(engine) as session:
            record = session.get(MutableRecord, 1)
            created = session.get(CreationRecord, 1)
            assert record is not None
            assert created is not None
            assert isinstance(created.created_at, Instant)
            assert isinstance(record.created_at, Instant)
            assert isinstance(record.updated_at, Instant)
            original = record.updated_at
            record.value = 2
            session.commit()
            assert record.updated_at == original

            explicit = Instant.from_utc(2020, 1, 1)
            record.updated_at = explicit
            session.commit()
            assert record.updated_at == explicit
    finally:
        engine.dispose()


def test_role_slugs_remain_explicit_and_scoped_to_the_guild() -> None:
    with pytest.raises(TypeError, match="slug"):
        PermissionRole(name="Builders")  # type: ignore[missing-argument]

    role = PermissionRole(slug="builders", name="Builders")
    role.name = "Redstone builders"
    assert role.slug == "builders"
    assert isinstance(role.created_at, Instant)
    constraints = model_table(PermissionRole).constraints
    unique_scopes = {tuple(c.columns.keys()) for c in constraints if isinstance(c, UniqueConstraint)}
    assert ("guild_id", "slug") in unique_scopes
    assert ("slug",) not in unique_scopes
    slug_constraint = next(c for c in constraints if c.name == "permission_roles_guild_slug_key")
    assert slug_constraint.dialect_options["postgresql"]["nulls_not_distinct"] is True
    assert any(isinstance(c, CheckConstraint) and c.name == "permission_roles_slug_format" for c in constraints)
