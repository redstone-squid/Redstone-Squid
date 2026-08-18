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
| 5.1 | The `vote` group disappears: `vote poll` retired, `vote delete` becomes a context menu, `poll close`/`refresh` become buttons on the poll card (C4, C7) | Not started |
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
