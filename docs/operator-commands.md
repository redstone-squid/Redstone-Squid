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
