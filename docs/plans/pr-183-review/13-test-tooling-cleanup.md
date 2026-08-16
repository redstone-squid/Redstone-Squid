# Test and tooling review follow-up

## Review findings

The review identified repeated Discord mocks, tautological model tests, large repository tests,
registry mutation in taxonomy tests, repetitive import-boundary assertions, and assertions whose
failure output is hard to read. These should not become blanket rules: direct SQL is appropriate
when an integration test is proving a database constraint or migration, and third-party
instrumentation belongs in a test when Squid depends on the resulting composition.

Later commits have already improved typing, added mapper round-trip coverage, and aligned stale
expectations. Every original test comment therefore needs reassessment against current HEAD before
deleting coverage.

## Planned work

1. Build small typed factories for Discord interactions, messages, channels, and component views;
   migrate only tests that currently duplicate fragile mock setup.
2. Replace taxonomy tests that mutate global registries with isolated bot/command-tree fixtures or
   assertions over the exported taxonomy contract.
3. Remove tests that only instantiate a declarative model and read back assigned values. Replace
   them with database round trips that prove server defaults, timezone conversion, constraints, or
   mapper behavior.
4. Consolidate the repeated Nucleation import checks into one parameterized architecture test while
   preserving distinct domain, application, and process-boundary failure messages.
5. Extract scenario builders and result assertions from long voting and schematic integration
   tests. Keep setup local when a helper would hide the behavior being tested.
6. Keep migration SQL assertions where they verify upgrade behavior; avoid raw SQL merely as a
   second implementation of repository setup. Revert migration-test changes only if they weaken
   the invariant coverage or break historical upgrade testing.
7. Prefer whole-value or structured comparisons over chains of weak membership assertions, and
   include useful IDs or payloads in failures from worker/process tests.
8. Classify each low-value test as delete, strengthen, or retain-with-rationale, then close the
   matching review thread with that disposition.
9. Replace the hand-maintained entity counts in `squid/persistence/alembic_entities.py` with a
   self-checking parse assertion, and remove the bump instructions the counts forced into two other
   plan documents. Detailed in [the dispositions](13-test-tooling-dispositions.md#tooling); this is
   the one thread here that lands on a source file rather than a test.

## Validation

- Run the focused unit or integration module after each cleanup; test-only commits must not reduce
  coverage of a named product invariant.
- Run architecture tests after fixture/import-surface changes and `alembic heads` plus migration
  integration tests after migration-harness changes.
- Run changed-file Ruff and BasedPyright checks and `git diff --check` before each reviewable
  commit.
