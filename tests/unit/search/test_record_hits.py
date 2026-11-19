"""Search hits read the current record projection's holder and metric fields."""

from unittest.mock import Mock

from squid.core.search.domain import BuildSearchHit, RecordSearchHit
from squid.core.search.infrastructure.hits import document_hit
from squid.core.search.models import SearchDocument


def test_record_hit_opens_the_first_ranked_holder_from_the_projection() -> None:
    document = Mock(
        spec=SearchDocument,
        resource_kind="record",
        source_key="result:12",
        title="Smallest 2x2 Door",
        subtitle=None,
        tags=["smallest", "door", "all_time"],
        document_data={
            "holder_build_ids": [7, 9],
            "metric": {"volume": 24},
            "base_title": "smallest",
            "version_scope": "all_time",
        },
    )

    hit = document_hit(document, None)

    assert isinstance(hit, RecordSearchHit)
    assert hit.build_id == 7
    assert hit.metrics == {"volume": 24}


def test_build_hit_reads_projected_dimensions_and_leaves_missing_ones_unknown() -> None:
    document = Mock(
        spec=SearchDocument,
        resource_kind="build",
        source_key="42",
        title="5x5 Piston Door",
        status="confirmed",
        description=None,
        tags=[],
        document_data={"dimensions": {"width": 10, "depth": 3}},
    )

    hit = document_hit(document, None)

    assert isinstance(hit, BuildSearchHit)
    assert hit.dimensions == (10, None, 3)
