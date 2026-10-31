# Phase 6: `/build`

> **Status.** Delivered 2026-08-19. Like phase 5 this landed as a sequence of steps, each one
> a commit that stands alone.

## Problem

`/build` is the largest group in the bot and the one a normal user meets first. Phase 1 rebuilt
its front door — `submit` is one command with autocompleted options and a workspace behind them
— and left the rest of the group as the audit found it
([00-audit.md](00-audit.md), `/build`):

- **Two edit surfaces that disagree about who may edit.** `build edit` is a 22-flag
  `FlagConverter` gated on `build.submission.edit`; the **Edit** button on a submission card is
  the same operation gated on `BuildEditView.can_edit`, which also admits a pending build's own
  submitter. Same operation, two answers, decided by which entry point you happened to use.
  The command is also a hybrid that is not one (C7): its preview and confirmation only exist on
  the interaction path, so the prefix form silently commits without either.
- **`build queue` prints `submitter_discord_id` as a bare integer** (C5), has no pagination
  (C6), and titles the card "Open Records" while the command describes itself as listing
  pending submissions.
- **`build recalc` takes a `discord.Message`** (C4), which in slash form means copying a link
  to the message you are looking at and pasting it back at the bot.
- **`build debug` renders `str(build.__dict__)`** into a message body, where it is both
  unreadable and liable to hit the length limit on exactly the builds worth debugging.
- **Two schematic tools live outside `build schematic`.** `measure-timing` and `detect-lattice`
  sit directly under `build` while four sibling tools that read the same file sit one level
  down.

## Steps

| # | Scope | Status |
|---|-------|--------|
| 6.1 | `build queue`: a shared list paginator, submitters named rather than numbered, and a title that matches the command (C5, C6) | **Delivered** |
| 6.2 | One edit surface: `/build edit` is app-only, its typed options seed the workspace view, and the gate is the view's owner-or-node rule (C7) | **Delivered** |
| 6.3 | `build recalc` becomes the "Recalculate Build" message context menu (C4) | **Delivered** |
| 6.4 | `build debug` attaches its dump as a file instead of pasting a `__dict__` into a message | **Delivered** |
| 6.5 | `measure-timing` and `detect-lattice` move under `build schematic` | **Delivered** |

Ordering is by independence. 6.2 is the phase's real work and comes after the smaller steps
have settled the group's shape around it.

## The paginator, and phase 5.6

C6's shared paginator was scoped as step 5.6, which has not shipped, and 6.1 is the first
command that needs it. Rather than reinvent one for `build queue` and delete it again later,
the module lands here — `squid/bot/utils/pagination.py` — and 5.6 is reduced to applying it to
the call sites phase 5 named (`version list`, `account claims`, the records diagnostics), each
of which belongs to a later phase's surface anyway. [05-condensation.md](05-condensation.md)
records the same split.

It paginates a sequence of rendered lines rather than driving a cursor query. Every call site
5.6 lists already holds its whole list in memory, and `build queue` is a staff review queue
whose length is bounded by how much review is outstanding. A paginator that also owns fetching
is a different, larger thing; when a call site needs it, `SearchResultsView` is the shape to
copy, not this.

## Not in this phase

- **The submission card's own controls.** The **Edit** button and the voting controls on a
  build card are phase 5.1's shape applied to builds, and nothing in the audit says they are
  wrong. 6.2 changes what the command does, not what the card does.
- **`build view` and `build approve`/`reject`.** Three commands that take a build id and do one
  obvious thing with it. They are not overlapping spellings of one question, and an id with
  autocomplete behind it is not the kind of id C5 objects to.

## 6.1 — the review queue is a list, and lists are paginated

Three changes to one command, all of them things a reviewer had been reading around.

**The submitter is a mention.** The list rendered `submitter_discord_id` as a bare integer
(audit C5), which names nobody: a reviewer wanting to ask the submitter a question had to
paste the number somewhere that would resolve it. It is now `<@id>`, which every client
resolves and which pings no one under the `no_mentions()` policy the rest of the bot sends
with. The field is derived from the account rather than stored on the build, so it can be
absent; that build now reads "submitted by someone unlinked" instead of "submitted by None".

**The card is titled what the command does.** It said "Open Records" — a name from when this
group was records-only — while the command's own description said pending submissions.

**The list pages.** It printed every pending build into one message, which Discord truncates
at exactly the queue lengths worth looking at. The shared paginator is described above.

### Kept deliberately

The build id stays. It is the handle a reviewer types into `build approve`, it is what the
card's own footer calls the submission, and unlike the submitter snowflake it is a number the
user is meant to see.

## 6.2 — one edit surface

`build edit` was a 22-flag `FlagConverter`, one option short of Discord's cap, sitting beside
`BuildEditView` — a paged modal workspace reached from the **Edit** button on any build card.
Two surfaces for one operation, and they disagreed about the two things that matter.

