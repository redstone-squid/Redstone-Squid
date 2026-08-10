//! Versioned machine-output envelopes shared by every command.

use std::collections::BTreeMap;
use std::io::{self, Write};

use serde::Serialize;

/// Machine-output schema emitted by this CLI release.
pub const OUTPUT_SCHEMA_VERSION: u32 = 1;

/// One non-fatal condition accompanying a successful command.
#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct Warning {
    pub code: String,
    pub message: String,
}

/// Stable envelope for one successful non-streaming command.
#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct SuccessEnvelope<T: Serialize> {
    pub schema_version: u32,
    pub command: String,
    pub data: T,
    pub warnings: Vec<Warning>,
    pub request_id: Option<String>,
}

impl<T: Serialize> SuccessEnvelope<T> {
    /// Construct a local or remote command result.
    #[must_use]
    pub fn new(command: impl Into<String>, data: T) -> Self {
        Self {
            schema_version: OUTPUT_SCHEMA_VERSION,
            command: command.into(),
            data,
            warnings: Vec::new(),
            request_id: None,
        }
    }
}

/// Stable, locale-independent error shape inside a failure envelope.
#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct Problem {
    pub code: String,
    pub message: String,
    pub field_errors: BTreeMap<String, String>,
    pub retryable: bool,
    pub suggested_action: Option<String>,
    pub idempotency_key: Option<String>,
}

/// Stable envelope for one failed command.
#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct FailureEnvelope {
    pub schema_version: u32,
    pub command: String,
    pub error: Problem,
    pub request_id: Option<String>,
}

impl FailureEnvelope {
    /// Construct a local or remote command failure.
    #[must_use]
    pub fn new(
        command: impl Into<String>,
        code: impl Into<String>,
        message: impl Into<String>,
    ) -> Self {
        Self {
            schema_version: OUTPUT_SCHEMA_VERSION,
            command: command.into(),
            error: Problem {
                code: code.into(),
                message: message.into(),
                field_errors: BTreeMap::new(),
                retryable: false,
                suggested_action: None,
                idempotency_key: None,
            },
            request_id: None,
        }
    }
}

/// Write exactly one compact JSON value followed by a newline.
pub fn write_json(value: &impl Serialize, output: &mut impl Write) -> io::Result<()> {
    serde_json::to_writer(&mut *output, value)
        .map_err(io::Error::other)
        .and_then(|()| writeln!(output))
}

#[cfg(test)]
mod tests {
    use serde::Serialize;

    use super::{OUTPUT_SCHEMA_VERSION, SuccessEnvelope, write_json};

    #[derive(Serialize)]
    struct Value {
        id: u8,
    }

    #[test]
    fn success_envelope_has_stable_shape() {
        let mut output = Vec::new();
        let result = write_json(
            &SuccessEnvelope::new("test.show", Value { id: 7 }),
            &mut output,
        );
        assert!(result.is_ok(), "JSON output failed: {result:?}");

        let value = serde_json::from_slice::<serde_json::Value>(&output);
        assert!(value.is_ok(), "output was not JSON: {value:?}");
        assert_eq!(
            value
                .as_ref()
                .ok()
                .and_then(|item| item.get("schema_version"))
                .and_then(serde_json::Value::as_u64),
            Some(u64::from(OUTPUT_SCHEMA_VERSION)),
        );
        assert_eq!(
            value
                .as_ref()
                .ok()
                .and_then(|item| item.get("command"))
                .and_then(serde_json::Value::as_str),
            Some("test.show"),
        );
    }
}
