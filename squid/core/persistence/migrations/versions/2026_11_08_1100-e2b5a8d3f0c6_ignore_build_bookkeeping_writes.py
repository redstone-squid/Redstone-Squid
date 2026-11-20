"""Ignore lease and embedding writes in the build queue triggers.

Acquiring or releasing an edit lease, or storing an embedding, used to queue a search projection,
a record recompute, and a Discord edit for a build whose visible content had not changed.
"""

from alembic_utils.pg_trigger import PGTrigger

from alembic import op

revision: str = "e2b5a8d3f0c6"
down_revision: str | None = "d1a4f7c2e9b5"
branch_labels: str | None = None
depends_on: str | None = None

_TRIGGERS = [
    "CREATE TRIGGER builds_enqueue_search AFTER INSERT OR DELETE ON public.builds FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();",
    "CREATE TRIGGER builds_enqueue_search_on_update AFTER UPDATE ON public.builds FOR EACH ROW WHEN ((to_jsonb(OLD.*) - ARRAY['embedding', 'locked_at', 'lock_token', 'lock_expires_at']) IS DISTINCT FROM (to_jsonb(NEW.*) - ARRAY['embedding', 'locked_at', 'lock_token', 'lock_expires_at'])) EXECUTE FUNCTION public.enqueue_build_search_projection();",
    "CREATE TRIGGER builds_enqueue_discord_sync AFTER INSERT OR DELETE ON public.builds FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();",
    "CREATE TRIGGER builds_enqueue_discord_sync_on_update AFTER UPDATE ON public.builds FOR EACH ROW WHEN ((to_jsonb(OLD.*) - ARRAY['embedding', 'locked_at', 'lock_token', 'lock_expires_at']) IS DISTINCT FROM (to_jsonb(NEW.*) - ARRAY['embedding', 'locked_at', 'lock_token', 'lock_expires_at'])) EXECUTE FUNCTION public.enqueue_discord_sync();",
]
_OLD_TRIGGERS = [
    "CREATE TRIGGER builds_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.builds FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();",
    "CREATE TRIGGER builds_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.builds FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();",
]


def upgrade() -> None:
    for sql in _OLD_TRIGGERS:
        op.drop_entity(PGTrigger.from_sql(sql))
    for sql in _TRIGGERS:
        op.create_entity(PGTrigger.from_sql(sql))


def downgrade() -> None:
    for sql in _TRIGGERS:
        op.drop_entity(PGTrigger.from_sql(sql))
    for sql in _OLD_TRIGGERS:
        op.create_entity(PGTrigger.from_sql(sql))
