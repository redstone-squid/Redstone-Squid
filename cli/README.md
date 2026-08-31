# Redstone Squid CLI

This workspace contains the standalone `squid` command-line client. It is intentionally separate
from the Python backend, Astro catalogue, and Minecraft clients so its native releases can use an
independent SemVer lifecycle.

The connected client uses the provider-neutral draft and form contract shared by the web,
Discord, Paper, and Fabric renderers. CLI and protocol versions remain independent.

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

## Authentication

The CLI holds an Ed25519 device key and uses browser approval to bind that public key to an account.
The browser receives only the short user code; the device code, signing key, and session token stay
in the CLI. Confirm the public-key fingerprint shown in both places before approving:

```console
squid auth login --label "Alice's workstation"
squid auth status
squid auth logout
```

After first approval, `auth login` renews a short-lived session by signing a one-time backend nonce.
It does not repeat browser approval unless the server device was revoked. In a headless environment,
`--allow-file-fallback` explicitly permits the owner-readable credential fallback and every human
connected command keeps displaying a warning while that fallback is selected. `auth logout` revokes
the current server session before clearing its encrypted local bearer; `--local-only` is available
for deliberate offline recovery.

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

## Drafts and submission

Connected draft commands use the account selected during device approval. Draft writes carry an
optimistic base revision and one idempotency key, so a stale second client receives a conflict
instead of silently overwriting another edit:

```console
squid draft list
squid draft create door --edit
squid draft show 64760b2f-b352-45e0-9ed1-67b9da901992
squid draft set 64760b2f-b352-45e0-9ed1-67b9da901992 capture_width 7
squid draft unset 64760b2f-b352-45e0-9ed1-67b9da901992 display_name
squid draft submit 64760b2f-b352-45e0-9ed1-67b9da901992 --wait
```

`draft set` accepts one JSON value, so strings must include JSON quotes. `draft delete` asks for
the exact draft ID unless `--yes` is supplied. Interactive editing always loads the immutable form
revision pinned to the draft, resolves current dynamic options, and stops with a web-continuation
instruction when a required control is unsupported. Cancellation preserves the synchronized draft.

For the shortest interactive path, create, edit, and finalize in one command:

```console
squid submit door --wait
```

Finalization may continue asynchronously. Without `--wait`, or after a local wait timeout, inspect
the durable result with `squid draft status DRAFT_ID`. Add global `--output json` for stable
versioned envelopes; prompts remain on stderr so stdout stays machine-readable.

Images and videos are streamed directly from a regular non-symlink file after the CLI reads the
draft's current server limits. The stable upload UUID makes a lost-response retry safe; the source
path and bytes are never copied into CLI state:

```console
squid media upload DRAFT_ID image screenshot.png --wait
squid media upload DRAFT_ID video demonstration.mp4 --strip-audio
squid media list DRAFT_ID
squid media status DRAFT_ID UPLOAD_ID
squid media discard DRAFT_ID UPLOAD_ID
```

Common extensions select the source content type. Use `--content-type image/...` or
`--content-type video/...` for another supported format. Discard confirmation requires the exact
upload ID unless `--yes` is supplied.

## Development

Install Rust 1.85 and run from this directory:

```console
cargo fmt --check
cargo clippy --workspace --all-targets --all-features -- -D warnings
cargo test --workspace
```
