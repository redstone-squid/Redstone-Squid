# Test and tooling review: thread dispositions

> These dispositions close the original PR #183 threads. The broader namespace, mock, snapshot,
> duplication, and quarantine cleanup is tracked in
> [the workspace test-suite audit](../test-suite-audit.md); its final disposition takes precedence
> for tests retained here as historical follow-up.

Every `tests/` comment `Glinte` left on [PR #183](https://github.com/redstone-squid/Redstone-Squid/pull/183)
at or before the `5edfd3e` cutoff, with the disposition that closes it, plus the one developer-tooling
thread on a source file that the original cluster split left unassigned (see
[Tooling](#tooling)). Four dispositions are in use, per `README.md`: **fixed** by this work,
**already fixed** after `5edfd3e`, **retained** with a rationale now recorded in the test, or
**deferred** to a named plan.

CodeQL's two bot comments on `tests/` are out of scope: they are not review comments by `Glinte`.

| # | File | Comment | Disposition |
|---|---|---|---|
| 3766216496 | `unit/bot/test_command_taxonomy.py` | "bad test tbh" (`EXPECTED_PREFIX_COMMAND_TREE`) | **Retained.** The command surface is a public interface and both directions of drift are otherwise silent. Rationale recorded on the constant and the test. |
| 3766220759 | `unit/persistence/test_timestamp_models.py` | "obviously this will pass, and failures are always boring" | **Fixed.** The hand-maintained list of 21 columns became a sweep of `Base.registry`, which fails for any column mapped to a bare `datetime`, including ones added after this. |
| 3766332672 | `architecture/test_import_surfaces.py` | "duplicating the nucleation test a stupid amount of times" | **Already fixed**, then improved. The cases were parameterised before this plan; the failure output was not, so a leak reported only a non-zero exit code. The child now reports which module leaked. |
| 3766500676 | `unit/bot/test_components_v2_ui.py` | "bad asseerts tbh" | **Fixed.** `[17, 1, 1, 1]` became named `discord.ComponentType` members, and the bare button count became a label comparison. |
| 3771060757 | `unit/bot/submission/test_attachments.py` | "actually we need to verify this claim" | **Fixed.** The absolute claim ("no content type at all") was narrowed to the two shapes actually observed in production, which is also exactly what the design needs and what the two parametrised cases pin. |
| 3771235233 | `integration/schematics/test_worker_pool.py` | "annoying assert" | **Fixed.** The pinned `nucleation-0.10.1` literal is derived from the installed distribution, so the assertion states its real invariant - the worker loaded the engine this environment installed - instead of failing on every bump. |
| 3775316974 | `integration/voting/.../test_vote_repository.py` | "having SQL in tests is bad" | **Fixed.** 80 lines of hand-written DDL replaced by the model metadata, via `tests/support/schema.py`. The account and guild foreign keys the copy had dropped are now real. Read-back `SELECT`s stay: they are the assertion, not a second setup path. |
| 3775329634 | `integration/voting/.../test_vote_repository.py` | "such a long test we should have helpers" | **Fixed.** `attach_vote_message` and `seed_generic_poll` extracted; every value the test asserts on stays in the test body. |
| 3775333078 | `integration/test_alembic_migrations.py` | "revert" | **Retained.** Reverting would delete the only coverage of a downgrade path, which never runs in production and so is the likeliest to be wrong - the plan's stated bar for reverting is not met. The pinned predecessor revision, which was the real maintenance smell, is now expressed relative to the migration under test. |
| 3775471758 | `unit/bot/test_command_taxonomy.py` | "hacky as fuck, but maybe justified? needs careful argument" | **Already fixed.** `_check_names` is gone; `_commands_of` carries the argument for reading a cog's tree through `__new__`. |
| 3775474298 | `unit/bot/test_command_taxonomy.py` | "no registries in tests" | **Already fixed.** The tier test was replaced by `test_sensitive_commands_declare_the_intended_permission_nodes`, which reads nodes off the check predicates. |
| 3779990071 | `architecture/test_discord_components_v2.py` | "hard coded" | **Fixed.** `owners == ["reactions.py"] * 4` became a name-per-file mapping, so a lost listener and a stray one in a cog are different failures. |
| 3779992042 | `unit/bot/test_reactions.py` | "subclass protocol" | **Fixed.** The test double subclasses `ReactionSubscriber` and marks its methods `@override`. |
| 3779995719 | `unit/bot/test_reactions.py` | "we probably need helpers" | **Fixed.** `make_reaction_payload` and `make_reaction_bot` live in `tests/support/discord.py`; the payload is a real `RawReactionActionEvent` and the five `arg-type` suppressions collapsed to one cast in the helper. |
| 3780229351 | `unit/bot/test_log.py` | "useless tests, remove the whole file" | **Fixed**, by the second half of the instruction. The `cog.log` wrapper test is gone; the privacy test now compares the whole `squid.*` field set, which is what actually proves arguments stay out, and the two listeners with real branching are covered. |
| 3783323740 | `unit/bot/test_app_main.py` | "refactor the bootstrap to not have a forked process… ugliest test ive ever seen" | **Deferred** to [12-runtime-observability.md](12-runtime-observability.md). The test is ugly because the bootstrap forks; rewriting the test without changing that would only move the ugliness. The process model is a runtime decision, not a test cleanup. |
| 3783391530 | `unit/test_config.py` | "not part of inert" | **Fixed.** A default `sample_ratio` of 1.0 says nothing about whether anything is exported. Split into the two separate claims it was conflating. |
| 3783545454 | `integration/observability/test_traces.py` | "i dont really see why we are testing third party code" | **Retained.** Four of its five claims are Squid functions - `correlation_id`, `TraceContextFilter`, `inject_trace_context`, `extracted_trace_span` - whose behaviour exists only once composed with the SDK, and whose unit tests can only assert against a mocked tracer. Rationale recorded in the module docstring. |
| 3783548057 | `unit/api/test_app.py` | "no." | **Fixed by deletion.** `test_create_app_delegates_optional_instrumentation` asserted that one function calls another and would have passed with instrumentation entirely broken. The real behaviour is covered against a live SDK in `test_traces.py`. |
| 3783550978 | `unit/test_observability.py` | "no" | **Fixed by deletion**, in part. `test_correlation_id_uses_active_trace_id` mocked the private trace lookup and asserted the value came back; deleted. The untraced fallback is kept and strengthened, because `build_error_presentation` shows that id to users. |
| 3783707542 | `unit/bot/test_errors.py` | "we really need centralized utils for discord mocks" | **Already fixed.** `test_errors.py` uses `tests/support/discord.py`; the workspace audit kept genuinely shared support there and context-specific fakes beside their tests. |
| 3784077161 | `unit/test_config.py` | "maybe don't duplicate the string" | **Fixed.** The anchored line (`cursor_secret`) was retired in `3605d011`; the same pattern in the API pepper and idempotency key id now reads from `BASE_ENVIRONMENT`. |
| 3784078799 | `unit/test_config.py` | "bad test" | **Fixed.** The exact set of required config groups churned with every new required setting while proving only that several fields were named. Now asserts the property that matters - errors aggregate rather than stopping at the first - with a lower bound on the groups. |
| 3788032262 | `unit/api/test_phase2_reads.py` | "subclass properly" | **Fixed.** Five duck-typed fakes now subclass the services they replace with `@override`. This surfaced one fake modelling a contract the service does not have: `render_content` returns `bytes` and raises on an unknown hash. |

## Tooling

One thread lands on a source file rather than on a test, and no plan claimed it in the original
cluster split. It is developer tooling for migration authoring, so it belongs here.

| # | File | Comment | Disposition |
|---|---|---|---|
| 3782845586 | `squid/persistence/alembic_entities.py:20` | "remove this crap" (`if len(_FUNCTION_SQL) != 11 or len(_TRIGGER_SQL) != 23`) | **Fixed by replacing the numbers, not by deleting the check.** The counts become a self-checking totality assertion that never needs bumping. |

The guard is worth keeping in some form. `postgres_entities.sql` *is* the declared set: whatever the
two regexes fail to parse is absent from `ALEMBIC_UTIL_ENTITIES`, and therefore absent from the
comparison `alembic-utils` makes during autogenerate. A `CREATE FUNCTION` whose body does not end in
`$$;` at the start of a line, or a `CREATE TRIGGER` broken across lines, would drop out silently.

What was wrong was the guard's form. Two hand-maintained magic numbers had been bumped across 15
commits, and two plan documents instructed the next author to bump them again
(`docs/plans/rbac.md:301`, `docs/plans/durable-queues.md:64-65`). This has landed:
`parse_entities(sql)` in `squid/persistence/alembic_entities.py` now takes the SQL as an argument
(read from disk only by the cached `alembic_util_entities()`, not at import time) and raises when
`EXPECTED_FUNCTIONS`/`EXPECTED_TRIGGERS` disagree with what the regexes actually parsed —
```python
functions = re.findall(r"^CREATE FUNCTION .*?\$\$;", sql, flags=re.MULTILINE | re.DOTALL)
triggers = re.findall(r"^CREATE TRIGGER .*?;$", sql, flags=re.MULTILINE)
if len(functions) != EXPECTED_FUNCTIONS or len(triggers) != EXPECTED_TRIGGERS:
    raise RuntimeError(...)
```
`tests/unit/persistence/test_alembic_entities.py` covers a well-formed definition, each direction of
a miscount, an unterminated `$$` body, and the shipped `postgres_entities.sql` itself — the malformed
cases the old import-time constant could never be fed.

`tests/integration/test_alembic_migrations.py:218-222` already compares the parsed triggers against
the live database (`trigger_names == expected_triggers`), so a missed statement would eventually
fail there too — but only with Docker and Postgres, whereas the import-time check fails in every
process the moment the file is edited. Both are worth having.

The three documentation fixes that were meant to ship with it have now landed: `docs/plans/rbac.md`
and `docs/plans/durable-queues.md` no longer instruct the next author to bump hand-maintained
numbers — they point at the `EXPECTED_FUNCTIONS`/`EXPECTED_TRIGGERS` constants instead and record the
current count (12/38) as a snapshot, not an instruction — and `docs/new-migration.md:13` now points
at `squid/persistence/postgres_entities.sql`.

## Not done here

- `tests/unit/media/test_jobs.py` (884 lines) and `tests/integration/builds/test_submission_targets.py`
  (727) are the two largest test modules and neither drew a review comment. Plan item 5 is satisfied
  for the modules that did; splitting these on size alone is not something the review asked for.
- The `SimpleNamespace`-as-service pattern appears in roughly twenty other test modules. Only
  `test_phase2_reads.py` was flagged, and only it was converted; the rest is a follow-up worth doing
  once, not a change to smuggle into this plan.

## Verification status

Unit-level changes are checked by inspection and by type checking only: this working environment has
no installed dependency set, and building the project's Rust-backed wheels on it does not complete.
The two integration modules touched - `test_vote_repository.py` and `test_alembic_migrations.py` -
need Docker and a Postgres testcontainer, so both need a CI run before these threads are closed.

Replying on GitHub and resolving the threads still requires separate explicit authorization, per
`README.md`.
