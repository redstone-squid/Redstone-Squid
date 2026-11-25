# Operator commands

`runtime-settings` lists the current global settings and revision. Choose a setting
with slash-command autocomplete to inspect its value and JSON schema, including
its type, bounds, and default example. Supply a JSON value to change it: for
example, `welcome_enabled false` or `welcome_forward_chance 0.25`. Channel lists
use JSON arrays of positive Discord channel IDs. Include the inspected revision
to reject an edit if another operator has changed settings in the meantime.
Viewing and editing require their respective runtime-setting permissions; edits
also require an account with current privacy consent.

The hidden owner-only prefix command `s` synchronizes application commands. Use
the configured bot prefix before these forms:

| Form | Scope and effect |
| --- | --- |
| `s` | Sync global commands. |
| `s ~` | Sync commands registered for the current guild. |
| `s *` | Copy global commands into the current guild, then sync it. |
| `s ^` | Clear the current guild's commands, then sync the empty guild tree. |
| `s <guild-id> [<guild-id> ...]` | Sync the specified guild trees. |

Guild flags require running the command in a guild. Explicit guild IDs take
precedence over flags. Guild synchronization does not publish changes globally.
Development layout demos and deliberate error triggers are excluded from ordinary
user workflows.

## Trace a worker change

In the application database, look up a retained queue item, domain event, build history entry or edit by its table and primary key. The trace includes earlier requests coalesced into the same work item and the chain of workers that produced it.

```sql
SELECT t.occurred_at, t.relation, t.item, t.transition, t.invocation_id,
       t.operator, t.effective, t.impersonation_session_id
FROM trace_work_activity((
    SELECT max(id) FROM work_activity
    WHERE relation = 'domain_events' AND item = '{"id": 42}'::jsonb
)) AS t
ORDER BY t.id;
```

The operator and effective actor identify the caller of each transition. A new worker execution gets its own invocation ID on every attempt. Missing identity denotes work outside an attributed invocation, including older producers during rollout. Collection starts when the migration runs. Access to these records belongs with internal audit access.
