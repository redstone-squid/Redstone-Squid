"""Turn a projected resource into the rows that index it.

A SearchProjection lists every searchable fact about one resource as a field value,
plus what its hit displays. index_document derives everything stored from that list:
each text value's normalized form and term vector, the document's weighted vector
for bare text, its heading terms, and digests that let unchanged documents and
embeddings be left alone.

Terms come from the shared tokenizer, and the vectors are written as literals, so
PostgreSQL indexes exactly the terms that queries and the explainer look for.
"""

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from whenever import Instant

from squid.core.search.application.fields import DEFAULT_FIELD_REGISTRY, FieldRegistry
from squid.core.search.domain import FieldValue, FreeTextWeight
from squid.core.search.domain.text import normalize_text, terms

PROJECTION_FORMAT = 1
"""Bump when the stored form of a document changes, so every document is rewritten."""

_MAX_POSITION = 16383
_MAX_POSITIONS_PER_TERM = 256
"""PostgreSQL's limits on tsvector positions."""


@dataclass(frozen=True, slots=True)
class SearchProjection:
    """The complete replacement state of one search document."""

    resource_kind: str
    source_key: str
    title: str
    subtitle: str | None = None
    description: str | None = None
    status: str | None = None
    document_data: dict[str, object] = field(default_factory=dict)
    values: tuple[FieldValue, ...] = ()

    @property
    def embedded(self) -> bool:
        return self.resource_kind in {"build", "record"}


@dataclass(frozen=True, slots=True)
class IndexedValue:
    field: str
    ordinal: int
    holder_build_id: int | None
    text: str | None = None
    normalized: str | None = None
    term_vector: str | None = None
    numeric: Decimal | None = None
    timestamp: Instant | None = None
    boolean: bool | None = None


@dataclass(frozen=True, slots=True)
class IndexedDocument:
    """Everything stored for one projection."""

    projection: SearchProjection
    values: tuple[IndexedValue, ...]
    search_vector: str
    heading_terms: tuple[str, ...]
    normalized_title: str
    terms: frozenset[str]
    source_hash: str
    embedding_text: str
    embedding_hash: str


def index_document(projection: SearchProjection, registry: FieldRegistry = DEFAULT_FIELD_REGISTRY) -> IndexedDocument:
    """Derive the stored rows and vectors of a projection."""
    values: list[IndexedValue] = []
    ordinals: dict[str, int] = defaultdict(int)
    weighted: list[tuple[str, FreeTextWeight]] = []
    embedded: dict[str, None] = {}
    vocabulary: set[str] = set()
    for item in projection.values:
        ordinal = ordinals[item.field]
        ordinals[item.field] += 1
        value = item.value
        if isinstance(value, bool):
            values.append(IndexedValue(item.field, ordinal, item.holder_build_id, boolean=value))
        elif isinstance(value, Decimal):
            values.append(IndexedValue(item.field, ordinal, item.holder_build_id, numeric=value))
        elif isinstance(value, Instant):
            values.append(IndexedValue(item.field, ordinal, item.holder_build_id, timestamp=value))
        else:
            value_terms = terms(value)
            vocabulary.update(value_terms)
            values.append(
                IndexedValue(
                    item.field,
                    ordinal,
                    item.holder_build_id,
                    text=value,
                    normalized=" ".join(value_terms),
                    term_vector=tsvector_literal(((value_terms, None),)),
                )
            )
            definition = registry.stored(item.field)
            if definition is not None and definition.free_text is not None and value_terms:
                weighted.append((value, definition.free_text))
                if item.holder_build_id is None:
                    embedded[value] = None
    heading = " ".join(part for part in (projection.title, projection.subtitle) if part)
    embedding_text = "\n".join(embedded) if projection.embedded else ""
    return IndexedDocument(
        projection=projection,
        values=tuple(values),
        search_vector=tsvector_literal((terms(text), weight) for text, weight in weighted),
        heading_terms=tuple(sorted(set(terms(heading)))),
        normalized_title=normalize_text(heading),
        terms=frozenset(vocabulary),
        source_hash=_digest(_hashed(projection)),
        embedding_text=embedding_text,
        embedding_hash=_digest(embedding_text),
    )


def tsvector_literal(values: Iterable[tuple[Sequence[str], FreeTextWeight | None]]) -> str:
    """Write terms as a tsvector, one value after another.

    A one-position gap separates values, so a phrase never matches across two
    values. Terms are letters and digits only, so quoting needs no escapes.
    """
    positions: dict[str, list[str]] = defaultdict(list)
    cursor = 0
    for value_terms, weight in values:
        for offset, term in enumerate(value_terms, start=1):
            entries = positions[term]
            if len(entries) < _MAX_POSITIONS_PER_TERM:
                entries.append(f"{min(cursor + offset, _MAX_POSITION)}{weight or ''}")
        cursor += len(value_terms) + 1
    return " ".join(f"'{term}':{','.join(entries)}" for term, entries in positions.items())


def _hashed(projection: SearchProjection) -> dict[str, object]:
    return {
        "format": PROJECTION_FORMAT,
        "title": projection.title,
        "subtitle": projection.subtitle,
        "description": projection.description,
        "status": projection.status,
        "document_data": projection.document_data,
        "values": [(value.field, value.value, value.holder_build_id) for value in projection.values],
    }


def _digest(value: object) -> str:
    serialized = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()
