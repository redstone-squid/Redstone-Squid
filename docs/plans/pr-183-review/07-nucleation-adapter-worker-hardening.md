# 07 — Nucleation Adapter, Wire, and Worker Hardening

## Findings

- The silent `or SchematicFormat.LITEMATIC` fallback remains and can persist a false source format. Reject an unresolved format or preserve an explicit unknown value; never label it litematic by default.
- Nucleation JSON is necessarily dynamic, but casts currently substitute for validation. Decode every external JSON document through narrow validators that reject malformed required fields and deliberately skip only documented optional evidence.
- `_optional()` still catches every exception. Upstream issues [#7](https://github.com/Schem-at/Nucleation/issues/7) and [#8](https://github.com/Schem-at/Nucleation/issues/8) are closed, while Squid pins `nucleation==0.10.1`; reproduce against a clean 0.10.1 install and remove or narrow the workaround if fixed.
- Optional sign/lattice evidence may be dropped, but load-bearing analysis and conversion output must fail explicitly. Translate user-visible adapter failures through Squid errors/i18n at the application boundary rather than embedding arbitrary engine text in Discord responses.
- `FrameStreamClosed` is a private protocol condition, not a domain error, so it should remain an infrastructure exception. The questioned `asdict` casts are static-typing artifacts; remove them only through a typed JSON alias/helper, not by pretending `dict[str, object]` satisfies every mapping contract.
- Worker operation timeouts are now configurable, and the pool subtracts queue wait from the operation budget. Those review concerns are already fixed.

## Plan

1. Add typed JSON decoding helpers for adapter results and worker request headers, including exact tuple lengths, enum values, numeric ranges, payload-part totals, and base64 validation.
2. Remove the format fallback and make the already-vetted `source_format` required for ingestion analysis; keep format sniffing only for standalone adapter/test callers if needed.
3. Reproduce the two documented Nucleation mismatches on 0.10.1. Remove resolved guards/comments; if behavior or current docs still disagree, file a new upstream issue with the pinned version, clean reproducer, observed cost, and record its issue number beside the narrow workaround.
4. Split optional-evidence recovery by operation and expected upstream exception. Preserve causes and structured developer context while keeping translated end-user messages at Squid boundaries.
5. Harden frame decoding before allocation/slicing, while leaving process crash/timeout translation in the supervisor.

## Tests

- Adapter integration fixtures for every supported input/output format, unknown source format, malformed engine JSON, malformed base64, absent optional metadata, and failed export.
- Wire tests for non-object headers, negative/oversized part lengths, mismatched body totals, invalid enums/vectors/RGBA values, and truncated frames.
- Worker-pool tests proving queue time consumes the configured deadline and each per-operation timeout is honored; retain current crash/respawn coverage.
- Run the self-contained upstream reproducers against a clean `nucleation==0.10.1` environment and capture the result in the implementing commit or follow-up issue.

## Disposition

- **Fix:** false format fallback, loose boundary decoding, overbroad optional exception handling, frame validation.
- **Already fixed:** configurable operation timeouts and timeout accounting from queue entry.
- **No change:** infrastructure-only `FrameStreamClosed`; casts unless replaced by genuinely typed decoding.
