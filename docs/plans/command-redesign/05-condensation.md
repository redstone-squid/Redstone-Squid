# Phase 5: condensing the long tail

> **Status.** In progress. Unlike phases 1–4 this one is a sweep rather than a single
> rebuild, so it lands as a sequence of steps; the table below records what has shipped.

## Problem

Phases 1–4 fixed the four surfaces the dogfooding session named. What is left is the tail:
groups that exist because a subsystem grew one, commands that are three spellings of one
question, and the cross-cutting defects C2–C7 from [00-audit.md](00-audit.md) that no single
phase owned.

C1 already took the pressure off the count — nine staff groups no longer appear in a
non-staff picker — so what remains is not "too many commands" in the abstract. It is
specific: commands that make you retype something the bot just printed, commands that take a
pasted message link where a right-click belongs, replies that leak UUIDs and raw ids, and
three different ad-hoc truncation schemes standing in for a paginator.

## Steps

| # | Scope | Status |
|---|-------|--------|
| 5.1 | The `vote` group disappears: `vote poll` retired, `vote delete` becomes a context menu, `poll close`/`refresh` become buttons on the poll card (C4, C7) | **Delivered** |
| 5.2 | `/admin` becomes `/records` and drops the `records-` prefix every member carried; `/help` learns to list app-only commands | **Delivered** |
| 5.3 | `/notifications`: one panel and one `follow`, seven commands down to two (C3, C5) | **Delivered** |
| 5.4 | `/account` claim review moves onto the `claims` list as buttons | Not started |
| 5.5 | `/perm`: `whoami`, `test` and `explain` collapse into `perm can` | **Delivered** |
| 5.6 | A shared list paginator, applied to `build queue`, `version list`, `account claims` and the records diagnostics (C6) | Not started |
| 5.7 | One ephemerality rule, applied bot-wide (C2) | Not started |

Ordering is by independence, not by value: each step is a commit that stands alone, and the
two sweeps (5.6, 5.7) come last because they touch what the earlier steps rewrite.

## Not in this phase

- **`/starboard`'s 14 commands.** The audit's own suggestion was "C1 first, then decide how
  much is worth rebuilding", and C1 has since hidden the whole group. A panel like phase 4's
  is the right shape, but it is a phase of its own, not a step in a sweep.
- **`/role` and the rest of `/perm`.** 22 staff commands that a non-staff picker no longer
  shows. Renaming `role` (it manages permission-role objects, not Discord roles) is a
  documentation problem more than a UI one.
- **`build edit`'s 22 flags.** Phase 1 established the shape the consolidation would take;
  doing it needs the same care phase 1 needed and does not fit here.

## 5.1 — polls carry their own controls

Five commands became one command and one right-click.

**`poll close` and `poll refresh` are buttons on the poll card.** Both took a
`discord.Message`, which in slash form means copying a link to the card you are looking at
and pasting it back at the bot (audit C4). They are now dynamic items in
`squid/bot/voting/controls.py`, rendered onto an open poll's card and dropped from a closed
one. Nothing is encoded in either custom id: the poll a click means is the message the
button is on, which also means a card published by an earlier release still routes.
`test_an_open_poll_card_carries_its_own_close_and_refresh_controls` pins both ids, since
renaming one would break every poll already open with nothing else failing.

**Neither button asks for consent, and the commands should not have either.** Closing and
refreshing store nothing about whoever asked — `close` writes a status, `refresh` recomputes
cached weights — so the account id is now read through `AccountIdCache` rather than minted.
The old commands ran `ensure_consented_account` first, which meant a staff member closing
somebody else's poll gained an account row for the privilege of clicking. Authorization is
unchanged: both controls run the session's own `can_close`, which admits the author or a
holder of `vote.poll.close_any`.

**`vote delete` is the "Vote to Delete" message context menu**, the second of the five
Discord allows this app. It was the clearest C4 case: a moderation vote is a judgement about
a specific message, and pasting that message's link into a slash option is the long way round
right-clicking it. The card still goes in the channel, because a public decision leaves a
public artifact; only the confirmation is ephemeral.

**`vote poll` is gone** — it was a deprecated alias — and with `delete` moved, the `vote`
group has no members and disappears.

**`/poll` is one app-only command.** It was a hybrid group whose every member answered "use
the slash command" without an interaction (audit C7), so the prefix tree advertised three
entry points and honoured none. A poll starts in a modal, so C7's "declare it app-only" is
the honest answer, and once `close` and `refresh` moved onto the card the group had one
member left to hold.

Two supporting moves: `describe_rejection` and the actor resolution moved out of `VoteCog`
into `squid/bot/voting/actors.py`, because a button callback has an interaction and a client
but no cog; and `reply_layout` joined `squid/bot/utils/components.py`, since a component
callback cannot know whether something upstream already spent the response.

### Taxonomy edits

