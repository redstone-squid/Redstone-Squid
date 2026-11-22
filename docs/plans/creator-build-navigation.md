# Navigating between creators and builds

Goal: from a creator page, open the builds credited to that creator or to one of their names. From a
build, open each credited creator's page.

## What exists today

- A build's `build_credits` rows reach an account in two ways. An alias credit (`alias_id`) counts
  when an account has claimed the alias. A provider credit (`kind`, `subject`) counts when it
  matches one of the account's identities, and only if that identity is public.
  `get_creator_profile_record` already applies these rules to count builds per name.
- The search index stores each credit's `name` as a `creator:` filter value. Claims, unlinking,
  identity visibility, and account merges never reproject builds, so names are the only creator
  information search has.
- Public creator pages are a static `FollowCard` returned by `/account creator:` and
  `/account user:`. There is no route for them, so a build cannot link to one.
- The build detail shows creators as plain text in the card's "Creators" field.

## Design

### 1. Creator page route: `/creators/{public_id}`

Move `VerifyCog._public_profile_card` into a `CreatorPage` screen with
`@router.route("/creators/{public_id}")`. `/account creator:` and `/account user:` open it.
Persistent addresses like `ui:/creators/<uuid>` stay within the 100-character limit. A retired
public ID resolves to the merged account through the existing redirect, and the page stays
reachable when hidden, because hidden pages still show credits today.

The page keeps its card and Follow button and adds one row of controls:

- **Builds**: opens the catalogue at `creator:<public_id>`.
- **Credited as…** (select, shown only when the account has more than one name): opens the
  catalogue filtered to one name (`creator:"Name"`). This is the per-alias view.

The account workspace's Credits tab gets the same **Builds** button for the owner's account.

### 2. `creator:` matches names and creator IDs

The catalogue should show an account's builds, not a name-based approximation:
`creator:"A" or creator:"B"` matches strangers who share a name, misses credits whose snapshot
spelling differs, and cannot exclude private identities.

The projection indexes each build's credits twice: the credited names, as now, and the public
creator ID of every account a credit resolves to. A credit resolves through a claimed alias or a
public identity. A record document indexes its holders' IDs the same way. `creator:` stays the only
field users see. A UUID value matches the ID storage field (`creator_id`). Any other value matches
names. Keeping the two storage fields separate means nobody can credit a build to the literal
text of someone's UUID and have it appear on that person's page. A credit named exactly like a
UUID can still be found by plain text search.

The index has to follow ownership changes. Extend the existing `creator_aliases_enqueue_search`
trigger, and add triggers on `account_identities` and the merge path. They enqueue every build
credited to the affected alias or subject: on claim and unclaim, link and unlink, an `is_public`
change, and a merge, which changes the surviving public ID. A retired public ID in a query is
rewritten to the canonical one before searching, using the redirect table, so old links keep
working.

The resolution rule (claimed alias, or public identity) already exists as the `public_references`
subquery in `get_creator_profile_record`. Extract it so the profile counts and the projection use
the same function. Otherwise the page count and the list it opens can disagree, or one of them can
expose a private identity.

The query box shows `creator:<uuid>`. The search summary should show the creator's display name.
Completion already suggests names. It should also offer creator profiles, whose suggestions
insert the ID.

### 3. Build to creators

Add a credit resolver to the account service:
`credit_owners(build_id) -> dict[int, UUID]`, which maps credit position to public creator ID.
It uses the same visibility rule, so an unclaimed name or a private identity resolves to nothing.

`_BuildDetail` renders a **Creators** select in its action row next to Schematic tools. It has
one option per credit, labeled with the name and, if it is not "creator", the role:

| Credit                  | Option opens                                  |
| ----------------------- | --------------------------------------------- |
| Resolves to an account  | `/creators/{public_id}`                       |
| Unresolved name         | Catalogue search `creator:"Name"` ("More by") |

Every credit is navigable, and unclaimed names act as the alias view. Use a select rather than
buttons because builds can have many creators, and Discord limits a row to five buttons. Offer at
most 25 options; leave out the select when the build has no credits.

### Navigation behavior

Each jump dispatches the target route (`CreatorPage.open`, `BuildBrowseScreen.create_search`)
and opens a new personal message. It does not replace the current screen. Going build →
creator → builds → build is several hops, and keeping each screen alive gives the reader the
history that a Back stack would otherwise provide. If this feels noisy, adding in-place
replacement with Back is a separate change.

## Out of scope for now

- A Creators button on published build-log posts (`ui:/builds/{id}/creators`). It is easy once
  the route exists but adds a row to every post.
- A Web/API counterpart. The API search gets `creator:<id>` for free.

## Order of work

1. `CreatorPage` route; `/account` opens it.
2. Shared credit-resolution subquery, creator IDs in the index with their enqueue triggers, and
   the Builds controls on the creator page and
   Credits tab.
3. `credit_owners` and the Creators select on build detail.

Each step can ship separately.
