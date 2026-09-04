from squid.records.infrastructure import models
from squid.records.infrastructure.models import RecordRule, RecordSeries, RecordStanding


def test_record_vocabulary_preserves_physical_tables() -> None:
    assert RecordSeries.__tablename__ == "record_competitions"
    assert RecordRule.__tablename__ == "record_definitions"
    assert RecordStanding.__tablename__ == "record_results"


def test_record_vocabulary_removes_legacy_compatibility_names() -> None:
    assert not hasattr(models, "RecordCompetition")
    assert not hasattr(models, "RecordDefinition")
    assert not hasattr(models, "RecordResult")
