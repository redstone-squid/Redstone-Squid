# Redstone Squid CLI

This workspace contains the standalone `squid` command-line client. It is intentionally separate
from the Python backend, Astro catalogue, and Minecraft clients so its native releases can use an
independent SemVer lifecycle.

The connected API client is gated on the completed provider-neutral platform contract. The first
milestones establish local configuration, output, credential, transport, recovery, and process
supervision behavior without depending on unfinished server routes.

## Development

Install Rust 1.85 and run from this directory:

```console
cargo fmt --check
cargo clippy --workspace --all-targets --all-features -- -D warnings
cargo test --workspace
```
