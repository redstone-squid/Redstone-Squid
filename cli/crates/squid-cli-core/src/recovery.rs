//! Crash-safe idempotent operation queue and conservative retry classification.

use std::collections::BTreeSet;
use std::fmt;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use serde_json::Value;
use thiserror::Error;
use uuid::Uuid;
use zeroize::Zeroize;

use crate::credential::SecretBytes;
use crate::encrypted_state::{
    EncryptedStateError, EncryptedStateStore, EncryptedUpdateError, StateKind,
};
use crate::transport::{ApiMethod, ApiRequest, TransportError};

const RECOVERY_SCHEMA_VERSION: u32 = 1;
const MAXIMUM_PENDING_OPERATIONS: usize = 1000;
const MAXIMUM_ERROR_CODE_BYTES: usize = 128;

/// Durable lifecycle of one operation that has not completed successfully.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PendingOperationState {
    Queued,
    Retrying,
    NeedsAttention,
}

/// Non-sensitive queue metadata safe for status output.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct PendingOperationInfo {
    pub id: Uuid,
    pub idempotency_key: Uuid,
    pub method: ApiMethod,
    pub path: String,
    pub base_revision: Option<u64>,
    pub created_at_unix_seconds: u64,
    pub last_attempt_at_unix_seconds: Option<u64>,
    pub attempts: u32,
    pub state: PendingOperationState,
    pub last_error_code: Option<String>,
}

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct StoredPendingOperation {
    #[serde(flatten)]
    info: PendingOperationInfo,
    body: SensitiveJson,
}

impl fmt::Debug for StoredPendingOperation {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("StoredPendingOperation")
            .field("info", &self.info)
            .field("body", &"[REDACTED]")
            .finish()
    }
}

#[derive(Deserialize, Serialize)]
#[serde(transparent)]
struct SensitiveJson(Value);

impl Drop for SensitiveJson {
    fn drop(&mut self) {
        zeroize_json(&mut self.0);
    }
}

