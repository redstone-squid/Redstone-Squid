from squid.records import models
from squid.records.models import RecordTitle, TitleDefinition, TitleResult


def test_record_vocabulary_preserves_physical_tables() -> None:
    assert RecordTitle.__tablename__ == "record_competitions"
    assert TitleDefinition.__tablename__ == "record_definitions"
    assert TitleResult.__tablename__ == "record_results"


def test_record_vocabulary_removes_legacy_compatibility_names() -> None:
    assert not hasattr(models, "RecordCompetition")
    assert not hasattr(models, "RecordDefinition")
    assert not hasattr(models, "RecordResult")
