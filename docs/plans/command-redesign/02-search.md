# Phase 2: one `/search`

> **Status.** Delivered 2026-08-18, together with this plan.

## Problem

Four commands answered one question — `search`, `restrictions search`, `patterns search`,
`patterns list` — and the three narrow ones existed because the wide one could not do their
job. Restrictions and patterns are both `tag_definitions` rows, and the search index projected
every approved tag as `metadata_kind = 'tag'`. The only thing `/search` could establish about a
tag was that it was a tag, so "show me patterns" needed its own command, and that command
printed `name (score: 12.3)` while the restriction one printed `restriction_id: name`.

Two smaller defects turned up while reading it:

- **The sort options contradicted each other.** `search_sorts` has always suggested complete
  answers in the `field` / `-field` form the domain parses — "width (descending)" is the value
  `-width`. The command built `SearchSort(sort, direction)` from the raw option, leaving the
  minus sign inside the field name, so the backend rejected `-width` as unsortable. Every
  descending suggestion the autocomplete offered crashed the command, and a bad sort field
  raised a bare `ValueError`, which the REST route turned into a 500 for a plain `?sort=bogus`.
- **The audit's ordering complaint was wrong.** It recorded that `scope`, `mode`, `sort` and
  `direction` appear "before the query". They do in the source, but discord.py sorts required
  parameters first when it serializes a command, so `query` has always been the first option in
  the picker. Nothing needed fixing there; the real problem was four knobs, not their order.

## Design

**The index says what a tag is.** The metadata projection records the definition's
`semantic_kind`, so a document is a `restriction`, a `pattern` or a `showcase` rather than a
`tag`. That makes `kind:pattern` and `kind:restriction` filterable and `pattern:<name>` match
the taxonomy entry the same way it already matches the builds carrying it. `kind:tag` is
emitted alongside, because it was the only taxonomy query previously possible. A migration
re-enqueues every definition so the worker rebuilds the documents already in the index; no tag
row changes, so no trigger would otherwise fire.

**The `scope` option names the taxonomies.** `records`, `builds`, `patterns`, `restrictions`,
`everything`. The first two and the last are the domain's scopes renamed; the middle two are
the metadata scope plus a `kind:` filter that the command supplies. Nobody discovers
`kind:pattern`, but everybody opens the option they were already going to fill in. The user's
own text is parenthesised when a filter is prepended, because AND binds tighter than OR and
`kind:pattern a OR b` would otherwise mean `(kind:pattern AND a) OR b`.

`SearchScope` itself is untouched: naming taxonomies is a Discord-UI concern and does not
belong in a domain enum two transports share.

**One sort option.** `direction` is gone and `sort` is parsed with `SearchSort.parse`, which
the REST routes already used. An unsortable field now raises `ValidationError` with
`INVALID_QUERY` on both transports and says which field it could not sort by.

**Option order** is `query, scope, sort, mode` — `mode` last, since keyword-vs-smart is a
retrieval mechanic rather than a question the reader has. It keeps its friendly labels.

## What was removed

- `/patterns` disappears entirely: `patterns search` is `/search scope:patterns`, and
  `patterns list` was a comma-joined dump of every pattern name into one message. The
  `pattern` option on `/build submit` and `/build edit` autocompletes the same list, in place,
  as you type — typing into an autocompleted field *is* the search.
- `restrictions search` is `/search scope:restrictions`. The `restrictions` group keeps
  `add-alias`, gains the group gate to match it, and is hidden from non-staff pickers: with the
  lookup gone it is a staff taxonomy group, not a public one.
- `BuildMetadataRepository`, `BuildMetadataQueries` and `RestrictionSearchItem` had no other
  callers and are deleted with the commands, along with `BuildQueryService`'s three
  pass-through methods.

## Taxonomy edits

- `patterns`, `patterns list`, `patterns search` and `restrictions search` leave
  `EXPECTED_PREFIX_COMMAND_TREE` and `UNGATED_COMMANDS`; `restrictions` leaves `UNGATED_COMMANDS`
  and joins `PICKER_VISIBILITY`.
- `restrictions` joins the group-gate check, so its gate stays no narrower than its member's.

## Not in this phase

- Folding the remaining filters into the query language. `width:5` already works; deciding
  whether `scope` should become `kind:` too is a bigger question than this phase.
- Creator and version metadata still have no named target; they remain reachable under
  `everything`, which is where the old plain `metadata` scope went.
- `build queue`'s raw submitter id and the other C5 leaks (phase 5).
