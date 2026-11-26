# Search index redesign

Status: implemented. Every match can be traced to the field and value that produced
it, and highlighting renders that evidence. A property test checks that the Python
explainer and the SQL predicate agree on which documents match.

## Problems it fixed

A search document kept its text in catch-all columns. `tags` mixed tag, creator and
version names, a record's `description` held its holders' build titles, and
`fuzzy_text` joined the rest. Matching ORed exact equality, `tsquery` and trigram
similarity across them, so quoted text still matched fuzzily (`title:"glass door"`
found **5x5 Door\* · Locational**, `#3292`). The highlighter guessed with its own
regex on the title and skipped OR branches. A record found through a holder's build
title could not say so.

Custom build names (4,697 builds), build orientation and extender fields were not
indexed. Records were keyed by result ID, so retired records fell out of search.
Every refresh rewrote the document and its GIN indexes, even when nothing had changed.

## Design

- **Documents are typed field values.** Each searchable fact is a `search_values` row:
  a field, a value, and for text a normalized form and a term vector. Record holder
  values carry the holder's build ID. Display data stays in the document and its
  `document_data`.
- **One registry.** A field declares whether a clause compares words or a whole
  keyword, and whether bare text searches it and at what weight. Category fields
  weigh A or B, creators C, and descriptions and holder fields D. Indexed-only fields,
  such as `holder_title`, have no query syntax.
- **One tokenizer.** Terms are runs of letters and digits after folding case, accents
  and `×`. Vectors are written as literals, so PostgreSQL indexes exactly the
  tokenizer's terms. A gap between values keeps a phrase within one value.
- **Matching.** Unquoted words also match close spellings: one edit for 4 to 7
  letters, two for longer words, never for terms with digits or under NOT. These
  come from a `search_terms` vocabulary, looked up once per search. Quoted text is
  an exact phrase. Keywords compare whole normalized values.
- **Ranking.** Reciprocal rank fusion combines a heading that names the query, a
  weighted full-text rank, records whose heading has every word (most general
  first), and semantic similarity. Candidates with equal scores share a rank.
- **Explanation.** `explain` evaluates the parsed query against a page's values with
  the compiler's rules, including SQL's unknown for absent holder measurements. Hits
  carry `SearchEvidence`. Discord highlights headings and subtitles, names the holder
  when a holder matched, shows another field's value when the heading shows nothing,
  and marks close spellings. The API returns the evidence.
- **Keys and coverage.** Records are keyed `record:<public ID>`. Retired records,
  former holders and ties are indexed, and retired records only appear for a query
  that mentions `record_state`. Records that never had competitors (52,322 in
  November 2026) are not indexed.
- **Writes.** A document whose hash is unchanged is not rewritten. Its embedding is
  kept while its embedding text, which leaves out holder values, is the same.
- **Queuing.** Record table triggers are statement-level, so a bulk change queues each
  record once. A trigger on `search_documents` queues the records that read a build or
  tag document when its title, status or data changes, replacing the worker's
  unconditional fan-out.

## Decisions made while building

- Steps 1, 2 and 5 of the plan shipped as one commit. Nothing read the old columns
  once the compiler moved, so there was no reason to keep writing them.
- Close matches do have spans. Matching is per token, so the matched token is known
  exactly.
- A leading criterion such as `fastest` is parsed as a filter on the record's
  criterion, not as a word, so the evidence names the criterion and the title's
  "Fastest" is not bold.
- The build, tag and credit triggers stay row-level. They are shared with Discord
  sync and record recompute, and their requests are keyed by resource, so a bulk
  change already queues each document once.
- Title suggestions keep trigram similarity on the normalized title. Autocomplete
  wants partial words, which token matching does not give.

## Follow-ups

- `title_holder_history` was empty in production on November 26, 2026, while
  13,345 current results existed. Former-holder search and the web's holder history
  have nothing to show until that is explained.
- The embedding worker takes 8 documents per poll. After the reindex, every document
  needs a new embedding.
