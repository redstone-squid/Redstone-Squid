# Search index redesign

Rebuild the search index so every match is traceable to the field and value that produced it.
Highlighting then renders that evidence instead of guessing with its own matching rules.
This replaces steps 2–5 of [Discord record search](discord-record-search.md).

## Problems

A search document is a handful of catch-all text columns. `tags` mixes tag names, creator
names, and version names. A record's `description` holds its holders' build titles.
`fuzzy_text` joins the title, subtitle, tags, and aliases. Once text is merged, nothing
downstream can tell what matched:

- The compiler ORs exact equality, `tsquery`, and trigram similarity across different
  columns. Quoted text therefore still matches fuzzily: `title:"glass door"` admits
  **5x5 Door\* · Locational** (`#3292`).
- The highlighter re-derives matches with its own regex, on the title only, and skips OR
  branches. `fastest 2x2 (glass OR door)` bolds only **2x2**.
- A record found through its holder's build title, such as `no furnaces` finding
  `result:632822`, cannot say so.

Some data is missing from the index:

- 4,697 of the 4,972 builds with a custom `display_name` cannot be found by it. `#7684`,
  "hidden enchant table", is indexed only as "🤖 Utility".
- `orientation` and `extender_length` are registered for builds, but only record documents
  carry them, so `orientation:trapdoor` never matches a build. Extender type is not
  projected.
- Only current results are indexed, keyed by result ID. Retired records, holder history,
  and tied builds are not searchable, although the web shows them.
- Titles carry display decoration: the 🤖 marker, `?x6`, `*`, and a copy of the subtitle.

Every projection rewrites its document, its filter rows, and four GIN indexes, even when
nothing changed. Changes are queued by triggers and by the worker itself: build to records,
and tag to records.

## Design

### Documents are typed field values

A document has an identity, a status, display data for its hit, and a list of field
values. Each value names its field, its position within the field, and, for record holder
fields, the holder build. The existing filter table becomes this table, with text values
added.

Text goes only into its own field: `title`, `name`, `subtitle`, `description`,
`restriction`, `restriction_alias`, `type`, `tag`, `creator`, `version`, `holder.title`,
`holder.creator`, and so on. Display decoration stays in display data and is never indexed.

Identities are stable. A build is keyed by its ID, a record by its public ID, and metadata
by kind and ID. A revived record keeps its key.

### One registry and one tokenizer

The field registry becomes the single definition of each field. A field declares:

- its type;
- whether bare free text searches it, and its ranking weight;
- its matcher: words, an exact keyword, a number, or a timestamp;
- the scopes it applies to.

The projection emits values for registered fields only. The parser resolves names against
the registry, and the compiler and explainer both read it.

One Python normalizer and tokenizer handles case folding, accents, `×`, and token
offsets. Projection and queries both use it. Postgres indexes our tokens with the
`simple` configuration, so it never tokenizes differently, and a span is a token range in
one field value.

Matching rules:

- **Unquoted words** match tokens. A token within a small edit distance is a close match.
- **Quoted text** matches the exact token sequence, with no fuzzy fallback.
- **Keywords** (`restriction:`, `kind:`) match whole normalized values.
- **Bare free text** searches free-text fields. Category fields outweigh holder fields, so
  a category match ranks above a holder match.

### SQL finds and ranks; Python explains the page

SQL keeps recall and ranking fast. Each document has one weighted vector, generated from
its free-text values, and filters run against the value table as they do today.

After a page is chosen, the explainer evaluates the parsed query against the field values
of the page's hits. It reports which leaf matched which value of which field, which OR
branches were satisfied, and the token spans. A close match is marked as close, and a
semantic match is marked as related. Neither has spans. Negated terms never produce
evidence.

Hits carry this evidence in place of `title_match`. Discord and the API render it:

- spans in the heading and subtitle;
- a line naming the holder when only a holder matched;
- a "close match" marker.

A property test checks that the explainer and SQL agree on which documents match, for
random documents and queries.

### Projection and queuing

A projection is a pure function from source rows to a document. The store compares the
document hash, which includes a format version, and skips unchanged documents. Bumping the
version requeues everything.

Statement-level triggers queue all projection work, including the build to record and tag
to record fan-out that the worker does now. A statement inserts its requests in one
`INSERT … SELECT`. It skips deletes for keys that have neither a document nor a queued
request.

## Coverage

New and corrected values:

- Builds: custom name, orientation, extender length and type.
- Records:
  - Retired records, with `record_state:retired`. They are hidden by default.
  - Former holders, with `former_holder:`.
  - Tied builds, with `tied:`. A tie is shown as a tie, never as a holder.

Not now: Discord source post text, and schematic metadata such as declared names and sign
text. Only 65 builds have a schematic.

## Steps

Each step ships on its own.

1. **Field values and identities.**
   - Add the registry declarations and the tokenizer.
   - Project typed values, the new coverage, and record public-ID keys. Skip unchanged
     documents.
   - Keep writing the old columns, so queries keep working.
2. **Queries.** Compile against field values and the generated vector. Apply the matching
   rules above, then check ranking against the examples below.
3. **Explanation.**
   - Add the explainer and its property test.
   - Replace `title_match` with match evidence in the domain hit and the API schema, and
     regenerate the web client.
   - Render the evidence in Discord, checking each screen against
     [the bot screen guide](../bot-screens.md).
4. **Triggers.** Replace the row-level search triggers with statement-level ones, and
   remove the worker's own fan-out.
5. **Cleanup.** Drop `fuzzy_text`, `tags`, `search_aliases`, the old vectors, and the old
   highlighter.

Bounded totals stay as planned in [Discord record search](discord-record-search.md):
show "200+" when a ranking branch reaches its cap.

## Verification

- `hidden enchant table` finds `#7684`, and the match is highlighted in its name.
- `title:"glass door"` excludes `#3292`.
- `fastest 2x2 (glass OR door)` highlights every satisfied term.
- `no furnaces` finds `result:632822` and names holder `#8329` as the match.
- `entityless` and `restriction:entityless` find **Fastest 2x2 Glass Door · No Entities**
  through the category. The unrestricted variant does not match.
- In Builds, `orientation:trapdoor` and `extender_length>=4` return builds.
- `record_state:retired` finds a retired record, and its key survives revival.
- Editing a build that holds 509 records writes no record documents unless their content
  changed.
