//! Browser-approved CLI device enrollment and short-lived session proofs.

use std::fmt;

use serde::{Deserialize, Deserializer, Serialize, Serializer};
use thiserror::Error;
use uuid::Uuid;
use zeroize::Zeroizing;

use crate::credential::{DeviceIdentity, SecretBytes};
use crate::encrypted_state::{EncryptedStateError, EncryptedStateStore, StateKind};
use crate::transport::{
    ApiClient, ApiMethod, ApiRequest, ApiResponse, ClientInstanceId, TransportError,
};

const AUTH_STATE_SCHEMA_VERSION: u32 = 1;
const MAXIMUM_PROOF_VALUE_BYTES: usize = u16::MAX as usize;

/// One-time browser approval response. The device code must never be shown in the browser.
#[derive(Debug, Deserialize)]
pub struct CliEnrollment {
    pub id: Uuid,
    pub device_code: String,
    pub user_code: String,
    pub verification_uri: String,
    pub verification_uri_complete: String,
    pub expires_at: String,
    pub polling_interval_seconds: u64,
}

#[derive(Debug, Serialize)]
struct CliEnrollmentCreateRequest<'a> {
    public_key: String,
    client_instance_id: ClientInstanceId,
    label: &'a str,
}

#[derive(Debug, Serialize)]
struct CliEnrollmentExchangeRequest {
    device_code: String,
    signature: String,
}

#[derive(Debug, Serialize)]
struct CliSessionChallengeRequest {
    device_id: Uuid,
}

/// One-time nonce that an enrolled device must sign.
#[derive(Debug, Deserialize)]
pub struct CliSessionChallenge {
    pub id: Uuid,
    pub device_id: Uuid,
    pub nonce: String,
    pub expires_at: String,
}

#[derive(Debug, Serialize)]
struct CliSessionExchangeRequest {
    device_id: Uuid,
    challenge_id: Uuid,
    nonce: String,
    signature: String,
}

/// Safe account-visible metadata for one enrolled CLI device.
#[derive(Debug, Deserialize)]
pub struct CliDevice {
    pub id: Uuid,
    pub client_instance_id: Uuid,
    pub label: String,
    pub public_key_fingerprint: String,
    pub created_at: String,
    pub last_used_at: String,
    pub revoked_at: Option<String>,
}

/// A short-lived bearer token that is redacted in diagnostics and zeroized on drop.
pub struct SessionToken(Zeroizing<String>);

impl SessionToken {
    /// Copy the token into the transport's scoped secret representation.
    #[must_use]
    pub fn to_secret(&self) -> SecretBytes {
        SecretBytes::new(self.0.as_bytes().to_vec())
    }
}

impl fmt::Debug for SessionToken {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("SessionToken([REDACTED])")
    }
}

impl Serialize for SessionToken {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.serialize_str(&self.0)
    }
}

impl<'de> Deserialize<'de> for SessionToken {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        String::deserialize(deserializer).map(|value| Self(Zeroizing::new(value)))
    }
}

/// One authenticated CLI session issued after a successful device proof.
#[derive(Debug, Deserialize)]
pub struct IssuedCliSession {
    pub device: CliDevice,
    pub session_id: Uuid,
    pub token: SessionToken,
    pub expires_at: String,
}

/// Encrypted local authorization state for one exact profile origin.
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct AuthState {
    schema_version: u32,
    client_instance_id: ClientInstanceId,
    device_id: Option<Uuid>,
    session_id: Option<Uuid>,
    token: Option<SessionToken>,
    expires_at: Option<String>,
}

impl AuthState {
    /// Construct state for a new local CLI installation.
    #[must_use]
    pub fn new() -> Self {
        Self {
            schema_version: AUTH_STATE_SCHEMA_VERSION,
            client_instance_id: ClientInstanceId::generate(),
            device_id: None,
            session_id: None,
            token: None,
            expires_at: None,
        }
    }

    /// Stable installation ID sent in the request header and enrollment body.
    #[must_use]
    pub const fn client_instance_id(&self) -> ClientInstanceId {
        self.client_instance_id
    }

    /// Enrolled device ID, when browser approval has completed at least once.
    #[must_use]
    pub const fn device_id(&self) -> Option<Uuid> {
        self.device_id
    }

