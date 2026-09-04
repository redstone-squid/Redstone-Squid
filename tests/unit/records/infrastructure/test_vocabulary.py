from squid.records.infrastructure.models import (
    RecordCompetition,
    RecordDefinition,
    RecordResult,
    RecordRule,
    RecordSeries,
    RecordStanding,
)


def test_record_vocabulary_preserves_physical_tables() -> None:
    assert RecordSeries.__tablename__ == "record_competitions"
    assert RecordRule.__tablename__ == "record_definitions"
    assert RecordStanding.__tablename__ == "record_results"


def test_record_vocabulary_retains_temporary_compatibility_names() -> None:
    assert RecordCompetition is RecordSeries
    assert RecordDefinition is RecordRule
    assert RecordResult is RecordStanding
