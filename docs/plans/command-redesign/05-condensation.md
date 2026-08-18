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
| 5.2 | `/admin` becomes `/records` and drops the `records-` prefix every member carried (C5 on its restriction input) | Not started |
| 5.3 | `/notifications`: one `follow`, layouts and i18n, subscriptions named rather than UUID'd, unfollow from the list (C3, C5) | Not started |
| 5.4 | `/account` claim review moves onto the `claims` list as buttons | Not started |
| 5.5 | `/perm`: `whoami`, `test` and `explain` collapse into one command | Not started |
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
