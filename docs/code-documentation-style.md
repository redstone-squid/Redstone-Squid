# Comments and docstrings

Write for an engineer who knows the language but has not worked on this part of
Redstone Squid. Help them use the code and understand the decisions behind it.
Read the implementation and its callers before describing what it guarantees.
Judge a change by how well readers can find and understand what they need, not
by how many words or comments it removes.

## Choose what to explain

A docstring describes an interface: what it does, what callers must supply, and
what they can expect. A comment can explain a decision, mark a meaningful phase,
or connect several steps to their purpose. Put it where a reader will need it.

- Describe a module's purpose and any rules that apply across it. Do not repeat
  its file name or list every definition.
- Describe what a class represents. Explain its lifetime, ownership, or allowed
  state changes when these affect how it is used.
- Describe a function's result and important side effects. Cover units, ordering,
  missing values, errors, or transaction requirements when callers need them.
- Document attributes when their meaning is not clear from their name and type.
  Explain what `None`, an empty collection, or a sentinel means where it matters.
- Explain surprising branches, workarounds, and constraints in comments. State
  the reason, not just the operation.
- In tests, explain unusual setup or the failure being prevented. A comment that
  merely repeats a descriptive test name adds little.

Look for missing explanations as well as weak existing prose. Leave accurate,
useful text alone. Remove comments that merely narrate individual assignments,
loops, or returns. Keep licenses, tool directives, and references needed to
understand or remove a workaround.

In a long function, short section comments can help readers find a particular
phase without tracing every statement. For example, `# Check foreign-key targets`
can introduce a block that resolves references, checks tables and columns, and
reports missing targets. Its purpose is navigation, even if the code eventually
makes that purpose clear. Keep useful signposts near their blocks; avoid a
separate outline that repeats the whole function.

## Introduce the concepts before the machinery

Assume knowledge of the language and common engineering ideas, but explain this
subsystem's vocabulary and relationships. Knowing what a transaction is does not
tell a reader what a voting session contains or why several messages refer to it.
Give complex modules and classes enough context to answer these questions:

- What problem does this solve, and who uses it?
- What do its main concepts represent, and how do they relate?
- What happens in the ordinary case, from input to result?
- What must stay true, and which component is responsible for it?

Use these questions to find missing context, not as mandatory docstring headings.
An explanation usually works best in that order, with exceptions and implementation
details after the behavior they qualify. Define a term before using it to explain
another term. A precedence tuple is useful shorthand once its parts have been
explained; it is not an introduction to the permission system.

Match the explanation to the abstraction. An interface should describe the
behavior callers can rely on. Its implementation can explain how a database lock
provides that behavior. Name the component that authorizes, commits, publishes,
or retries when that responsibility matters; "the caller handles it" can leave
the reader searching for the missing half of the explanation.

Connect a mechanism to its reason and observable consequence. "Mutations serialize
on the session row" leaves out why that matters. Explain that updates to one voting
session run one at a time so each update checks the latest stored tally before
deciding whether to close the vote. Keep the technical term when it is useful,
but do not make it carry the whole explanation.

## Use consistent terminology

Use the same term for the same concept across a subsystem's interfaces,
implementations, tests, and examples. Do not introduce synonyms just for variety:
readers may reasonably assume that different names describe different things.
Prefer established domain terms and connect them to the identifiers used in code.

Keep distinct concepts distinct. For example, qualify "voting session" and
"database session" when both appear in an explanation. When a domain term differs
from a storage or transport name, explain the correspondence once rather than
silently switching between them. Do not force different subsystems to share a
term when their meanings differ.

Define shared vocabulary near the domain types or module that owns the concepts.
Before reviewing a subsystem across several files, agree on those terms and
check that each explanation uses them consistently. If existing names conflict,
make the distinction explicit; a prose rewrite should not silently redefine an
identifier or require an unrelated code rename.

## Give the reader enough space

Use a one-line docstring for a simple operation. A difficult algorithm or a
resource with a complex lifetime may need several paragraphs and an example.
Length follows the reader's needs; there is no target number of lines.

Start with a sentence that stands on its own. For a longer docstring, leave a
blank line after that sentence. Explain the common case before the exceptions.
Use Google-style `Args:`, `Returns:`, `Yields:`, or `Raises:` sections when they
make details easier to find. Do not add empty sections or repeat type annotations.
Use triple double quotes in Python and keep lines within the project's
120-character limit.

Choose the format for the information:

- Use bullets for independent checks, conditions, or guarantees that readers may
  need to find individually.
- Use a numbered list when order matters.
- Use prose to explain reasons and connections.

When correcting a list's claims, preserve its useful structure rather than
automatically turning it into a paragraph.

These invented examples show different amounts of detail:

```python
def cached_ids(self) -> set[int]:
    """Return a copy of the cached build IDs."""
```

