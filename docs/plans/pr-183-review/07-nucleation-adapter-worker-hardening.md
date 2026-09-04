# 07 — Nucleation Adapter, Wire, and Worker Hardening

## Findings

- The silent `or SchematicFormat.LITEMATIC` fallback remains and can persist a false source format. Reject an unresolved format or preserve an explicit unknown value; never label it litematic by default.
- Nucleation JSON is necessarily dynamic, but casts currently substitute for validation. Decode every external JSON document through narrow validators that reject malformed required fields and deliberately skip only documented optional evidence.
- `_optional()` still catches every exception (`squid/schematics/infrastructure/nucleation_adapter.py:403-420`). Upstream issues [#7](https://github.com/Schem-at/Nucleation/issues/7) and [#8](https://github.com/Schem-at/Nucleation/issues/8) are closed. **Citation correction:** Squid no longer pins `nucleation==0.10.1` — `pyproject.toml` now pins `nucleation==0.10.14` (bumped in `13a1adcc`, "schematics: align rendering with nucleation 0.10.14"). The `_optional()` docstring still cites the 0.10.1-era issue numbers and has not been re-verified against 0.10.14; reproduce against a clean 0.10.14 install and remove or narrow the workaround if fixed.
- Optional sign/lattice evidence may be dropped, but load-bearing analysis and conversion output must fail explicitly. Translate user-visible adapter failures through Squid errors/i18n at the application boundary rather than embedding arbitrary engine text in Discord responses.
- `FrameStreamClosed` is a private protocol condition, not a domain error, so it should remain an infrastructure exception. The questioned `asdict` casts are static-typing artifacts; remove them only through a typed JSON alias/helper, not by pretending `dict[str, object]` satisfies every mapping contract.
- Worker operation timeouts are now configurable, and the pool subtracts queue wait from the operation budget. Those review concerns are already fixed.

## Plan

1. Add typed JSON decoding helpers for adapter results and worker request headers, including exact tuple lengths, enum values, numeric ranges, payload-part totals, and base64 validation.
2. Remove the format fallback and make the already-vetted `source_format` required for ingestion analysis; keep format sniffing only for standalone adapter/test callers if needed.
3. Reproduce the two documented Nucleation mismatches on the currently pinned `0.10.14` (not `0.10.1` as originally written — the pin moved in `13a1adcc`). Remove resolved guards/comments; if behavior or current docs still disagree, file a new upstream issue with the pinned version, clean reproducer, observed cost, and record its issue number beside the narrow workaround.
4. Split optional-evidence recovery by operation and expected upstream exception. Preserve causes and structured developer context while keeping translated end-user messages at Squid boundaries.
5. Harden frame decoding before allocation/slicing, while leaving process crash/timeout translation in the supervisor.

## Tests

- Adapter integration fixtures for every supported input/output format, unknown source format, malformed engine JSON, malformed base64, absent optional metadata, and failed export.
- Wire tests for non-object headers, negative/oversized part lengths, mismatched body totals, invalid enums/vectors/RGBA values, and truncated frames.
- Worker-pool tests proving queue time consumes the configured deadline and each per-operation timeout is honored; retain current crash/respawn coverage.
- Run the self-contained upstream reproducers against a clean `nucleation==0.10.14` environment (the currently pinned version) and capture the result in the implementing commit or follow-up issue.

## Disposition

- **Fix:** false format fallback (still live, unchanged, at `nucleation_adapter.py:379`), loose boundary decoding, overbroad optional exception handling, frame validation.
- **Already fixed:** configurable operation timeouts and timeout accounting from queue entry (verified in `worker.py`, unchanged since audit).
- **No change:** infrastructure-only `FrameStreamClosed` (still in `wire.py:73`, still used from `worker.py`); casts unless replaced by genuinely typed decoding (verified still pervasive across `nucleation_adapter.py` and `worker.py` — no typed JSON decoding helper has landed).

## Status

**Done.** Unresolved source formats reject instead of becoming litematic. Required native JSON,
base64, frame headers, numeric ranges, vectors, RGBA, payload totals, and every operation's arity
are decoded strictly before allocation or slicing. Optional sign/lattice evidence alone may be
dropped, with malformed evidence covered; simulation failures keep stable user copy and developer
context. Queue wait and every operation timeout are covered. A clean `0.10.14` reproducer confirmed
the current exception-type documentation mismatch and it is reported as
[Nucleation #40](https://github.com/Schem-at/Nucleation/issues/40), cited beside the sole narrow
workaround.