    /// Current session ID, if the local session has not been cleared.
    #[must_use]
    pub const fn session_id(&self) -> Option<Uuid> {
        self.session_id
    }

    /// Server-authored session expiry for status display.
    #[must_use]
    pub fn expires_at(&self) -> Option<&str> {
        self.expires_at.as_deref()
    }

    /// Copy the current bearer into a short-lived secret for one request.
    #[must_use]
    pub fn session_token(&self) -> Option<SecretBytes> {
        self.token.as_ref().map(SessionToken::to_secret)
    }

    /// Replace the local bearer and bind it to the server-issued device.
    pub fn set_session(&mut self, issued: IssuedCliSession) {
        self.device_id = Some(issued.device.id);
        self.session_id = Some(issued.session_id);
        self.token = Some(issued.token);
        self.expires_at = Some(issued.expires_at);
    }

    /// Remove only the short-lived bearer while preserving the device identity.
    pub fn clear_session(&mut self) {
        self.session_id = None;
        self.token = None;
        self.expires_at = None;
    }

    /// Forget a revoked or missing server device before starting fresh enrollment.
    pub fn clear_device(&mut self) {
        self.device_id = None;
        self.clear_session();
    }

    fn validate(&self) -> Result<(), AuthStateError> {
        if self.schema_version != AUTH_STATE_SCHEMA_VERSION {
            return Err(AuthStateError::UnsupportedSchema(self.schema_version));
        }
        let session_fields = [
            self.session_id.is_some(),
            self.token.is_some(),
            self.expires_at.is_some(),
        ];
        if session_fields.iter().any(|present| *present)
            && (!session_fields.iter().all(|present| *present) || self.device_id.is_none())
        {
            return Err(AuthStateError::InvalidState);
        }
        Ok(())
    }
}

impl Default for AuthState {
    fn default() -> Self {
        Self::new()
    }
}

impl fmt::Debug for AuthState {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("AuthState")
            .field("schema_version", &self.schema_version)
            .field("client_instance_id", &self.client_instance_id)
            .field("device_id", &self.device_id)
            .field("session_id", &self.session_id)
            .field("token", &self.token.as_ref().map(|_token| "[REDACTED]"))
            .field("expires_at", &self.expires_at)
            .finish()
    }
}

/// Load origin-bound authorization state, creating and persisting its instance ID once.
pub fn load_or_create_auth_state(
    store: &EncryptedStateStore,
    key: &SecretBytes,
) -> Result<AuthState, AuthStateError> {
    if let Some(state) = load_auth_state(store, key)? {
        return Ok(state);
    }
    let state = AuthState::new();
    store.write(StateKind::Session, key, &state)?;
    Ok(state)
}

/// Read authorization state without creating local files for a status or logout command.
pub fn load_auth_state(
    store: &EncryptedStateStore,
    key: &SecretBytes,
) -> Result<Option<AuthState>, AuthStateError> {
    let state = store.read::<AuthState>(StateKind::Session, key)?;
    if let Some(state) = &state {
        state.validate()?;
    }
    Ok(state)
}

/// Persist validated authorization state in the origin-bound encrypted envelope.
pub fn save_auth_state(
    store: &EncryptedStateStore,
    key: &SecretBytes,
    state: &AuthState,
) -> Result<(), AuthStateError> {
    state.validate()?;
    store.write(StateKind::Session, key, state)?;
    Ok(())
}

/// Narrow API adapter for CLI device authorization routes.
#[derive(Clone, Copy, Debug)]
pub struct CliAuthApi<'a> {
    client: &'a ApiClient,
}

impl<'a> CliAuthApi<'a> {
    /// Bind authorization operations to an already validated, redirect-free API client.
    #[must_use]
    pub const fn new(client: &'a ApiClient) -> Self {
        Self { client }
    }

    /// Start browser approval for the caller-held device public key.
    pub fn start_enrollment(
        &self,
        identity: &DeviceIdentity,
        client_instance_id: ClientInstanceId,
        label: &str,
        idempotency_key: Uuid,
    ) -> Result<ApiResponse<CliEnrollment>, CliAuthError> {
        let request = CliEnrollmentCreateRequest {
            public_key: identity.public_key(),
            client_instance_id,
            label,
        };
        Ok(self.client.send_json(
            ApiRequest::new(ApiMethod::Post, "/v1/cli/auth/enrollments")
                .with_json(&request)?
                .with_idempotency_key(idempotency_key),
            None,
        )?)
    }

