"""Reading an indexed document back as the hit the projection wrote."""

import json

from squid.search.domain import RecordSearchHit
from squid.search.infrastructure.models import SearchDocument
from squid.search.infrastructure.projection import (
    RecordHolderProjectionSource,
    RecordProjectionSource,
    SearchProjection,
    build_record_projection,
    normalize_search_text,
    projection_source_hash,
)
from squid.search.infrastructure.repository import _to_hit


def _indexed(projection: SearchProjection) -> SearchDocument:
    """Build the row `SearchProjectionStore.replace` writes, JSONB round-trip included.

    The round trip is the point: `document_data` reaches a reader as JSON, so a projected tuple
    comes back as a list.
    """
    normalized_title = normalize_search_text(projection.title)
    return SearchDocument(
        resource_kind=projection.resource_kind,
        source_key=projection.source_key,
        title=projection.title,
        subtitle=projection.subtitle,
        description=projection.description,
        status=projection.status,
        normalized_title=normalized_title,
        fuzzy_text=normalized_title,
        tags=sorted({normalize_search_text(tag) for tag in projection.tags if tag.strip()}),
        document_data=json.loads(json.dumps(projection.document_data)),
        source_hash=projection_source_hash(projection),
    )


def _source(*, record_class: str = "fastest") -> RecordProjectionSource:
    return RecordProjectionSource(
        result_id=3,
        definition_id=7,
        status="resolved",
        history_complete=True,
        gap_reasons={},
        record_class=record_class,
        build_kind="door",
        version_scope="all_time",
        category_key="door:door|2x2|t[20]|Door:r[]:p[]",
        title="Fastest 2x2 Door",
        subtitle="All-time",
    )


def _holder(build_id: int, *, title: str, metric: dict[str, object] | None = None) -> RecordHolderProjectionSource:
    return RecordHolderProjectionSource(
        build_id=build_id,
        title=title,
        subtitle="All-time",
        metric={"timing": [10, 12]} if metric is None else metric,
    )


def _record_hit(document: SearchDocument, score: float | None = 1.5) -> RecordSearchHit:
    hit = _to_hit(document, score)
    assert isinstance(hit, RecordSearchHit)
    return hit


def test_a_record_hit_names_its_top_ranked_holder() -> None:
    """The holder id is what the "View build" affordance and the API's `build_id` open."""
    projection = build_record_projection(
        _source(),
        (_holder(41, title="Fast 2x2 Door"), _holder(42, title="Second 2x2 Door")),
    )

    hit = _record_hit(_indexed(projection))

    assert hit.build_id == 41
    assert hit.build_title == "Fast 2x2 Door"
    assert hit.title == hit.build_title


def test_a_record_with_no_holder_names_no_build() -> None:
    projection = build_record_projection(_source())

    hit = _record_hit(_indexed(projection))

    assert hit.build_id == 0
    assert hit.title == "Fastest 2x2 Door"
    assert hit.metrics == {}


def test_a_record_hit_carries_the_holders_measurements() -> None:
    projection = build_record_projection(_source(), (_holder(41, title="Fast 2x2 Door"),))

    hit = _record_hit(_indexed(projection))

    assert hit.metrics == {"timing": "10, 12"}


def test_a_measurement_the_computation_could_not_establish_is_not_reported() -> None:
    """`_metric_snapshot` writes JSON null for an unproven stage, which is not a measurement."""
    projection = build_record_projection(
        _source(record_class="smallest"),
        (_holder(41, title="Small 2x2 Door", metric={"volume": 240, "timing": [10, None], "completion_at": None}),),
    )

    hit = _record_hit(_indexed(projection))

    assert hit.metrics == {"volume": 240, "timing": "10, ?"}


def test_a_record_hit_reports_the_indexed_scope_and_class() -> None:
    projection = build_record_projection(_source(), (_holder(41, title="Fast 2x2 Door"),))

    hit = _record_hit(_indexed(projection))

    assert hit.record_class == "fastest"
    assert hit.version_scope == "all_time"
    assert hit.score == 1.5


def test_a_document_missing_its_scope_falls_back_to_the_domain_spelling() -> None:
    """A hand-written or stale document must not invent a `version_scope` no query matches."""
    document = _indexed(build_record_projection(_source()))
    document.document_data = {}

    hit = _record_hit(document, None)

    assert hit.version_scope == "all_time"
    assert hit.record_class == "unknown"
