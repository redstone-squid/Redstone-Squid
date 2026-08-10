//! Stable process exit classes for scripts and human callers.

use std::process::ExitCode;

/// Stable process result classes exposed by the CLI.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[repr(u8)]
pub enum ExitStatus {
    /// The command completed successfully.
    Success = 0,
    /// Command syntax or local input was invalid.
    Usage = 2,
    /// Local configuration, state, or filesystem access failed.
    LocalState = 3,
    /// No valid account or service credential was available.
    Authentication = 4,
    /// The authenticated principal lacks permission.
    Authorization = 5,
    /// The server rejected an otherwise valid request.
    ServerRejection = 6,
    /// An optimistic draft or resource revision was stale.
    Conflict = 7,
    /// A server rate limit was reached.
    RateLimited = 8,
    /// The server or network was unavailable.
    Unavailable = 9,
    /// A required locally supervised child exited.
    ChildFailure = 10,
    /// Waiting ended while server work continued.
    WaitTimeout = 11,
    /// A cryptographic, trust, or integrity check failed.
    Security = 12,
    /// The user interrupted the command.
    Interrupted = 130,
}

impl From<ExitStatus> for ExitCode {
    fn from(value: ExitStatus) -> Self {
        Self::from(value as u8)
    }
}

#[cfg(test)]
mod tests {
    use super::ExitStatus;

    #[test]
    fn published_values_remain_stable() {
        let values = [
            (ExitStatus::Success, 0),
            (ExitStatus::Usage, 2),
            (ExitStatus::LocalState, 3),
            (ExitStatus::Authentication, 4),
            (ExitStatus::Authorization, 5),
            (ExitStatus::ServerRejection, 6),
            (ExitStatus::Conflict, 7),
            (ExitStatus::RateLimited, 8),
            (ExitStatus::Unavailable, 9),
            (ExitStatus::ChildFailure, 10),
            (ExitStatus::WaitTimeout, 11),
            (ExitStatus::Security, 12),
            (ExitStatus::Interrupted, 130),
        ];

        for (status, expected) in values {
            assert_eq!(status as u8, expected);
        }
    }
}
