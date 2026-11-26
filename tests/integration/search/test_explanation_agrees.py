"""The explainer and the SQL predicate agree on which documents match.

Search filters in SQL and explains hits in Python. If the two disagreed, a hit
could appear without a reason, or a reason could be shown for a document that
does not match. Hypothesis generates queries over a small vocabulary against one
seeded corpus of builds and records, including close spellings, phrases,
keywords, negation, and holder measurements.
"""

from collections import defaultdict
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.core.search.application import SearchQueryParser
from squid.core.search.application.explanation import explain
from squid.core.search.domain import FieldValue
from squid.core.search.infrastructure.compiler import compile_query
from squid.core.search.infrastructure.documents import SearchProjection
from squid.core.search.infrastructure.projection import SearchProjectionRepository
from squid.core.search.infrastructure.repository import PostgresSearchBackend
from squid.core.search.models import SearchDocument, SearchValue

WORDS = ("glass", "glas", "door", "doors", "piston", "slime", "2x2", "3x3", "no", "entities")
RESTRICTIONS = ("No Entities", "Entityless", "No Slime")

_CORPUS: tuple[SearchProjection, ...] = (
    SearchProjection("build", "1", "2x2 Glass Door", values=(FieldValue("title", "2x2 Glass Door"),)),
    SearchProjection(
        "build",
        "2",
        "3x3 Piston Doors",
        values=(
            FieldValue("title", "3x3 Piston Doors"),
            FieldValue("restriction", "No Slime"),
            FieldValue("volume", Decimal(27)),
        ),
    ),
    SearchProjection(
        "build",
        "3",
        "Glas Slime Door",
        values=(FieldValue("title", "Glas Slime Door"), FieldValue("description", "a door made of glass")),
    ),
    SearchProjection(
        "record",
        "record:a",
        "Fastest 2x2 Glass Door",
        values=(
            FieldValue("title", "Fastest 2x2 Glass Door"),
            FieldValue("subtitle", "No Entities"),
            FieldValue("restriction", "No Entities"),
            FieldValue("restriction", "Entityless"),
            FieldValue("holder", "1", 1),
            FieldValue("holder_title", "2x2 Glass Door", 1),
            FieldValue("volume", Decimal(12), 1),
            FieldValue("holder", "3", 3),
            FieldValue("holder_title", "Glas Slime Door", 3),
        ),
    ),
    SearchProjection(
        "record",
        "record:b",
        "Smallest 3x3 Piston Door",
        values=(
            FieldValue("title", "Smallest 3x3 Piston Door"),
            FieldValue("holder", "2", 2),
            FieldValue("holder_title", "3x3 Piston Doors", 2),
            FieldValue("volume", Decimal(27), 2),
        ),
    ),
)


def _atom() -> st.SearchStrategy[str]:
    word = st.sampled_from(WORDS)
    return st.one_of(
        word,
        st.tuples(word, word).map(lambda pair: f'"{pair[0]} {pair[1]}"'),
        word.map(lambda value: f"title:{value}"),
        st.tuples(word, word).map(lambda pair: f'title:"{pair[0]} {pair[1]}"'),
        st.sampled_from(RESTRICTIONS).map(lambda value: f'restriction:"{value}"'),
        st.sampled_from(("volume<20", "volume>=20", "volume:[10 TO 30]", "volume IS SET", "volume IS NOT SET")),
    )


def _query() -> st.SearchStrategy[str]:
    return st.recursive(
        _atom(),
        lambda inner: st.one_of(
            inner.map(lambda value: f"NOT ({value})"),
            st.tuples(inner, inner).map(lambda pair: f"({pair[0]}) OR ({pair[1]})"),
            st.tuples(inner, inner).map(lambda pair: f"({pair[0]}) ({pair[1]})"),
        ),
        max_leaves=4,
    )


@pytest.fixture
async def corpus(migrated_session_factory: async_sessionmaker[AsyncSession]) -> async_sessionmaker[AsyncSession]:
    async with migrated_session_factory.begin() as session:
        store = SearchProjectionRepository(session)
        for projection in _CORPUS:
            await store.replace(projection)
    return migrated_session_factory


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(source=_query())
async def test_explainer_agrees_with_the_sql_predicate(corpus: async_sessionmaker[AsyncSession], source: str) -> None:
    query = SearchQueryParser().parse(source)
    async with corpus() as session:
        expansion = await PostgresSearchBackend(lambda: session)._expansion(session, query)  # pyright: ignore[reportPrivateUsage]
        matched = set(
            (await session.scalars(select(SearchDocument.source_key).where(compile_query(query, expansion)))).all()
        )
        rows = await session.execute(
            select(SearchDocument.source_key, SearchDocument.resource_kind, SearchValue).join(
                SearchValue, SearchValue.document_id == SearchDocument.id
            )
        )
    values: dict[str, list[FieldValue]] = defaultdict(list)
    kinds: dict[str, str] = {}
    for key, kind, value in rows.tuples():
        kinds[key] = kind
        stored = next(
            item
            for item in (value.text_value, value.numeric_value, value.timestamp_value, value.boolean_value)
            if item is not None
        )
        values[key].append(FieldValue(value.field_name, stored, value.holder_build_id))
    explained = {
        key
        for key, document in values.items()
        if explain(query, document, record=kinds[key] == "record", expansion=expansion) is not None
    }

    assert explained == matched, source
