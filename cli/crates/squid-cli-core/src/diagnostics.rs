//! Stored error report lookup, for operators holding `diagnostics.error.read`.

use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::credential::SecretBytes;
use crate::transport::{ApiClient, ApiMethod, ApiRequest, ApiResponse, TransportError};

/// Longest reference the server will consider, mirrored so a typo fails locally.
pub const MAXIMUM_REFERENCE_LENGTH: usize = 128;

/// Upper bound on a listing, so a server answering with an unbounded page is rejected
/// rather than rendered.
const MAXIMUM_LISTED_REPORTS: usize = 100;

#[derive(Debug, Error, PartialEq, Eq)]
pub enum DiagnosticsContractError {
    #[error("the reference is empty or too long")]
    InvalidReference,
    #[error("the server returned more error reports than the protocol allows")]
    TooManyReports,
}

/// One stored failure, without its traceback or logs.
#[derive(Debug, Deserialize, Serialize)]
pub struct ErrorReportSummary {
    pub reference: String,
    pub correlation_id: String,
    pub occurred_at: String,
    pub surface: String,
    #[serde(default)]
    pub origin: Option<String>,
    pub exception_type: String,
    #[serde(default)]
    pub code: Option<String>,
}

/// One stored failure with everything kept about it.
#[derive(Debug, Deserialize, Serialize)]
pub struct ErrorReportDetail {
    pub reference: String,
    pub correlation_id: String,
    pub occurred_at: String,
    pub surface: String,
    #[serde(default)]
    pub origin: Option<String>,
    pub exception_type: String,
    #[serde(default)]
    pub code: Option<String>,
    pub message: String,
    pub traceback: String,
    #[serde(default)]
    pub log_tail: Vec<String>,
    #[serde(default = "one")]
    pub matching_references: u32,
}

const fn one() -> u32 {
    1
}

/// A page of stored failures.
#[derive(Debug, Deserialize, Serialize)]
pub struct ErrorReportPage {
    pub items: Vec<ErrorReportSummary>,
}

impl ErrorReportPage {
    /// Reject a page the protocol does not allow before anything renders it.
    pub fn validate(&self) -> Result<(), DiagnosticsContractError> {
        if self.items.len() > MAXIMUM_LISTED_REPORTS {
            return Err(DiagnosticsContractError::TooManyReports);
        }
        Ok(())
    }
}

/// Validate a caller-supplied reference before it reaches a URL path.
pub fn validate_reference(reference: &str) -> Result<&str, DiagnosticsContractError> {
    let trimmed = reference.trim().trim_matches('`').trim();
    if trimmed.is_empty() || trimmed.len() > MAXIMUM_REFERENCE_LENGTH {
        return Err(DiagnosticsContractError::InvalidReference);
    }
    // Path segment, so anything that could escape it is refused here rather than percent-encoded
    // into a request the server would only reject anyway.
    if !trimmed
        .bytes()
        .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-' | b'.'))
    {
        return Err(DiagnosticsContractError::InvalidReference);
    }
    Ok(trimmed)
}

pub struct DiagnosticsApi<'a> {
    client: &'a ApiClient,
}

impl<'a> DiagnosticsApi<'a> {
    #[must_use]
    pub const fn new(client: &'a ApiClient) -> Self {
        Self { client }
    }

    pub fn list_errors(
        &self,
        token: &SecretBytes,
    ) -> Result<ApiResponse<ErrorReportPage>, TransportError> {
        self.client.send_json(
            ApiRequest::new(ApiMethod::Get, "/api/v1/diagnostics/errors"),
            Some(token),
        )
    }

    pub fn get_error(
        &self,
        reference: &str,
        token: &SecretBytes,
    ) -> Result<ApiResponse<ErrorReportDetail>, TransportError> {
        let reference =
            validate_reference(reference).map_err(|_error| TransportError::InvalidEndpointPath)?;
        self.client.send_json(
            ApiRequest::new(
                ApiMethod::Get,
                format!("/api/v1/diagnostics/errors/{reference}"),
            ),
            Some(token),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn strips_the_backticks_an_error_card_rendered() {
        assert_eq!(validate_reference("  `0a1b2c3d4e5f` "), Ok("0a1b2c3d4e5f"));
    }

    #[test]
    fn refuses_a_reference_that_could_escape_its_path_segment() {
        for candidate in ["", "   ", "../secrets", "a/b", "a?b", &"a".repeat(129)] {
            assert_eq!(
                validate_reference(candidate),
                Err(DiagnosticsContractError::InvalidReference),
                "accepted {candidate:?}",
            );
        }
    }

    #[test]
    fn rejects_an_unbounded_page() {
        let page = ErrorReportPage {
            items: (0..MAXIMUM_LISTED_REPORTS + 1)
                .map(|index| ErrorReportSummary {
                    reference: format!("{index:012}"),
                    correlation_id: format!("{index:032}"),
                    occurred_at: "2026-08-17T00:00:00Z".to_owned(),
                    surface: "http".to_owned(),
                    origin: None,
                    exception_type: "RuntimeError".to_owned(),
                    code: None,
                })
                .collect(),
        };

        assert_eq!(
            page.validate(),
            Err(DiagnosticsContractError::TooManyReports)
        );
    }
}
