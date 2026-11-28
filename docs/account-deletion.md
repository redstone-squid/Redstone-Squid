# Account deletion

A creator deletes their account with `DELETE /v1/accounts/me`. The community
records they contributed stay; nothing left behind identifies them.
[The architecture test](../tests/architecture/test_account_deletion.py) gives
every foreign key to `accounts` one of the dispositions below, so a new table
must choose one before it lands.

## Tombstone, not row deletion

Deletion keeps the `accounts` row, sets `deleted_at`, and clears its consent
receipt. `delete_account` in `squid/core/accounts/infrastructure/account_deletion.py`
then erases or unlinks everything else in one transaction, so the `ondelete`
rules never fire. A hard delete would fail on a dozen `RESTRICT` references, and
`CASCADE` on `votes` would silently change closed results.

A tombstone answers to no public ID, so its creator page and merge redirects
return 404. Signing in again with the same Discord or Minecraft identity creates
a new account, because the identities are erased.

## Before deleting

- **Recent sign-in.** The browser session must be under ten minutes old, the
  same window a merge uses. CLI devices, API keys, and impersonation cannot delete.
- **Settled reviews.** Deletion is refused while the account has a pending build,
  a pending edit proposal, a draft being processed or finalized, or a vote it
  opened that has not closed. There is no way to withdraw a submission yet, so
  the user waits for review.

## What happens to each reference

- **Erased.** Profile, identities, sessions, CLI devices, API keys they own,
  Minecraft server installations and grants, notification state, catalogue
  preferences, alias claims, permissions granted to them, editor seats on
  others' drafts, and their unsubmitted drafts, even shared ones.
- **Kept on the tombstone.** Builds they submitted, review history, drafts
  behind submitted builds, votes they opened, ballots (still counted), and staff
  actions such as grants, tags, and settings changes.
- **Unlinked.** Creator aliases lose their claim. Credits that name the account
  follow the `credits` parameter: `keep_name` turns them into plain name credits
  with no provider subject, and `remove` deletes them and renumbers the rest.
  Removing avoids a shared "Deleted creator" name, which would become a creator
  page of its own.
- **Swept.** Schematic sources and inference runs are deleted by their existing
  sweeps once no remaining draft refers to them.

## What remains elsewhere

- Build history is immutable, so earlier snapshots still hold the credits as
  they were, including names and provider subjects.
- Discord messages the bot indexed keep their authors.
- Backups and logs expire on their own schedule.
