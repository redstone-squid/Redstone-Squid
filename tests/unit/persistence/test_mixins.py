"""Exercise constructor defaults and writes that bypass the ORM."""

from sqlalchemy import Integer, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from whenever import Instant

from squid.persistence.mixins import AuditTimestampsMixin, CreatedAtMixin


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
