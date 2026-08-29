# Testing

The suite is organized first by test type and then by bounded context:

```text
tests/
├── architecture/                 # executable dependency rules
├── unit/<context>/domain/        # entities, values, policies, and properties
├── unit/<context>/application/   # use cases with narrow fakes
├── integration/<context>/infrastructure/
├── e2e/<context>/                # adapters composed with in-process dependencies
├── external/                     # opt-in live services
├── support/                      # only genuinely shared builders and fakes
└── fuzz/                         # standalone harnesses and seed corpora
```

Not every target directory exists yet. Add one when it receives its first real test; do not add placeholder tests.

## Commands

```bash
just test                 # unit and architecture; no Docker or credentials
just test-integration     # includes ephemeral PostgreSQL
just test-all             # unit, architecture, and integration
pytest                    # same fast paths selected by pyproject.toml
```

PostgreSQL integration tests require a working Docker daemon. Live Supabase, Discord, Mojang, OpenAI, and other
third-party tests belong under `external` and must require an explicit opt-in; they are not part of `test-all`.

On Linux or WSL, run the fuzzers with:

```bash
just fuzz-version          # Minecraft version string parsing
just fuzz-cursor           # search cursor codec decoding
just fuzz-search-parser    # search query language parsing
just fuzz-target target=cursor_codec seconds=20
```

Each command copies its committed seeds into the ignored `.fuzz/` workspace so an evolving corpus does not dirty the
repository. A crash artifact must become a deterministic regression test or a minimized committed corpus seed before
the bug is considered fixed.

Local recipes default to 20 seconds. The generic recipe accepts only allowlisted targets and bounded integer budgets;
runs longer than five minutes also require `--allow-long-run`.

Fuzz targets are pure, synchronous, in-process functions that accept untrusted text directly from a Discord command
or an API request: parsers and codecs, not the handlers around them. Handlers themselves are async and do I/O, which
breaks the fast, deterministic, coverage-guided loop fuzzing depends on.

API exploration is a maintained standalone campaign, not part of `just test`. `just fuzz-api-smoke`
starts the disposable API stack and runs the pinned Schemathesis CLI with a bounded generated-example
and wall-clock budget. `tests/unit/fuzz/` tests the launcher, safety policy, event classification, and
applicability manifest deterministically; `tests/integration/fuzz/` tests the disposable stack lifecycle.
The old in-process Schemathesis experiment was removed: it hung during collection and duplicated a
less faithful transport topology without exercising the maintained campaign lifecycle.

## What to test

Domain tests are synchronous and cover invariants, state transitions, value objects, and domain events. They do not
import SQLAlchemy, Advanced Alchemy, Discord, FastAPI, or Supabase.

Application tests cover use-case decisions and orchestration. Their dependencies are narrow protocols expressed in
domain terms. Use a small stateful fake when its state matters; use `Mock` or `AsyncMock` only for a narrow interaction
boundary.

Infrastructure tests cover behavior owned by Squid:

- Custom queries, filters, ordering, projections, and loading strategies.
- Persistence-to-domain mapping.
- Overridden repository behavior.
- Database constraints, relationships, cascades, and PostgreSQL-specific expressions.
- Multi-repository transactions, locks, upserts, idempotency, and races.
- Regressions for an observed integration or configuration defect.

Advanced Alchemy stays in infrastructure. Do not test its inherited `add`, `get`, `list`, `update`, `delete`, bulk
operations, pagination, or standard exceptions unless Squid changes the behavior. Application ports must not mirror
the library's generic CRUD surface.

## How to write tests

- Prefer test functions. Use classes only for cohesive behavior grouping or a Hypothesis state machine.
- Assert public behavior and observable state, not private attributes or incidental call order.
- Give each test one behavioral reason to fail and name it as the behavior and outcome.
- Use typed factory functions for ordinary objects. Fixtures are for lifecycles such as containers, sessions,
  transactions, clients, environment restoration, and temporary files.
- Keep context-specific builders and fakes beside their tests until multiple modules genuinely share them.
- Build valid objects by default. Construct invalid state visibly in the test that exercises it.
- Use Hypothesis for invariants and round trips, with bounded strategies that reflect the domain or storage type.
- Keep tests deterministic by injecting clocks, ID generators, randomness, and external gateways.
- Never replace PostgreSQL with SQLite when testing PostgreSQL adapter behavior.
- Every skip or xfail must explain the external limitation or link to tracked work; xfails remain strict.
- Treat coverage as diagnostic during the DDD migration. A percentage is not a substitute for testing important
  behavior.

## Architecture migration

The enforced rules keep every context's domain and application layers independent from transport and persistence.
Domain and application code cannot import `advanced_alchemy`, `sqlalchemy`, `discord`, `fastapi`, `supabase`, or
concrete infrastructure packages.
