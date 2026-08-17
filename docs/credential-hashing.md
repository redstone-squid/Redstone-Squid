# Credential hashing

Four subsystems store a digest of a bearer secret rather than the secret itself: API keys, web
sessions, CLI authorization secrets, and Minecraft installation/player credentials. They all use the
same construction, and this page is the single place its rationale is written down. The call sites
link here instead of repeating it.

## The construction

```python
hmac.digest(pepper, secret.encode(), hashlib.sha256)
```

- **The secret is 256 bits of CSPRNG output.** `secrets.token_bytes(32)` for API keys
  (`API_KEY_SECRET_BYTES`) and Minecraft credentials (`SECRET_BYTES`), `secrets.token_urlsafe(32)`
  for web and CLI sessions. Nothing in this set is chosen, typed, or remembered by a human.
- **The pepper is a deployment key, not a salt.** It lives in configuration
  (`SQUID_API_KEY_PEPPER`, `SQUID_API_SESSION_PEPPER`, `SQUID_CLI_AUTH_PEPPER`,
  `SQUID_MINECRAFT_AUTH_PEPPER`), never in the database, so a database disclosure alone does not let
  an attacker test candidate secrets offline. Configuration enforces a floor on each: 16 bytes for
  the API peppers, 32 for the CLI and Minecraft ones.
- **Digests that share one pepper are domain-separated.** `CliSecretCodec.digest` and
  `MinecraftSecretCodec.digest` prefix a purpose label and a NUL byte, so a device-code digest can
  never authenticate as a session token.
- **Comparison is `hmac.compare_digest`,** and lookup is by the credential's public identifier — the
  key ID, session id, or grant id carried in the token — rather than by the digest.
- **Revocation and expiry are checked after the digest matches,** so a revoked credential and a
  wrong secret are indistinguishable in timing and in response.

## Why not a password KDF

Argon2, scrypt, and bcrypt exist to make guessing cheap-to-enumerate inputs expensive. A password
has perhaps 30 bits of entropy, so an attacker who steals the digest can enumerate the input space;
a memory-hard KDF prices that enumeration out.

None of these secrets are in that regime. Against 256 bits of CSPRNG entropy there is no input space
to enumerate: an attacker who cannot invert HMAC-SHA-256 also cannot guess the preimage, and the
work factor a KDF adds defends nothing that is under attack.

It would also be worse than free. A KDF here sits on the authentication path of every request, and
that path is reachable by an attacker who holds nothing:

- `ApiKeyService.authenticate` short-circuits before hashing when the key ID misses, but the key ID
  is the public half of the token and is written into the caller's subject as `api-key:{key_id}`. Anyone
  who has seen a key ID in a log can send a valid key ID with a garbage secret and force one full
  KDF evaluation per request — a work amplifier handed to an unauthenticated caller.
- Web, CLI, and Minecraft authentication have the same shape: the public identifier is in the token,
  and the digest is computed before the credential's state is known.

HMAC-SHA-256 costs microseconds, so the same request buys an attacker nothing.

## Where entropy *is* the question

The rule above is about high-entropy random secrets. The one peppered digest in this codebase whose
input is human-sized is the account verification code. It used to be six digits — roughly 19.8 bits —
stored as `sha256(pepper || code)` and looked up by code alone, with nothing capping the rate.

The lookup is what made that dangerous rather than merely weak. `consume_code_and_link_account`
matches on the code, the expiry and the `valid` flag, and never on the Minecraft UUID the code was
issued for, so one guess is tested against *every* outstanding code at once: the chance per attempt
is `outstanding / space`, not `1 / space`. And the Discord identity comes from whoever typed the
code, so a hit attaches the matched Java account to the guesser. That is identity takeover, not a
nuisance.

Three levers, all now applied ([plan 2](plans/pr-183-review/02-user-identity-persistence.md),
subplan 6):

- **A keyed digest.** `hash_verification_code` is `hmac.digest(pepper, code, sha256)`. The pepper is
  a key; prefixing was the weaker construction for no saving. No dual-read path and no backfill —
  codes live ten minutes, so a deploy invalidates at most one window and the in-game `/link`
  reissues.
- **A wider code.** `generate_verification_code` mints ten digits, about 33 bits. It stays numeric
  because `/verify` returns an `int` to the in-game plugin that shows the code to the player; base32
  would be stronger and would also change that response type.
- **An attempt cap.** `verification_attempts` counts *consecutive* failures per `(provider, subject)`
  and refuses redemptions for a cooling-off period once the cap is reached. Keyed on the external
  identity rather than an account, because the guesser may not have an account yet. A success clears
  the count, and holding a *correct* code never counts as a failure — otherwise replaying your own
  successful code would lock out an already-linked user, turning the control into the abuse.

A KDF is still not the answer here: the code is short-lived and now rate-limited, which is what buys
the margin a work factor would otherwise have to.

## Static analysis

CodeQL's `py/weak-sensitive-data-hashing` flags SHA-256 on anything it can reach from a
password-shaped name. The surviving hashing sites carry an inline
`# codeql[py/weak-sensitive-data-hashing]` suppression with the entropy rationale on the adjacent
line. If code scanning still reports one — inline suppression behaviour differs between the default
setup and the advanced workflow in `.github/workflows/codeql.yml` — dismiss the alert as "won't fix"
and link this page from the dismissal comment.