```python
async def claim_next(self, *, lease_seconds: float) -> Job | None:
    """Reserve the oldest available job until its lease expires.

    The caller must commit the transaction before starting work. Other workers
    skip the reserved job while its lease is valid. If the worker stops without
    finishing, the job becomes available again after the lease expires.

    Returns:
        The reserved job, or None if no job is available.
    """
```

Use examples for behavior that prose alone leaves hard to picture: a parser's
accepted syntax, a sequence of state changes, or a subtle distinction between
two inputs. Choose examples that distinguish plausible interpretations: show
which of two conflicting permission rules wins, rather than only a successful
permission check. Keep examples small and verify them against the implementation.

## Use direct language

Name the actor and the action. Prefer familiar words, but keep technical terms
when they are the clearest way to describe a concept. Explain unfamiliar domain
terms when a reader first needs them.

Do not squeeze several ideas into a string of nouns. For example, replace
“Apply stale-owner completion fencing” with “Ignore the result if another worker
has claimed the job.” If the reader also needs to know how ownership is checked,
explain that in a second sentence.

Avoid praise such as “robust,” “seamless,” or “elegant.” State the behavior that
earns the claim. Avoid filler such as “This method is responsible for.” Use
ordinary sentences; do not force every explanation into a terse fragment.
Make the prose pleasant through useful detail and clear examples, without jokes
that distract from the code.

For example, this comment explains a reason the operation alone cannot show:

```python
# Sort before hashing so a change in query order does not invalidate the cache.
```

“Sort the values” would only repeat the code.

## Check the explanation

Verify claims about retries, cancellation, locking, permissions, and persistence
against all relevant branches. Distinguish what this function guarantees from
what its caller must arrange. Avoid absolute claims such as “always” unless the
code supports them. If behavior is unclear, investigate it instead of guessing.

Make broad terms such as "normalize," "valid," and "matching" concrete when the
distinction affects callers. For a matcher, explain whether matching is exact or
by substring, whether order decides the result, and what happens when nothing
matches. A small input/output example can reveal a surprising rule: a substring
matcher may map `bigint` to `integer` because it finds `int` first.

A technically true summary that hides an important distinction is not enough.
Describe current limitations without presenting them as intended design, and
flag suspected bugs separately.

Keep current reasons and constraints; remove stories about earlier rewrites
unless they explain a decision that still matters. Do not copy a long explanation
across wrappers and implementations. Put the contract at the interface and
describe implementation-specific details beside the implementation. Briefly
repeat a prerequisite when readers need it locally, and point to a specific
symbol for the fuller explanation. Avoid making readers follow several links
just to understand an ordinary call.

Some documentation is consumed by programs. In this repository, model docstrings
can become database comments, and command docstrings can become help text. Check
those consumers before editing. Preserve examples used by tests, migration
metadata, generated-file notices, and type-checker suppressions. Edit generators
instead of hand-editing generated output.

Review the final diff for accuracy, readability, and ease of navigation. Compare
the old and new explanations: retain useful examples, reasons, and relationships
even when their wording needs work. Restore a better original passage when a
rewrite has lost something; new wording is not automatically better.

Then read the explanation before the implementation, as someone new to the
subsystem would. Can that reader describe the normal flow, predict the example's
result, and identify the responsibilities without guessing what the terms mean?
Use a reviewer unfamiliar with the edit when practical. Checking facts against
code and checking whether prose teaches the reader are separate review tasks.

Documentation changes should not accidentally change executable expressions or user-facing strings.
Run the relevant repository checks; passing tests support the review but cannot
prove that an explanation is true.

## Sources

This guide adapts [PEP 257](https://peps.python.org/pep-0257/) for docstring
structure and [Google's Python style guide](https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings)
for interface documentation and comments. We keep sections and detail only when
they help, rather than requiring an inventory of every symbol or parameter.
[Google's clear-sentence guidance](https://developers.google.com/tech-writing/one/clear-sentences)
informs the advice on concrete subjects and verbs. The examples and repository
checks above apply those principles to this project.

Examples worth studying in source code:

- [CPython's heapq](https://github.com/python/cpython/blob/main/Lib/heapq.py)
  introduces the heap invariant and uses detailed comments to explain algorithm
  choices, alongside short docstrings for simple operations.
- [Tokio's broadcast channel](https://github.com/tokio-rs/tokio/blob/master/tokio/src/sync/broadcast.rs)
  introduces senders and receivers before lag and closure. Its lock-order comment
  explains the deadlock that the ordering prevents.
- [SQLite's WAL implementation](https://github.com/sqlite/sqlite/blob/master/src/wal.c)
  defines frames and checkpoints before explaining how readers obtain a consistent
  view. Separate sections make the longer explanation navigable.

Borrow the explanatory techniques, not every project's formatting or amount of
detail. These are examples to learn from, not templates to reproduce everywhere.
