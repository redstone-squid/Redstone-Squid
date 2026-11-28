# Account deletion

Let a creator delete their account through `DELETE /v1/accounts/me` while the
community records they contributed stay intact. This plan sets the policy;
[the architecture test](../../tests/architecture/test_account_deletion.py) keeps
it in step with the schema.

## Tombstone, not row deletion

Deletion keeps the `accounts` row, sets a deletion time, and clears its personal
columns. Feature-owned steps then erase or withdraw the account's data in one
transaction, as `merge_accounts` does for merges. The `ondelete` rules never fire.

A hard delete would fail on a dozen `RESTRICT` references, and `CASCADE` on
`votes` would silently change closed results. The tombstone keeps staff actions
attributable to one ID, as `profile_clear_audit` already assumes, without keeping
anything that identifies the person. Signing in again with the same Discord or
Minecraft identity creates a new account, because the identities are erased.

## What happens to each reference

Every foreign key to `accounts` gets exactly one disposition:

- **Erased.** Private state with no value to anyone else: profile, identities,
  sessions, devices, API keys they own, Minecraft grants, notification state,
  preferences, permissions granted to them, editor seats on others' drafts, and
  unclaimed or pending alias claims. Private schematic sources leave through the
  existing reference-safe cleanup.
- **Withdrawn while pending, kept once settled.** Builds, submissions, drafts, and
  revision proposals. Pending work is withdrawn and erased; nothing exists for
  that yet. Their unsubmitted drafts go too, even when shared, so the
  confirmation names the editors who will lose access. Approved and rejected
  work stays on the tombstone because builds, review history, and media
  references depend on it.
- **Kept on the tombstone.** Actions taken on shared things: grants, roles, tags,
  settings, attestations, vote sessions they opened, and ballots. Ballots stay
  counted in open and closed votes alike, so deletion never changes an outcome.
- **Creator's choice.** Credits on builds. At deletion the creator chooses
  between keeping their name and replacing it with "Deleted creator". Either way
  the account link is removed, and the alias is retired so nobody else can claim
  the name and inherit the credits.

A tombstone that still submitted a kept build is in that build's
`maintainer_account_ids`. Nobody can act as it, but maintainer notifications
must skip deleted accounts rather than recreate notification rows for them.

## Outside PostgreSQL

- The search projection indexes creator names, so an anonymized credit must
  requeue the affected builds.
- Uploaded media for erased drafts goes through `MediaStorageCleanup`; cached
  avatars go with the identities.
- Discord posts by the bot show creator names. Approved build posts follow the
  credit choice when they are next re-rendered.
- Backups and logs expire on their own schedule, which the privacy notice states.

## Steps

1. Add a deletion phase per feature, alongside each feature's `account_merge` module,
   and a coordinator that locks the account and runs them in one transaction.
2. Add submission withdrawal, which deletion needs and creators can use directly.
3. Serve `DELETE /v1/accounts/me`. Require recent proof of the account, offer
   the export first, and take the credit choice in the request body.
4. Update the privacy notice to say what remains after deletion and why.
5. Move the test's disposition lists into the feature modules as they gain
   deletion phases, as the merge test does.
