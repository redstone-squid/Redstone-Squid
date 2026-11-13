from squid.records.models import RecordTitle, TitleDefinition, TitleResult


def test_record_vocabulary_preserves_physical_tables() -> None:
    assert RecordTitle.__tablename__ == "record_titles"
    assert TitleDefinition.__tablename__ == "title_definitions"
    assert TitleResult.__tablename__ == "title_results"