    /// Exchange browser approval after signing the exact enrollment challenge.
    pub fn exchange_enrollment(
        &self,
        identity: &DeviceIdentity,
        enrollment: &CliEnrollment,
        idempotency_key: Uuid,
    ) -> Result<ApiResponse<IssuedCliSession>, CliAuthError> {
        let message = enrollment_proof_message(enrollment.id, &enrollment.device_code)?;
        let request = CliEnrollmentExchangeRequest {
            device_code: enrollment.device_code.clone(),
            signature: identity.sign(&message),
        };
        Ok(self.client.send_json(
            ApiRequest::new(ApiMethod::Post, "/v1/cli/auth/enrollments/exchange")
                .with_json(&request)?
                .with_idempotency_key(idempotency_key),
            None,
        )?)
    }

    /// Request a one-time nonce for a previously enrolled device.
    pub fn start_session_challenge(
        &self,
        device_id: Uuid,
        idempotency_key: Uuid,
    ) -> Result<ApiResponse<CliSessionChallenge>, CliAuthError> {
        Ok(self.client.send_json(
            ApiRequest::new(ApiMethod::Post, "/v1/cli/auth/session-challenges")
                .with_json(&CliSessionChallengeRequest { device_id })?
                .with_idempotency_key(idempotency_key),
            None,
        )?)
    }

    /// Sign and consume one session nonce into a new short-lived bearer.
    pub fn exchange_session_challenge(
        &self,
        identity: &DeviceIdentity,
        challenge: &CliSessionChallenge,
        idempotency_key: Uuid,
    ) -> Result<ApiResponse<IssuedCliSession>, CliAuthError> {
        let message = session_proof_message(challenge.device_id, challenge.id, &challenge.nonce)?;
        let request = CliSessionExchangeRequest {
            device_id: challenge.device_id,
            challenge_id: challenge.id,
            nonce: challenge.nonce.clone(),
            signature: identity.sign(&message),
        };
        Ok(self.client.send_json(
            ApiRequest::new(ApiMethod::Post, "/v1/cli/auth/sessions")
                .with_json(&request)?
                .with_idempotency_key(idempotency_key),
            None,
        )?)
    }

    /// Revoke the exact server session represented by the bearer token.
    pub fn revoke_current_session(
        &self,
        token: &SecretBytes,
        idempotency_key: Uuid,
    ) -> Result<ApiResponse<()>, CliAuthError> {
        Ok(self.client.send_no_content(
            ApiRequest::new(ApiMethod::Delete, "/v1/cli/auth/sessions/current")
                .with_idempotency_key(idempotency_key),
            Some(token),
        )?)
    }
}

/// Build the backend's byte-exact versioned enrollment proof message.
pub fn enrollment_proof_message(
    enrollment_id: Uuid,
    device_code: &str,
) -> Result<Vec<u8>, CliAuthError> {
    let mut message = b"squid-cli-enrollment-v1\0".to_vec();
    message.extend_from_slice(enrollment_id.as_bytes());
    append_length_prefixed(&mut message, device_code.as_bytes())?;
    Ok(message)
}

/// Build the backend's byte-exact versioned session proof message.
pub fn session_proof_message(
    device_id: Uuid,
    challenge_id: Uuid,
    nonce: &str,
) -> Result<Vec<u8>, CliAuthError> {
    let mut message = b"squid-cli-session-v1\0".to_vec();
    message.extend_from_slice(device_id.as_bytes());
    message.extend_from_slice(challenge_id.as_bytes());
    append_length_prefixed(&mut message, nonce.as_bytes())?;
    Ok(message)
}

fn append_length_prefixed(message: &mut Vec<u8>, value: &[u8]) -> Result<(), CliAuthError> {
    if value.len() > MAXIMUM_PROOF_VALUE_BYTES {
        return Err(CliAuthError::ProofValueTooLong);
    }
    let length = u16::try_from(value.len()).map_err(|_error| CliAuthError::ProofValueTooLong)?;
    message.extend_from_slice(&length.to_be_bytes());
    message.extend_from_slice(value);
    Ok(())
}

