"""PostgreSQL search predicate compiler tests."""

from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker

from squid.search.application import SearchQueryParser
from squid.search.domain import SearchRequest, SearchScope
from squid.search.infrastructure.compiler import PostgresSearchQueryCompiler
from squid.search.infrastructure.repository import PostgresSearchBackend


def _compile(query: str) -> tuple[str, dict[str, object]]:
    expression = PostgresSearchQueryCompiler().compile(SearchQueryParser().parse(query))
    compiled = expression.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


def test_compiler_preserves_full_boolean_semantics_with_bound_values() -> None:
    sql, parameters = _compile('door OR (status:confirmed NOT title:"slow door")')

    assert "search_documents.combined_vector @@ plainto_tsquery" in sql
    assert "search_documents.status" in sql
    assert "NOT" in sql
    assert "search_documents.title_vector @@ phraseto_tsquery" in sql
    assert {"door", "confirmed", "slow door"}.issubset(set(parameters.values()))
    assert "slow door" not in sql


def test_compiler_uses_typed_facet_exists_for_numeric_range() -> None:
    sql, parameters = _compile("volume:[10 TO 20]")

    assert "EXISTS" in sql
    assert "search_document_facets.numeric_value BETWEEN" in sql
    assert "search_document_facets.field_name" in sql
    assert "volume" in parameters.values()
    assert "10" in {str(value) for value in parameters.values()}
    assert "20" in {str(value) for value in parameters.values()}


def test_compiler_uses_typed_facet_exists_for_timestamp_comparison() -> None:
    sql, parameters = _compile("completion_at>=2026-07-30")

    assert "search_document_facets.timestamp_value >=" in sql
    assert "completion_at" in parameters.values()


def test_compiler_maps_kind_and_restriction_to_facets_without_dynamic_identifiers() -> None:
    sql, parameters = _compile("kind:door restriction:slim")
    flattened = {
        item for value in parameters.values() for item in (value if isinstance(value, list | tuple) else (value,))
    }

    assert sql.count("EXISTS") == 2
    assert "search_document_facets.text_value" in sql
    assert {"kind", "door", "restriction", "slim"}.issubset(flattened)


def test_compiler_allows_trigram_candidates_to_recover_typo_text() -> None:
    sql, parameters = _compile("dorr")

    assert "search_documents.combined_vector @@ plainto_tsquery" in sql
    assert "similarity(search_documents.fuzzy_text" in sql
    assert "search_documents.normalized_title" in sql
    assert " OR " in sql
    assert "dorr" in parameters.values()


def test_title_field_allows_trigram_but_description_remains_fts_only() -> None:
    title_sql, _ = _compile("title:dorr")
    description_sql, _ = _compile("description:dorr")

    assert "similarity(search_documents.normalized_title" in title_sql
    assert "similarity" not in description_sql


def test_compiler_treats_injection_text_as_a_bound_value() -> None:
    value = "door'); DROP TABLE builds; --"
    sql, parameters = _compile(f'title:"{value}"')

    assert "DROP TABLE" not in sql
    assert value in parameters.values()


def test_backend_predicate_defaults_builds_to_confirmed() -> None:
    parser = SearchQueryParser()
    backend = PostgresSearchBackend(async_sessionmaker())
    request = SearchRequest("door", scope=SearchScope.BUILDS)

    compiled = backend.compile_predicate(request, parser.parse(request.query)).compile(dialect=postgresql.dialect())

    assert "search_documents.resource_kind" in str(compiled)
    assert "search_documents.status" in str(compiled)
    assert ["confirmed"] in compiled.params.values()


def test_explicit_status_cannot_override_confirmed_visibility_default() -> None:
    parser = SearchQueryParser()
    backend = PostgresSearchBackend(async_sessionmaker())
    request = SearchRequest("status:denied", scope=SearchScope.BUILDS)

    compiled = backend.compile_predicate(request, parser.parse(request.query)).compile(dialect=postgresql.dialect())

    assert "denied" in compiled.params.values()
    assert ["confirmed"] in compiled.params.values()


def test_explicit_visibility_policy_is_applied_independently_of_query() -> None:
    parser = SearchQueryParser()
    backend = PostgresSearchBackend(async_sessionmaker())
    request = SearchRequest(
        "status:denied",
        scope=SearchScope.BUILDS,
        visible_statuses=frozenset({"pending", "confirmed"}),
    )

    compiled = backend.compile_predicate(request, parser.parse(request.query)).compile(dialect=postgresql.dialect())

    parameters = compiled.params.values()
    assert "denied" in parameters
    assert any(set(value) == {"pending", "confirmed"} for value in parameters if isinstance(value, list))
