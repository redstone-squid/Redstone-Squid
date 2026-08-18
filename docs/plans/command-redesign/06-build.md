# Phase 6: `/build`

> **Status.** In progress. Like phase 5 this lands as a sequence of steps, each one a commit
> that stands alone; the table below records what has shipped.

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
| 6.2 | One edit surface: `/build edit` is app-only, its typed options seed the workspace view, and the gate is the view's owner-or-node rule (C7) | Not started |
| 6.3 | `build recalc` becomes the "Recalculate Build" message context menu (C4) | Not started |
| 6.4 | `build debug` attaches its dump as a file instead of pasting a `__dict__` into a message | Not started |
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
