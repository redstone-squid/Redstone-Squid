"""Search hits read the current record projection's holder and metric fields."""

from unittest.mock import Mock

from squid.search.domain import RecordSearchHit
from squid.search.infrastructure.repository import _to_hit
from squid.search.models import SearchDocument


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

    hit = _to_hit(document, None)

    assert isinstance(hit, RecordSearchHit)
    assert hit.build_id == 7
    assert hit.metrics == {"volume": 24}
