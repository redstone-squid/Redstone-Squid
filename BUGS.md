# BUGS

## `admin records-lookup` on an unmatched category dumps a raw traceback instead of a friendly error

Asking `/admin records-lookup` to materialize a `kind`/`base_key`/`restrictions` combination that
no confirmed build satisfies crashes with an opaque "Something went wrong" card and logs a full
traceback as an application error, even though this is an entirely expected outcome of an admin
mistyping a category.

```
Error b03322f1d85e — 17 August 2026 15:06 — admin records-lookup
ValueError: No confirmed build satisfies the requested record category.
  File "squid/bot/submission/records.py", line 148, in lookup
    summary = await self.records.lookup_or_materialize(
  File "squid/records/application/services.py", line 303, in lookup_or_materialize
    raise ValueError(msg)
```

**Root cause**

- `RecordComputationService.lookup_or_materialize` (`squid/records/application/services.py:292-310`)
  raises a bare `ValueError` at line 303 when `_category_has_candidate` finds no confirmed
  candidate matching the requested `kind`/`base_key`/`restriction_ids`/`version_id`. This is
  reachable directly from user-supplied slash-command arguments (`squid/bot/submission/records.py:148`),
  not an internal invariant violation.
- `build_error_presentation` (`squid/bot/errors.py:173-233`) only renders a friendly, localized
  message for exceptions it recognizes: `DomainError` (and subclasses), discord.py's own
  `UserInputError`/`CheckFailure` family, etc. A plain `ValueError` matches none of those branches,
  so it falls through to the generic "Something went wrong" bucket — which also logs the full
  traceback at error level and files an error report, treating an expected "no match" result the
  same as an unanticipated crash.
- The codebase already has the right base class for this: `ValidationError` and `NotFoundError`
  (`squid/core/errors.py:165-180`) are both `DomainError` subclasses (so they render nicely) that
  are also `ValueError`/`LookupError` subclasses (so existing `except ValueError` callers, if any,
  keep working). `squid/records/errors.py` already follows this pattern for
  `RecordNotFoundError`, but `lookup_or_materialize` was never converted to use it.

**Suggested direction**

Add a records-domain error (e.g. `NoMatchingCandidateError(NotFoundError)` or
`ValidationError` with `ErrorCode.RECORD_NOT_FOUND`/`ErrorCode.VALIDATION_ERROR`) in
`squid/records/errors.py`, following the `RecordNotFoundError` shape, and raise it instead of the
bare `ValueError` at `services.py:303`. That routes the failure through
`build_error_presentation`'s `DomainError` branch so the admin sees a localized "no build matches
this category" message instead of a generic crash card, without it being logged/reported as an
unexpected error.

This site is one of 230 bare builtin raises in application and domain code, all of which are
invisible to both error presenters for the same reason. `docs/plans/structured-errors.md` covers the
systemic fix; the architecture rule that stops new ones landing is already in place.