Removed: `vote`, `vote poll`, `vote delete`, `poll close`, `poll create`, `poll refresh` —
the two groups the prefix tree carried for polls, replaced by one app command and one context
menu. `test_polls_are_one_app_only_command` records the new shape, since an app-only command
is invisible to the prefix tree that pins everything else.

### Known cost

A poll whose card was deleted can no longer be closed by hand; it closes at its deadline like
any other. The renderer already treats a deleted card as deliberate (`repost_if_deleted =
False`), so this trades a rarely-used escape hatch for not asking anyone to paste a link
again. The prefix forms of `vote delete` and the poll commands are gone with them.

## 5.2 — the group `admin` always was

`/admin` held four commands, all of them record-computation tooling, and every one repeated
the group name it actually wanted: `admin records-gaps`, `admin records-title-issues`,
`admin records-rebuild`, `admin records-lookup`. It is now `/records gaps`, `title-issues`,
`rebuild` and `lookup`. C1's `manage_guild` visibility hint moves with it, and the nodes are
unchanged — the rename is a rename.

`admin` was also a misleading name for a picker entry: it reads as "everything staff", while
the actual administrative surface is spread across `settings`, `perm`, `role`, `starboard`
and `error`.

**`/help` now lists app-only commands too.** The directory read `bot.commands`, which is the
prefix tree, so a command with no prefix spelling was missing from the one surface built for
discovery. That had been true of `/help` and `/notifications` all along, and 5.1 added `/poll`
to the list by making it app-only — a command nobody can find is not much better than a
command that does not exist.

The category map moved out of the command body into `DIRECTORY_CATEGORIES` for the same
reason as everything else in this phase: it is edited by hand and read by name, so a retired
group leaves an entry that lists nothing at all. `patterns` outlived phase 2 in it and `vote`
outlived 5.1 by a commit. `test_the_help_directory_names_commands_that_exist` resolves every
entry against a fully loaded bot, so the next one fails instead.

### Deferred within this step

`records lookup` still takes restriction ids rather than names (audit C5). Its autocomplete
already turns names into ids for the slash path, and accepting names outright means teaching
`RestrictionDefinition` to carry an id — a change in the builds context for the benefit of one
staff inspection command. The option text no longer instructs anyone to type ids by hand,
which is the part that was actively misleading.

## 5.3 — one panel and one verb for notifications

Seven slash commands became two.

**`/notifications show` is the panel.** `status` read the two delivery switches, `channels`
wrote both, `list` printed the subscriptions, and `unfollow` took an id you had to read off
`list` and type into a second command. All four are one screen now: two toggle buttons and a
select whose options *are* the subscriptions, so the id never becomes something a person
handles. The same shape as phase 4's settings panel, holding the service rather than a
snapshot for the same reason — the panel exists to write.

`show` rather than a bare `/notifications` because Discord has no bare-group invocation; a
hybrid group would fake one with a fallback, but this cog is app-only on purpose (the panel
needs an interaction), so the fallback is spelled out.

**`/notifications follow` is the one verb.** `follow-creator`, `follow-record` and
`follow-records` were one verb spelled three times, told apart by which argument you had.
Which argument you have still tells them apart; it just no longer costs three picker entries
and three near-identical descriptions. Giving none, or more than one kind at once, is an
error rather than a guess.

**Replies are layouts** (audit C3). The cog already translated its strings — the audit's
"raw strings" finding was half right — but sent them as bare content while the rest of the
bot sends cards. It also stopped printing the new subscription's id, which was the only
reason anybody had to remember one.

**A record filter reads as its predicates.** `list` rendered `str(filter.as_dict())`, braces
and all; the panel renders `door · smallest · tag 4=glass`.

### Deferred within this step

Creator and record subscriptions still show a public UUID (audit C5). Naming them means
resolving a creator profile and a record competition from the notifications surface, which
crosses into the accounts and records contexts and wants a subject-describing port rather
than a lookup smuggled into a view. What this step removes is the *retyping*, which was the
part that actually cost anything.

## 5.5 — `perm can`

`whoami`, `test` and `explain` were three commands asking one question — what may this person
do — separated by whose permissions and how much detail. `test` and `explain` ran the *same*
check on the same subject and differed only in how they printed the result, which is not a
distinction a picker entry should cost.

`perm can [user] [node]` covers all three: no arguments lists what you hold, a user lists what
they hold, and a node decides that one permission with the full trace `explain` rendered. The
short verdict `test` printed is gone rather than kept as a flag — nobody wants less
explanation of a permission decision, and the trace already leads with the verdict.

Authorization follows the arguments rather than the command. Reading your own permissions
needs `permission.node.view`; reading somebody else's needs `permission.subject.inspect`,
checked inline because one `@requires` cannot say "only when the user argument names someone
else". The group gate admits either, so nobody granted only one of them loses the command.

Prose elsewhere referring to `/perm explain` — the resolution domain, the administration
service, the decision docstrings — now names `perm can`, since that trace is the whole reason
those modules keep a trace at all.