fn zeroize_json(value: &mut Value) {
    match value {
        Value::String(value) => value.zeroize(),
        Value::Array(values) => {
            for value in values {
                zeroize_json(value);
            }
        }
        Value::Object(values) => {
            let values = std::mem::take(values);
            for (mut key, mut value) in values {
                key.zeroize();
                zeroize_json(&mut value);
            }
        }
        Value::Null | Value::Bool(_) | Value::Number(_) => {}
    }
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct RecoveryDocument {
    schema_version: u32,
    operations: Vec<StoredPendingOperation>,
}

impl Default for RecoveryDocument {
    fn default() -> Self {
        Self {
            schema_version: RECOVERY_SCHEMA_VERSION,
            operations: Vec::new(),
        }
    }
}

impl RecoveryDocument {
    fn validate(&self) -> Result<(), RecoveryMutationError> {
        if self.schema_version != RECOVERY_SCHEMA_VERSION
            || self.operations.len() > MAXIMUM_PENDING_OPERATIONS
        {
            return Err(RecoveryMutationError::InvalidDocument);
        }
        let mut ids = BTreeSet::new();
        let mut idempotency_keys = BTreeSet::new();
        for operation in &self.operations {
            if !ids.insert(operation.info.id)
                || !idempotency_keys.insert(operation.info.idempotency_key)
                || operation.info.method == ApiMethod::Get
                || !valid_error_code(operation.info.last_error_code.as_deref())
            {
                return Err(RecoveryMutationError::InvalidDocument);
            }
            operation
                .to_request()
                .map_err(|_error| RecoveryMutationError::InvalidDocument)?;
        }
        Ok(())
    }
}

impl StoredPendingOperation {
    fn to_request(&self) -> Result<ApiRequest, TransportError> {
        let request = ApiRequest::new(self.info.method, self.info.path.clone())
            .with_json(&self.body.0)
            .map(|request| request.with_idempotency_key(self.info.idempotency_key))?;
        request.validate()?;
        Ok(request)
    }
}

/// Atomic queue stored inside the origin-bound encrypted state layer.
#[derive(Clone, Debug)]
pub struct RecoveryQueue {
    state: EncryptedStateStore,
}

impl RecoveryQueue {
    /// Use the encrypted pending-operation coordinate for one exact origin.
    #[must_use]
    pub const fn new(state: EncryptedStateStore) -> Self {
        Self { state }
    }

    /// Persist a mutating operation before its first network attempt.
    pub fn enqueue(
        &self,
        key: &SecretBytes,
        method: ApiMethod,
        path: impl Into<String>,
        body: Value,
        base_revision: Option<u64>,
    ) -> Result<PendingOperationInfo, RecoveryError> {
        if method == ApiMethod::Get {
            return Err(RecoveryError::ReadOnlyOperation);
        }
        let path = path.into();
        ApiRequest::new(method, path.clone())
            .with_json(&body)?
            .validate()?;
        let created_at_unix_seconds = unix_seconds(SystemTime::now())?;
        let info = PendingOperationInfo {
            id: Uuid::new_v4(),
            idempotency_key: Uuid::new_v4(),
            method,
            path,
            base_revision,
            created_at_unix_seconds,
            last_attempt_at_unix_seconds: None,
            attempts: 0,
            state: PendingOperationState::Queued,
            last_error_code: None,
        };
        let stored = StoredPendingOperation {
            info: info.clone(),
            body: SensitiveJson(body),
        };
        self.state
            .update(
                StateKind::PendingOperations,
                key,
                RecoveryDocument::default,
                move |document| {
                    document.validate()?;
                    if document.operations.len() == MAXIMUM_PENDING_OPERATIONS {
                        return Err(RecoveryMutationError::QueueFull);
                    }
                    document.operations.push(stored);
                    document.validate()?;
                    Ok(())
                },
            )
            .map_err(map_update_error)?;
        Ok(info)
    }

    /// List queue metadata without decrypting bodies into caller-visible values.
    pub fn list(&self, key: &SecretBytes) -> Result<Vec<PendingOperationInfo>, RecoveryError> {
        let document = self
            .state
            .read::<RecoveryDocument>(StateKind::PendingOperations, key)?
            .unwrap_or_default();
        document.validate().map_err(map_mutation_error)?;
        Ok(document
            .operations
            .into_iter()
            .map(|operation| operation.info)
            .collect())
    }

    /// Reconstruct one exact idempotent request for a retry worker.
    pub fn request(&self, key: &SecretBytes, id: Uuid) -> Result<ApiRequest, RecoveryError> {
        let document = self
            .state
            .read::<RecoveryDocument>(StateKind::PendingOperations, key)?
            .unwrap_or_default();
        document.validate().map_err(map_mutation_error)?;
        document
            .operations
            .iter()
            .find(|operation| operation.info.id == id)
            .ok_or(RecoveryError::NotFound)?
            .to_request()
            .map_err(RecoveryError::Transport)
    }

    /// Record a failed attempt while preserving the same idempotency key.
    pub fn record_failure(
        &self,
        key: &SecretBytes,
        id: Uuid,
        retryable: bool,
        error_code: Option<String>,
    ) -> Result<PendingOperationInfo, RecoveryError> {
        if !valid_error_code(error_code.as_deref()) {
            return Err(RecoveryError::InvalidErrorCode);
        }
        let attempted_at = unix_seconds(SystemTime::now())?;
        self.state
            .update(
                StateKind::PendingOperations,
                key,
                RecoveryDocument::default,
                move |document| {
                    document.validate()?;
                    let operation = document
                        .operations
                        .iter_mut()
                        .find(|operation| operation.info.id == id)
                        .ok_or(RecoveryMutationError::NotFound)?;
                    operation.info.attempts = operation.info.attempts.saturating_add(1);
                    operation.info.last_attempt_at_unix_seconds = Some(attempted_at);
                    operation.info.state = if retryable {
                        PendingOperationState::Retrying
                    } else {
                        PendingOperationState::NeedsAttention
                    };
                    operation.info.last_error_code = error_code;
                    Ok(operation.info.clone())
                },
            )
            .map_err(map_update_error)
    }

    /// Remove an operation only after confirmed success or explicit user discard.
    pub fn remove(&self, key: &SecretBytes, id: Uuid) -> Result<(), RecoveryError> {
        self.state
            .update(
                StateKind::PendingOperations,
                key,
                RecoveryDocument::default,
                move |document| {
                    document.validate()?;
                    let original_length = document.operations.len();
                    document
                        .operations
                        .retain(|operation| operation.info.id != id);
                    if document.operations.len() == original_length {
                        return Err(RecoveryMutationError::NotFound);
                    }
                    Ok(())
                },
            )
            .map_err(map_update_error)
    }
}

/// Conservative retry policy; mutating requests require a stable idempotency key.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct RetryPolicy {
    pub maximum_attempts: u32,
    pub initial_delay: Duration,
    pub maximum_delay: Duration,
}

