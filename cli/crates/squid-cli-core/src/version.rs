//! Compile-time CLI and protocol version metadata.

use serde::Serialize;

/// First submission protocol understood by this binary.
pub const MINIMUM_PROTOCOL: u32 = 1;

/// Latest submission protocol understood by this binary.
pub const MAXIMUM_PROTOCOL: u32 = 1;

/// Build metadata reported by `squid version`.
#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct VersionInfo {
    /// CLI SemVer assigned by Cargo.
    pub cli_version: &'static str,
    /// Rust compilation target used for this binary.
    pub target: &'static str,
    /// Oldest supported submission protocol.
    pub minimum_protocol: u32,
    /// Newest supported submission protocol.
    pub maximum_protocol: u32,
}

impl VersionInfo {
    /// Construct metadata embedded in the current binary.
    #[must_use]
    pub const fn current() -> Self {
        Self {
            cli_version: env!("CARGO_PKG_VERSION"),
            target: current_target(),
            minimum_protocol: MINIMUM_PROTOCOL,
            maximum_protocol: MAXIMUM_PROTOCOL,
        }
    }
}

const fn current_target() -> &'static str {
    env!("SQUID_BUILD_TARGET")
}
