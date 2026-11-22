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

- **Builds**: opens the catalogue filtered to everything credited to this account.
- **Credited as…** (select, shown only when the account has more than one name): opens the
  catalogue filtered to one name (`creator:"Name"`). This is the per-alias view.

The account workspace's Credits tab gets the same **Builds** button for the owner's account.

### 2. Account filter in search: `by:<public_id>`

The catalogue should show an account's builds, not a name-based approximation:
`creator:"A" or creator:"B"` matches strangers who share a name, misses credits whose snapshot
spelling differs, and cannot exclude private identities.

Add a `by` search field that is compiled when the query runs, not projected. It is an EXISTS
over `build_credits` joined to claimed aliases and public identities of the account with that
public ID, using the redirect table for merged accounts. Because it is evaluated at query time, a
new claim, unlink, visibility change, or merge applies to the next search without reindexing.

Extract the `public_references` subquery from `get_creator_profile_record` into one function that
both the counts and the search compiler call. The counts on the creator page and the filtered list
then cannot disagree, and a private identity cannot leak through one of them. Calling it from
search makes search depend on account tables; that is the price of keeping the visibility rule in
one place.

Because it is an ordinary field, `by:` composes with sorting, other filters, records scope, and
saved preferences. The search summary should show the creator's display name instead of the
UUID; completion can offer `by:` from the existing `creator_profiles` suggestions.

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
- A Web/API counterpart. `/api/v1/creators` can expose `by` once it exists in search.

## Order of work

1. `CreatorPage` route; `/account` opens it.
2. Shared public-credit subquery, `by:` field, and the Builds controls on the creator page and
   Credits tab.
3. `credit_owners` and the Creators select on build detail.

Each step can ship separately.
