"""Guard the tables that may emit domain events."""

from alembic_utils.pg_trigger import PGTrigger

from squid.persistence.alembic_entities import ALEMBIC_UTIL_ENTITIES

EMITTING_TABLES = {"public.builds", "public.vote_sessions"}
"""Tables allowed to emit domain events.

Handlers write in response to events, so an emitting table that a handler also
writes to is a cycle. `ApplyBuildVoteOutcomeHandler` writes `builds` and
`PostConfirmedBuildHandler` writes `messages`; the chain terminates only because
`messages` is absent here. Adding a table to this set means checking that no
handler writes to it, directly or through a service.
"""


def test_only_the_reviewed_tables_emit_domain_events() -> None:
    emitting = {
        trigger.on_entity
        for trigger in ALEMBIC_UTIL_ENTITIES
        if isinstance(trigger, PGTrigger) and "emit_domain_event" in trigger.definition
    }
    assert emitting == EMITTING_TABLES
