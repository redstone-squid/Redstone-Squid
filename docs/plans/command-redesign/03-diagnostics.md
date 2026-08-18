# Phase 3: `/error` you can read

> **Status.** Delivered 2026-08-19, together with this plan.

## Problem

The audit gave this phase a two-line root-cause fix and two interaction complaints. The root
cause turned out to be wrong, the prefix form turned out to be broken in the opposite direction
from the one reported, and the two interaction complaints were real.

### The prefix form was never missing `invoke_without_command=True`

The audit recorded that `hybrid_group(name="error", fallback="show")` cannot bind `reference`
on the prefix side because the group runs its callback without parsing arguments. That is true
of a plain `commands.group`, but not of this one: `HybridGroup.__init__` assigns
`self.invoke_without_command = True` unconditionally (`discord/ext/commands/hybrid.py`), and
`Group.invoke` rewinds the argument view when the first word is not a subcommand before
delegating to `Command.invoke`. A reproducer against the locked discord.py confirms it —
`!error abc123` binds `reference="abc123"`, `!error recent` reaches the subcommand, and bare
`!error` raises `MissingRequiredArgument`, which is what a required parameter should do.

Passing the flag the audit asked for would have been a no-op on a line that already reads that
way. `test_the_error_group_binds_a_reference_from_the_prefix_form` pins the real contract
instead, because the thing that would actually take the prefix form away — converting the group
to a plain `app_commands.Group` — fails nothing today.

### The prefix form did work, and that was the bug

`Context.send` drops `ephemeral` when there is no interaction; it forwards to
`Messageable.send`, which has no such parameter. Every reply in the cog passed
`ephemeral=True`, with a comment above the argument saying a traceback names internal paths and
carries the unredacted message every other surface withholds. On the slash side that held. On
the prefix side it silently did not, so `!error <ref>` posted the traceback, the card, and the
`error-<ref>.txt` attachment with its log tail into whichever channel a moderator typed it in.
The command the audit reported as not working was in fact the one leaking.

### A report could not be read past its first screen

`/error show` rendered a fixed 1200-character tail of the traceback and attached the rest. The
attachment was never the complaint — needing to leave Discord to read frame 4 of a traceback
was. The log tail was worse off: it existed only inside the attachment and had no on-screen
form at all.

### `error recent` listed references you had to retype

Each line named a reference and offered no way to open it, so reading the list and then
inspecting one entry meant copying a hex string into a second command.

## Design

**One view, two states.** `ErrorReportView` (`squid/bot/diagnostics_view.py`) renders either the
recent list or one open report, and both entry points construct the same object: `/error show`
opens it on a report, `/error recent` opens it on the list. Opening an entry from the list is a
re-render, not a round trip — `recent` already fetched every row the select can offer, so the
view holds reports rather than a service. That is the one structural difference from
`SearchResultsView`, which pages a backend and therefore has to keep one.

**The body pages instead of previewing.** The card keeps its summary fields pinned — when,
where, exception, full ID, and the work-lost and ambiguity notes — and the description becomes
one page of the report body, with earlier/later buttons and a footer naming the section and the
page number. The traceback pages first and the log tail follows it, which is the first time the
log tail has been readable without downloading anything. Pages break on line boundaries so a
frame is never cut in half, and a single line longer than a page is hard-split rather than
dropped.

It opens on the **last** traceback page. That is deliberately the old preview's content: the
failing frame is at the end, so the previous behaviour was the right default, and the buttons
exist to walk back from it rather than to replace it.

**The attachment follows the open report.** `/error show` still sends `error-<ref>.txt`, and
opening an entry from the list now attaches it too, so a report opened from the list is the same
artifact as one looked up by reference. Going back to the list clears it, since the list is not
about any one report. `edit_interaction_layout` grew an optional `attachments` argument for
this; omitting it leaves the message's files alone, so a paging button does not re-upload what
it is not changing.

**Every reply is private, whatever the transport.** `Diagnostics._deliver` sends ephemerally
when there is an interaction, sends normally when the context is already a direct message, and
otherwise sends to the author's DMs and leaves a one-line acknowledgement in the channel. A
closed DM says so and delivers nothing — it does not fall back to the channel, because the
channel is exactly what the report must not reach.

DM delivery rather than declaring the group app-only: the dogfooding complaint was that the
prefix form did not work, so the fix is for it to work privately, not to disappear.

## Not in this phase

- **C2 in general.** `_deliver` is the policy for one cog whose payload is a traceback. The
  bot-wide ephemerality rule, and the 11 sites already writing
  `ephemeral=ctx.interaction is not None`, stay with phase 5.
- **A shared paginator.** `_paginate` splits one report body; `SearchResultsView`,
  `build queue`, `version list`, `account claims` and `admin records-gaps` still each have their
  own scheme (C6). Retiring all five is phase 5's job, and doing it from a sample of one would
  be guessing at the interface.
- **Purging a single report.** `error clear` remains all-or-nothing.