/// Authorization protocol or transport failure.
#[derive(Debug, Error)]
pub enum CliAuthError {
    #[error("CLI authorization transport failed: {0}")]
    Transport(#[from] TransportError),
    #[error("CLI authorization proof value exceeded the protocol limit")]
    ProofValueTooLong,
}

/// Invalid or unreadable encrypted authorization state.
#[derive(Debug, Error)]
pub enum AuthStateError {
    #[error("encrypted CLI authorization state failed: {0}")]
    Encrypted(#[from] EncryptedStateError),
    #[error("CLI authorization state schema {0} is unsupported")]
    UnsupportedSchema(u32),
    #[error("CLI authorization state contains an incomplete session")]
    InvalidState,
}

#[cfg(test)]
mod tests {
    use std::error::Error;
    use std::io::{self, Read, Write};
    use std::net::{TcpListener, TcpStream};
    use std::thread;

    use tempfile::tempdir;
    use uuid::Uuid;

    use super::{
        AuthState, CliAuthApi, enrollment_proof_message, load_or_create_auth_state,
        save_auth_state, session_proof_message,
    };
    use crate::credential::{DeviceIdentity, SecretBytes};
    use crate::encrypted_state::EncryptedStateStore;
    use crate::form::RendererCapabilities;
    use crate::locale::Locale;
    use crate::origin::ApiOrigin;
    use crate::profile::{Profile, ProfilePaths};
    use crate::transport::{ApiClient, ClientInstanceId};

