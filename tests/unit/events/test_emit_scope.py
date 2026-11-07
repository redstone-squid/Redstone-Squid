"""Guard the tables that may emit domain events."""

from alembic_utils.pg_trigger import PGTrigger

from squid.persistence.alembic_entities import current_entities

EMITTING_TABLES = {"public.builds", "public.vote_sessions"}
"""Tables allowed to emit domain events.

Handlers can write to other tables in response to an event. Before adding a
table, follow those writes and their trigger conditions to check that they cannot
emit an endless cycle of events. This assertion makes changes to that set visible.
"""


def test_only_the_reviewed_tables_emit_domain_events() -> None:
    emitting = {
        trigger.on_entity
        for trigger in current_entities()
        if isinstance(trigger, PGTrigger) and "emit_domain_event" in trigger.definition
    }
    assert emitting == EMITTING_TABLES