impl Default for RetryPolicy {
    fn default() -> Self {
        Self {
            maximum_attempts: 5,
            initial_delay: Duration::from_secs(1),
            maximum_delay: Duration::from_secs(30),
        }
    }
}

impl RetryPolicy {
    /// Return a bounded exponential delay only for safe and retryable failures.
    #[must_use]
    pub fn delay(
        self,
        method: ApiMethod,
        has_idempotency_key: bool,
        attempts_completed: u32,
        error: &TransportError,
    ) -> Option<Duration> {
        if attempts_completed >= self.maximum_attempts
            || (!method.is_idempotent() && !has_idempotency_key)
            || !retryable_transport_error(error)
        {
            return None;
        }
        let exponent = attempts_completed.saturating_sub(1).min(31);
        let multiplier = 1_u32 << exponent;
        let exponential = self
            .initial_delay
            .checked_mul(multiplier)
            .map(|delay| delay.min(self.maximum_delay))
            .unwrap_or(self.maximum_delay);
        let server_delay = retry_after_delay(error)
            .map(|delay| delay.min(self.maximum_delay))
            .unwrap_or_default();
        Some(exponential.max(server_delay))
    }
}

fn retryable_transport_error(error: &TransportError) -> bool {
    match error {
        TransportError::Request(_) => true,
        TransportError::Http {
            status, problem, ..
        } => {
            problem.as_ref().is_some_and(|problem| problem.retryable)
                || matches!(*status, 429 | 502 | 503 | 504)
        }
        _ => false,
    }
}

fn retry_after_delay(error: &TransportError) -> Option<Duration> {
    match error {
        TransportError::Http { retry_after, .. } => retry_after
            .as_deref()
            .and_then(|value| value.parse::<u64>().ok())
            .map(Duration::from_secs),
        _ => None,
    }
}

fn valid_error_code(value: Option<&str>) -> bool {
    value.is_none_or(|value| {
        !value.is_empty()
            && value.len() <= MAXIMUM_ERROR_CODE_BYTES
            && value
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
    })
}

fn unix_seconds(value: SystemTime) -> Result<u64, RecoveryError> {
    value
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .map_err(|_error| RecoveryError::Clock)
}

fn map_update_error(error: EncryptedUpdateError<RecoveryMutationError>) -> RecoveryError {
    match error {
        EncryptedUpdateError::State(error) => RecoveryError::State(error),
        EncryptedUpdateError::Mutation(error) => map_mutation_error(error),
    }
}

