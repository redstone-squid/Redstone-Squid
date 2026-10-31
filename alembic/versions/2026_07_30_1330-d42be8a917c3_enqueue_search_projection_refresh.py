"""enqueue search projection refresh

Revision ID: d42be8a917c3
Revises: a71c4e9d2f10
Create Date: 2026-07-30 13:30:00+00:00
"""

from collections.abc import Sequence
from typing import TypeVar

from alembic_utils.pg_function import PGFunction
from alembic_utils.pg_trigger import PGTrigger
from alembic_utils.replaceable_entity import ReplaceableEntity

from alembic import op
from squid.persistence.alembic_entities import alembic_util_entities

revision: str = "d42be8a917c3"
down_revision: str | Sequence[str] | None = "a71c4e9d2f10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FUNCTION_NAMES = {
    "enqueue_build_search_projection",
    "enqueue_computed_record_search_projection",
    "enqueue_legacy_record_search_projection",
    "enqueue_metadata_search_projection",
}
_TRIGGER_NAMES = {
    "build_creators_enqueue_search",
    "build_restrictions_enqueue_search",
    "build_types_enqueue_search",
    "build_versions_enqueue_search",
    "builds_enqueue_search",
    "doors_enqueue_search",
    "extenders_enqueue_search",
    "record_computation_runs_enqueue_search",
    "record_result_holders_enqueue_search",
    "record_results_enqueue_search",
    "restriction_aliases_enqueue_search",
    "restrictions_enqueue_search",
    "smallest_door_records_enqueue_search",
    "types_enqueue_search",
    "users_enqueue_search",
    "versions_enqueue_search",
}


def upgrade() -> None:
    """Install queue-only triggers and seed projections for existing resources."""
    for entity in _selected_entities(PGFunction, _FUNCTION_NAMES):
        op.execute(entity.to_sql_statement_create())
    for entity in _selected_entities(PGTrigger, _TRIGGER_NAMES):
        op.execute(entity.to_sql_statement_create())

    op.execute(
        """
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action)
        SELECT 'build', id::text, 'upsert' FROM public.builds
        ON CONFLICT (resource_kind, source_key) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action)
        SELECT 'record', 'legacy-smallest:' || record_id::text, 'upsert'
        FROM public.smallest_door_records
        ON CONFLICT (resource_kind, source_key) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action)
        SELECT 'record', 'result:' || rr.id::text, 'upsert'
        FROM public.record_results rr
        JOIN public.record_computation_runs rcr ON rcr.id = rr.run_id
        WHERE rcr.is_active
        ON CONFLICT (resource_kind, source_key) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action)
        SELECT 'metadata', 'restriction:' || id::text, 'upsert' FROM public.restrictions
        UNION ALL
        SELECT 'metadata', 'type:' || id::text, 'upsert' FROM public.types
        UNION ALL
        SELECT 'metadata', 'creator:' || id::text, 'upsert' FROM public.users WHERE ign IS NOT NULL
        UNION ALL
        SELECT 'metadata', 'version:' || id::text, 'upsert' FROM public.versions
        ON CONFLICT (resource_kind, source_key) DO NOTHING
        """
    )


def downgrade() -> None:
    """Remove search refresh triggers and their functions."""
    for entity in reversed(_selected_entities(PGTrigger, _TRIGGER_NAMES)):
        op.execute(entity.to_sql_statement_drop())
    for entity in reversed(_selected_entities(PGFunction, _FUNCTION_NAMES)):
        op.execute(entity.to_sql_statement_drop())


EntityT = TypeVar("EntityT", bound=ReplaceableEntity)


def _selected_entities(
    entity_type: type[EntityT],
    names: set[str],
) -> list[EntityT]:
    return [
        entity
        for entity in alembic_util_entities()
        if isinstance(entity, entity_type) and entity.signature.partition("(")[0] in names
    ]
