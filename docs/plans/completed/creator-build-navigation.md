# Navigating between creators and builds

Goal: from a creator page, open the builds credited to that creator or to one of their names. From a
build, open each credited creator's page.

## What shipped

- `CreatorPage` (`squid/bot/accounts/creator_page.py`) is routed at `/creators/{public_id}`.
  `/account creator:` and `/account user:` post it publicly; the route opens it privately. It has
  a **Builds** button (`/creators/{public_id}/builds`). If the creator has several names, a select
  searches the builds credited to one name. Hidden pages keep both controls but not Follow.
- The account workspace's Credits tab has a **Your builds** button.
- `creator:` takes a name or a public creator ID. Build documents index the IDs of the accounts
  their credits publicly belong to under `creator_id`, separate from names, so a credit named like
  someone's UUID is not attributed to them. ID clauses are filters only and add no ranking text.
  Record documents copy their holders' IDs. The catalogue summary shows the creator's name instead
  of the ID.
- `accounts/infrastructure/attribution.py` defines which account a credit belongs to: a claimed
  alias, or a matching public identity. Creator page counts, the search projection, and
  `AccountService.credit_owners` all use it.
- Alias changes already re-queued their builds. `account_identities_enqueue_credited_builds`
  does the same on link, unlink, and visibility changes. Merges move aliases and identities, so
  these triggers cover them too.
- The build view has a **Creators** select. A credit with an owner opens their page; an unclaimed
  name searches the builds credited to it.

Each jump opens a new personal message instead of replacing the current screen.

## Not done

- Searching `creator:<retired public id>` after a merge finds nothing. Links built by the bot use
  the canonical ID; rewriting retired IDs at query time would need the redirect table in search.
- Completion still suggests only names for `creator:`, not creator profiles.
- Published build-log posts have no Creators button.
