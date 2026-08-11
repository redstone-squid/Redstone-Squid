# Redstone Squid CLI

This workspace contains the standalone `squid` command-line client. It is intentionally separate
from the Python backend, Astro catalogue, and Minecraft clients so its native releases can use an
independent SemVer lifecycle.

The connected API client is gated on the completed provider-neutral platform contract. The first
milestones establish local configuration, output, credential, transport, recovery, and process
supervision behavior without depending on unfinished server routes.

## Profiles

Every connected command will resolve a named profile. Custom origins are isolated by their exact
normalized scheme, host, and port, and must be trusted explicitly:

```console
squid profile add local --origin http://127.0.0.1:8000 --trust
squid profile add production --origin https://squid.example --trust
squid profile list
squid profile use production
```

Plain HTTP is accepted only for literal IPv4 or IPv6 loopback addresses. Profile configuration is
non-secret; device credentials and encrypted recovery state use separate stores. Removing a
profile purges those local stores before deleting its configuration.

Device signing keys and draft-cache keys use Windows Credential Manager, macOS Keychain, or the
Linux Secret Service. If native storage is unavailable, callers must explicitly permit the
owner-readable file fallback. That choice is pinned per exact origin and remains detectable so
every subsequent command can display a warning until the credentials are purged or migrated.

Draft caches, resumable operation records, and short-lived sessions are stored in versioned
XChaCha20-Poly1305 envelopes. Authentication binds every file to its exact origin and state kind,
so copied, swapped, or modified ciphertext fails closed.

HTTP clients are constructed only from validated trusted profiles. They never follow redirects or
inherit ambient proxy settings, require TLS 1.2 or newer outside the literal-loopback development
exception, cap JSON requests and responses, and send protocol, renderer-capability, locale, and
instance headers on every request.

Mutating operations can be written to the encrypted recovery queue before network I/O. Retries
reuse the original idempotency key, serialize concurrent queue updates, retain private JSON only in
encrypted state, and stop automatically on permanent failures or after a bounded backoff budget.

Update metadata is notification-only. The verifier authenticates the exact manifest bytes with a
pinned Ed25519 release key before parsing them, requires HTTPS artifact URLs, compares independent
CLI/protocol versions, and can stream-check the signed size and SHA-256 without replacing a binary.

## Terminal forms and processes

The form core is deliberately independent of the unfinished API DTOs. An adapter can map a pinned
server manifest into validated stable field codes, localized labels, constraints, and supported
controls. Required renderer capabilities fail closed and request web continuation; unknown optional
presentation hints are reported but may be ignored. Non-interactive invocations never open a prompt.

Profiles can select either localized line prompts or a full-screen terminal editor. Both sanitize
server-authored terminal text. The full-screen editor supports bounded text, multiline text,
integers, booleans, single choice, and multiple choice, and restores raw/alternate-screen state on
success, cancellation, or failure.

External editor commands are tokenized without invoking a shell. Documents use bounded,
owner-readable temporary files, symlink replacements are rejected, and timed-out child processes
are terminated and reaped before the command returns.

## Development

Install Rust 1.85 and run from this directory:

```console
cargo fmt --check
cargo clippy --workspace --all-targets --all-features -- -D warnings
cargo test --workspace
```
