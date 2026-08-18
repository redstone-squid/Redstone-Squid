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
| 6.1 | `build queue`: a shared list paginator, submitters named rather than numbered, and a title that matches the command (C5, C6) | Not started |
| 6.2 | One edit surface: `/build edit` is app-only, its typed options seed the workspace view, and the gate is the view's owner-or-node rule (C7) | Not started |
| 6.3 | `build recalc` becomes the "Recalculate Build" message context menu (C4) | Not started |
| 6.4 | `build debug` attaches its dump as a file instead of pasting a `__dict__` into a message | Not started |
| 6.5 | `measure-timing` and `detect-lattice` move under `build schematic` | Not started |

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
