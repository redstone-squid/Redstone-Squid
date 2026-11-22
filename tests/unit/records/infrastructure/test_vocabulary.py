from squid.core.records.models import Record, RecordDefinition, RecordResult


def test_record_vocabulary_preserves_physical_tables() -> None:
    assert Record.__tablename__ == "record_titles"
    assert RecordDefinition.__tablename__ == "title_definitions"
    assert RecordResult.__tablename__ == "title_results"
