# Make `SquidError` the only error vocabulary in application and domain code

## Context

Error `b03322f1d85e` was reported as a bug: `/admin records-lookup` on an unmatched category showed
a generic "Something went wrong" card, logged a full traceback at error level, and filed an error
report — for an entirely expected outcome. The cause is one line
(`squid/records/application/services.py:303` raising a bare `ValueError`), but the class of defect is
systemic.

Both presenters classify by exception type and nothing else:

- `build_error_presentation` (`squid/bot/errors.py:173-233`) renders friendly, localized text only for
  `DomainError` and discord.py's own error families. Everything else falls to the generic bucket,
  which also logs at error level and files a report.
- `handle_squid_error` (`squid/api/errors.py:176-221`) renders full problem details for `DomainError`,
  redacts non-domain `SquidError`, and hands anything else to `handle_unexpected_error` as a 500.

So an exception's base class *is* its user-facing contract. The hierarchy in `squid/core/errors.py`
is well designed and complete — the problem is purely adoption:

- **230 bare builtin raises** (211 `ValueError`, 17 `TypeError`, 2 `RuntimeError`) across **42 files**
  under `squid/*/application/**`, `squid/*/domain/**`, and the packages that flatten a layer into a
  single `application.py`/`domain.py`. Every one is invisible to both presenters.
- **~24 exception classes off the hierarchy entirely**, most notably the `cli_auth` and
  `minecraft_auth` families, which sit on plain `RuntimeError` and are hand-mapped to problem details
  by two near-identical `_raise_transport_error` helpers.

Intended outcome: application and domain code can only raise `SquidError` subclasses, that rule is
mechanically enforced so it cannot regress, and the existing violations are retired package by
package.

A key enabler: `ValidationError` is a `ValueError`, `NotFoundError` is a `LookupError`, and
`ConflictError` is a `RuntimeError` (`squid/core/errors.py:165-186`). Those mixins exist precisely so
this migration is backward compatible — most of the 69 `pytest.raises(<builtin>)` assertions in
`tests/` keep passing untouched.

## Classification rule

Every converted site picks one of three, by *who caused it*:

| Cause | Class | Presenter behaviour |
|---|---|---|
| Bad user input, missing resource, state conflict | `ValidationError` / `NotFoundError` / `ConflictError` | Friendly localized message, no error report |
| Caller-contract violation (`limit < 1`, bad enum for the code path) | `InvalidStateError` | Generic card + report — correct, it's a bug |
| Persisted data violates an invariant | `DataIntegrityError` | Generic card + report — correct, it's a bug |

The second and third columns are the point: converting an internal invariant to `InvalidStateError`
does not change what the user sees, but it *does* put the failure inside the `SquidError` vocabulary
so the rule stays total and AST-checkable. No judgement about "is this reachable" is needed.

Per the translation decision, **every** converted site gets an `_()` msgid plus `message_params`
rather than an f-string, including internal ones, so `just extract` picks them all up consistently.

## Phase 1 — Enforcement — **DONE**

`test_application_and_domain_layers_raise_only_structured_errors` in
`tests/architecture/test_boundaries.py`, following the existing AST-based rules in that module.

- Walks `squid/*/application/**`, `squid/*/domain/**`, and the packages that flatten a layer into a
  single `application.py`/`domain.py`/`services.py`.
- Flags `raise <Name>(...)` where `<Name>` is a builtin exception.
- Allows `AssertionError` and `NotImplementedError` (programming errors and Protocol stubs), and bare
  `raise` re-raises.
- `BARE_RAISE_ALLOWLIST` is a `dict[str, int]` ratchet over the 42 pre-existing offenders. It fires in
  three directions, all verified: a **new offending file**, a **new raise inside an already-listed
  file** (the pinned count), and a **stale entry** left behind after a migration. Each Phase 2 commit
  lowers or deletes entries; the rule is done when the dict is empty.

Ruff cannot express this — `TRY002` only covers vanilla `Exception`.

## Phase 2 — Migrate by package, one commit each

Ordered by user-visible payoff. Highest-value first:

1. **`squid/records/`** — includes the reported bug. `services.py:303` becomes a new
   `NoMatchingRecordCategoryError(NotFoundError)` in `squid/records/errors.py`, following the shape of
   the existing `RecordNotFoundError:7-22` (`default_message`/`default_title`/`default_code`/
   `default_resource` classvars, `context` + `public_context` in `__init__`). The `_base_key` and
   `_category_with_restrictions` invariants at `:460`/`:487` become `DataIntegrityError`; the
   unsupported-`kind` guards at `:496`/`:511` become `InvalidStateError`.
2. **`squid/tags/application/services.py`** (10 sites) — almost entirely user-input validation
   (`tag {id} does not exist` → `TagNotFoundError`, the value-type guards → `ValidationError`).
3. **`squid/submissions/`** (~56 sites across `domain/forms.py`, `domain/finalization.py`,
   `application/finalization.py`, `application/drafts.py`) — the largest package; split further if the
   diff gets unreviewable.
4. **`squid/media/`, `squid/notifications/`, `squid/starboard/`, `squid/search/`, remainder.**

Each package already has, or gets, a `squid/<package>/errors.py` — that convention exists in 17
packages already.

## Phase 3 — Fold the off-hierarchy classes in

`squid/core/errors.py` is transport-neutral (stdlib + `squid.core.i18n` only, enforced by
`test_exception_model_imports_no_transport`), so rebasing these introduces no coupling.

**`cli_auth` (12 classes) and `minecraft_auth` (12 classes)** — rebase each class onto the
`DomainError` subclass its `_raise_transport_error` branch currently maps it to, then delete both
helpers (`squid/api/v1/cli_auth.py:361-386`, `squid/api/v1/minecraft_auth.py:463-484`) and the
`_execute` wrappers that call them. Two details must be preserved:

- The `code` classvar (`"cli_authorization_pending"` etc.) currently reaches clients as
  `public_context={"cli_auth_code": ...}`. Keep it by having the package base class populate
  `public_context` from `code` in `__init__`. Asserted by
  `tests/unit/cli_auth/test_api_contract.py:309` and `tests/unit/minecraft_auth/test_api_contract.py:451`.
- `_RATE_LIMIT_RETRY_SECONDS = 60` is duplicated in both API modules; it moves onto the two
  `TooManyActive*Error` classes, which become `RateLimitedError` subclasses.

**Others**, changing base class only: the 5 `RuntimeError`s in `squid/media/application/jobs.py:254-286`
(move to the existing `squid/media/errors.py`), `squid/artifacts/infrastructure.py:20,24`,
`squid/events/application.py:48`, `squid/idempotency/infrastructure/crypto.py:16,20`,
`squid/submissions/application/finalization.py:117`, `squid/bot/utils/uploads.py:14`.

**Deliberately excluded** — pure control-flow signals with their own handlers, not failures:
`IdempotencyReplay` (`squid/api/idempotency.py:30`, registered at `squid/api/errors.py:301`) and
`FrameStreamClosed` (`squid/schematics/infrastructure/wire.py:73`).

## Verification

- Per package during Phase 2: the package's own unit tests with `--no-cov`, e.g.
  `uv run pytest tests/unit/records tests/unit/tags --no-cov`.
- The new rule plus the existing suite it joins: `uv run pytest tests/architecture --no-cov`.
- Phase 3 contract tests, which pin the public problem-detail shape:
  `uv run pytest tests/unit/cli_auth/test_api_contract.py tests/unit/minecraft_auth/test_api_contract.py --no-cov`.
- `uv run pytest tests/unit/api/ --no-cov` for the problem-details handlers, and the bot error
  presenter tests, since Phase 3 changes which branch several errors take.
- `just extract` after each package, to confirm new `_()` msgids reach `locales/squid.pot` and that
  the diff contains no stray f-string messages.
- `just typecheck` once after the final edit, compared against a pre-change baseline (the tree is not
  at zero errors — see the known-failing-checks note).
- Full suite deferred to CI, except after Phase 3, whose blast radius spans both transports.

## Follow-up

Once Phase 2 empties the allowlist, close out `BUGS.md` — the `b03322f1d85e` entry added this session
describes the single-site symptom and should be replaced by a pointer to the systemic fix, matching
how the previous six entries were retired in `915f8c4a`.