**They disagreed about who may edit.** The command required `build.submission.edit`; the view
admits that node *or* the submitter of a build still pending review. A person could edit their
own pending submission by clicking a button and not by typing the command. The command now
opens the view, so the view's rule is the only rule and `can_edit` is the only place it is
written down.

**They disagreed about what a hybrid is.** The command's preview-and-confirm step ran only
`if ctx.interaction`, so the prefix form committed straight to the database with no preview at
all — a hybrid whose two halves did different things, which is C7 wearing a different hat. It
is app-only now, like `build submit`: a workspace needs an interaction.

**The shape is phase 1's.** Typed options for the fields with a taxonomy behind them — `id`,
`door_size`, `door_type`, `pattern`, `build_size`, `versions`, `restrictions`, `creators`,
`notes` — because slash options autocomplete and modals do not. Everything else lives in the
workspace, which now reaches five fields it could not before (`animated_restrictions`,
`extra_user_info`, and the server info trio); without those, merging the flags away would have
lost capability rather than consolidated it.

Options do not apply immediately. Each one is *staged* into the field that owns it, exactly as
if it had been typed into the modal, so one review prompt shows the option-supplied changes and
the hand-edited ones together, before anything is written. `BuildField.stage` is the
parse-and-remember half of a modal submission, lifted out so something other than a modal can
call it.

**One `restrictions` option replaces three flags.** `wiring_placement_restrictions`,
`animated_restrictions` and `component_restrictions` were three flags asking which bucket a
restriction belongs in — a fact about the restriction, not a decision for the person editing.
The taxonomy sorts them, via a new `sort_restrictions` free function in the builds domain (the
existing method wrote its answer onto a build, and staging needs the answer without one).

**A field the build does not have is a refusal.** `door_size` on an extender names nothing, and
silently dropping a typed option is the failure this merge exists to end.

### Fixed on the way

The modal's list parser did not strip whitespace, so a round trip through the workspace turned
`Alice, Bob` into `["Alice", " Bob"]` — the formatter writes `", "` and the parser split on the
comma alone. It surfaced here because the typed options now flow through the same parser the
flags' `ListConverter` used to bypass.

`squid/bot/utils/converters.py` is deleted. Every converter in it existed for the flags.

### Taxonomy edits

`build edit` leaves the prefix tree and becomes the second app-only member of the group.
`test_build_slash_group_includes_the_app_only_workspaces` records both, since an app-only
command is invisible to the prefix tree that pins everything else.

## 6.3 — recalculation is a right-click

`build recalc` took a `discord.Message`, which in slash form means copying a link to a message
and pasting it back at the bot (audit C4). Re-reading a build is a judgement about one specific
message, so it is now the **Recalculate Build** message context menu — the third of the five
this app may register, after phase 1's **Edit Build** and phase 5.1's **Vote to Delete**.

**It now says when there is nothing to recalculate.** Inference is an `on_message` listener
that silently ignores anything outside a build log channel, so the command answered "Build
recalculated." whatever you pointed it at, including messages no build could ever come from.
The eligibility test is split out of the listener and the menu answers with it.

**The permission gate moved into the body.** A context menu cannot carry a `commands.check`, so
`enforce(interaction, ...)` in `squid/bot/utils/permissions.py` raises exactly what
`requires(...)` raises and the shared error presenter renders one refusal for both surfaces,
`forbid` explanation included. Writing a bespoke denial card here is how the same refusal ends
up reading differently depending on which surface produced it.

**Both of the cog's context menus are now removed on unload.** The tree belongs to the bot
rather than the cog, so reloading the extension left the old menu registered and the second
`add_command` raised. That was already true of **Edit Build**; adding a second menu to the same
cog is what made it worth fixing rather than noting.

### Taxonomy edits

Removed: `build recalc`. The node `build.submission.recalc` is unchanged and is still what the
menu checks.

## 6.4 — a debug dump is a file

`build debug` rendered `str(build.__dict__)` into a message body. Two things were wrong with
that and neither was the command's existence: the output passes Discord's length limit on
exactly the builds complicated enough to be worth debugging, and Python's `repr` renders an
`IntEnum` status as `<Status.PENDING: 0>` and every `Instant` as its constructor call.

It now attaches `build-<id>-debug.json`: sorted keys, indented, enums by name, and one message
carrying both the file and the sentence saying what it is. `embedding` is dropped, because a
few thousand floats would be most of the file and mean nothing to a reader; the length is kept
as `embedding_dimensions`, since "is this build embedded at all" is a question somebody
actually asks.

## 6.5 — the schematic tools live under `build schematic`

`measure-timing` and `detect-lattice` read a build's attached schematic and report what the
engine found in it, which is the sentence that describes `build schematic` as a whole. They
sat one level up for no reason anybody recorded, and their permission nodes had said where
they belonged the whole time: `build.schematic.measure_timing` and
`build.schematic.detect_lattice` were never renamed to match the commands' location, because
the location was the thing that was wrong.

### Taxonomy edits

`build measure-timing` and `build detect-lattice` become `build schematic measure-timing` and
`build schematic detect-lattice`. The nodes are untouched, so no grant changes.