const fn map_mutation_error(error: RecoveryMutationError) -> RecoveryError {
    match error {
        RecoveryMutationError::InvalidDocument => RecoveryError::InvalidState,
        RecoveryMutationError::QueueFull => RecoveryError::QueueFull,
        RecoveryMutationError::NotFound => RecoveryError::NotFound,
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum RecoveryMutationError {
    InvalidDocument,
    QueueFull,
    NotFound,
}

/// Recovery queue validation, persistence, or request reconstruction failure.
#[derive(Debug, Error)]
pub enum RecoveryError {
    #[error("read-only requests are not persisted in the recovery queue")]
    ReadOnlyOperation,
    #[error("pending operation was not found")]
    NotFound,
    #[error("pending operation queue is full")]
    QueueFull,
    #[error("pending operation state is invalid")]
    InvalidState,
    #[error("pending operation error code is invalid")]
    InvalidErrorCode,
    #[error("system clock is before the Unix epoch")]
    Clock,
    #[error("pending operation request is invalid: {0}")]
    Transport(#[from] TransportError),
    #[error("pending operation state failed: {0}")]
    State(#[from] EncryptedStateError),
}

#[cfg(test)]
mod tests {
    use std::error::Error;
    use std::sync::Arc;
    use std::thread;

    use serde_json::json;
    use tempfile::tempdir;

    use super::{PendingOperationState, RecoveryQueue, RetryPolicy};
    use crate::credential::SecretBytes;
    use crate::encrypted_state::EncryptedStateStore;
    use crate::origin::ApiOrigin;
    use crate::profile::ProfilePaths;
    use crate::transport::{ApiMethod, TransportError};

    fn queue(directory: &std::path::Path) -> Result<RecoveryQueue, Box<dyn Error>> {
        let origin = ApiOrigin::parse("https://example.com")?;
        Ok(RecoveryQueue::new(EncryptedStateStore::new(
            &ProfilePaths::under(directory),
            &origin,
        )))
    }

    fn key() -> SecretBytes {
        SecretBytes::new(vec![3; 32])
    }

    #[test]
    fn persists_body_but_lists_only_metadata() -> Result<(), Box<dyn Error>> {
        let directory = tempdir()?;
        let queue = queue(directory.path())?;
        let added = queue.enqueue(
            &key(),
            ApiMethod::Patch,
            "/api/v1/drafts/00000000-0000-0000-0000-000000000001",
            json!({"private_answer": "hidden"}),
            Some(7),
        )?;
        let listed = queue.list(&key())?;
        assert_eq!(listed, vec![added.clone()]);
        let request = queue.request(&key(), added.id)?;
        let debug = format!("{request:?}");
        assert!(!debug.contains("hidden"));
        assert!(debug.contains(&added.idempotency_key.to_string()));
        Ok(())
    }

    #[test]
    fn records_failure_without_changing_idempotency_key() -> Result<(), Box<dyn Error>> {
        let directory = tempdir()?;
        let queue = queue(directory.path())?;
        let added = queue.enqueue(
            &key(),
            ApiMethod::Post,
            "/api/v1/drafts",
            json!({"category": "door"}),
            None,
        )?;
        let failed = queue.record_failure(
            &key(),
            added.id,
            true,
            Some(String::from("temporarily_unavailable")),
        )?;
        assert_eq!(failed.idempotency_key, added.idempotency_key);
        assert_eq!(failed.attempts, 1);
        assert_eq!(failed.state, PendingOperationState::Retrying);
        queue.remove(&key(), added.id)?;
        assert!(queue.list(&key())?.is_empty());
        Ok(())
    }

    #[test]
    fn serializes_concurrent_enqueues() -> Result<(), Box<dyn Error>> {
        let directory = tempdir()?;
        let queue = Arc::new(queue(directory.path())?);
        let mut workers = Vec::new();
        for index in 0..8 {
            let queue = Arc::clone(&queue);
            workers.push(thread::spawn(move || {
                queue.enqueue(
                    &key(),
                    ApiMethod::Post,
                    "/api/v1/drafts",
                    json!({"index": index}),
                    None,
                )
            }));
        }
        for worker in workers {
            let result = worker
                .join()
                .map_err(|_error| std::io::Error::other("recovery worker panicked"))?;
            assert!(result.is_ok(), "concurrent enqueue failed: {result:?}");
        }
        assert_eq!(queue.list(&key())?.len(), 8);
        Ok(())
    }

    #[test]
    fn retry_policy_requires_safety_and_retryable_failure() {
        let policy = RetryPolicy::default();
        let retryable = TransportError::Http {
            status: 503,
            problem: None,
            request_id: None,
            retry_after: None,
        };
        assert_eq!(
            policy.delay(ApiMethod::Post, true, 1, &retryable),
            Some(std::time::Duration::from_secs(1)),
        );
        assert_eq!(policy.delay(ApiMethod::Post, false, 1, &retryable), None);
        let rate_limited = TransportError::Http {
            status: 429,
            problem: None,
            request_id: None,
            retry_after: Some(String::from("10")),
        };
        assert_eq!(
            policy.delay(ApiMethod::Post, true, 1, &rate_limited),
            Some(std::time::Duration::from_secs(10)),
        );
        let permanent = TransportError::Http {
            status: 422,
            problem: None,
            request_id: None,
            retry_after: None,
        };
        assert_eq!(policy.delay(ApiMethod::Patch, true, 1, &permanent), None);
    }
}