    fn read_http_request(stream: &mut TcpStream) -> io::Result<String> {
        let mut request = Vec::new();
        let mut buffer = [0_u8; 2048];
        let (header_end, content_length) = loop {
            let length = stream.read(&mut buffer)?;
            if length == 0 || request.len() + length > 64 * 1024 {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "incomplete or excessive test request",
                ));
            }
            request.extend_from_slice(&buffer[..length]);
            if let Some(position) = request.windows(4).position(|window| window == b"\r\n\r\n") {
                let header_end = position + 4;
                let headers = String::from_utf8_lossy(&request[..header_end]);
                let content_length = headers
                    .lines()
                    .find_map(|line| {
                        let (name, value) = line.split_once(':')?;
                        name.eq_ignore_ascii_case("content-length")
                            .then(|| value.trim().parse::<usize>().ok())
                            .flatten()
                    })
                    .unwrap_or(0);
                break (header_end, content_length);
            }
        };
        while request.len() < header_end + content_length {
            let length = stream.read(&mut buffer)?;
            if length == 0 || request.len() + length > 64 * 1024 {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "incomplete or excessive test request body",
                ));
            }
            request.extend_from_slice(&buffer[..length]);
        }
        String::from_utf8(request)
            .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))
    }

    fn write_json_response(stream: &mut TcpStream, status: &str, body: &str) -> io::Result<()> {
        write!(
            stream,
            "HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
            body.len(),
        )
    }

    #[test]
    fn proof_messages_match_the_python_wire_contract() -> Result<(), Box<dyn Error>> {
        let enrollment = enrollment_proof_message(
            "11111111-2222-4333-8444-555555555555".parse()?,
            "abc_DEF-123",
        )?;
        assert_eq!(
            hex::encode(enrollment),
            "73717569642d636c692d656e726f6c6c6d656e742d76310011111111222243338444555555555555000b6162635f4445462d313233",
        );
        let session = session_proof_message(
            "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee".parse()?,
            "01234567-89ab-4cde-8fab-0123456789ab".parse()?,
            "nonce_value-123",
        )?;
        assert_eq!(
            hex::encode(session),
            "73717569642d636c692d73657373696f6e2d763100aaaaaaaabbbb4ccc8dddeeeeeeeeeeee0123456789ab4cde8fab0123456789ab000f6e6f6e63655f76616c75652d313233",
        );
        Ok(())
    }

    #[test]
    fn encrypted_state_keeps_a_stable_instance_and_redacts_tokens() -> Result<(), Box<dyn Error>> {
        let directory = tempdir()?;
        let origin = ApiOrigin::parse("https://example.com")?;
        let store = EncryptedStateStore::new(&ProfilePaths::under(directory.path()), &origin);
        let key = SecretBytes::new(vec![7; 32]);
        let first = load_or_create_auth_state(&store, &key)?;
        let instance_id = first.client_instance_id();
        save_auth_state(&store, &key, &first)?;
        let second = load_or_create_auth_state(&store, &key)?;
        assert_eq!(second.client_instance_id(), instance_id);
        assert!(format!("{second:?}").contains("token: None"));
        Ok(())
    }

    #[test]
    fn new_state_has_no_account_session() {
        let state = AuthState::new();
        assert!(state.device_id().is_none());
        assert!(state.session_id().is_none());
        assert!(state.session_token().is_none());
    }

    #[test]
    fn authorization_api_sends_only_public_proofs_and_revokes_with_the_bearer()
    -> Result<(), Box<dyn Error>> {
        let listener = TcpListener::bind("127.0.0.1:0")?;
        let address = listener.local_addr()?;
        let server = thread::spawn(move || -> io::Result<Vec<String>> {
            let mut requests = Vec::new();
            let (mut enrollment_stream, _peer) = listener.accept()?;
            requests.push(read_http_request(&mut enrollment_stream)?);
            write_json_response(
                &mut enrollment_stream,
                "201 Created",
                r##"{"id":"11111111-2222-4333-8444-555555555555","device_code":"abc_DEF-123","user_code":"ABCD-EFGH","verification_uri":"https://catalogue.test/cli/link","verification_uri_complete":"https://catalogue.test/cli/link#code=ABCD-EFGH","expires_at":"2026-08-12T00:10:00Z","polling_interval_seconds":3}"##,
            )?;

            let (mut exchange_stream, _peer) = listener.accept()?;
            requests.push(read_http_request(&mut exchange_stream)?);
            write_json_response(
                &mut exchange_stream,
                "200 OK",
                r#"{"device":{"id":"aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee","client_instance_id":"99999999-8888-4777-8666-555555555555","label":"Test CLI","public_key_fingerprint":"1234-5678-90AB-CDEF-1234","created_at":"2026-08-12T00:00:00Z","last_used_at":"2026-08-12T00:00:00Z","revoked_at":null},"session_id":"01234567-89ab-4cde-8fab-0123456789ab","token":"squid_cli_v1_0123456789abcdef0123456789abcdef_secret","expires_at":"2026-08-12T00:15:00Z"}"#,
            )?;

            let (mut revoke_stream, _peer) = listener.accept()?;
            requests.push(read_http_request(&mut revoke_stream)?);
            revoke_stream.write_all(
                b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
            )?;
            Ok(requests)
        });

        let profile = Profile::new(ApiOrigin::parse(&format!("http://{address}"))?);
        let instance = ClientInstanceId::parse("99999999-8888-4777-8666-555555555555")?;
        let client = ApiClient::for_profile(
            &profile,
            Locale::En,
            instance,
            &RendererCapabilities::prompt(false),
        )?;
        let identity = DeviceIdentity::from_secret(&SecretBytes::new(vec![7; 32]))?;
        let api = CliAuthApi::new(&client);
        let enrollment = api
            .start_enrollment(&identity, instance, "Test CLI", Uuid::new_v4())?
            .data;
        let issued = api
            .exchange_enrollment(&identity, &enrollment, Uuid::new_v4())?
            .data;
        assert!(format!("{issued:?}").contains("[REDACTED]"));
        api.revoke_current_session(&issued.token.to_secret(), Uuid::new_v4())?;

        let requests = server
            .join()
            .map_err(|_error| io::Error::other("test server panicked"))??;
        assert_eq!(requests.len(), 3);
        assert!(requests[0].starts_with("POST /v1/cli/auth/enrollments HTTP/1.1"));
        assert!(requests[0].contains(&identity.public_key()));
        assert!(!requests[0].to_ascii_lowercase().contains("account_id"));
        assert!(requests[1].starts_with("POST /v1/cli/auth/enrollments/exchange HTTP/1.1"));
        assert!(requests[1].contains("\"signature\":"));
        assert!(!requests[1].contains("private_key"));
        assert!(!requests[1].contains("session_token"));
        assert!(
            requests[2]
                .to_ascii_lowercase()
                .contains("authorization: bearer squid_cli_v1_")
        );
        Ok(())
    }
}
