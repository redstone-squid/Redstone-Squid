//! Fail-closed HTTP transport for one exact, explicitly trusted API origin.

use std::collections::BTreeMap;
use std::fs;
use std::io::{self, Read};
use std::path::Path;
use std::time::Duration;

use reqwest::StatusCode;
use reqwest::blocking::{Client, Response};
use reqwest::header::{
    ACCEPT, ACCEPT_LANGUAGE, AUTHORIZATION, CONTENT_TYPE, HeaderMap, HeaderValue, RETRY_AFTER,
    USER_AGENT,
};
use reqwest::redirect::Policy;
use reqwest::tls::Version;
use serde::Serialize;
use serde::de::DeserializeOwned;
use thiserror::Error;
use uuid::Uuid;
use zeroize::Zeroizing;

use crate::credential::SecretBytes;
use crate::form::RendererCapabilities;
use crate::locale::Locale;
use crate::origin::ApiOrigin;
use crate::profile::{Profile, ProfileError};
use crate::version::VersionInfo;

const MAXIMUM_CA_BYTES: u64 = 1024 * 1024;
const MAXIMUM_REQUEST_BYTES: usize = 8 * 1024 * 1024;
const MAXIMUM_RESPONSE_BYTES: u64 = 8 * 1024 * 1024;
const DEFAULT_CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
const DEFAULT_REQUEST_TIMEOUT: Duration = Duration::from_secs(60);
const PROTOCOL_HEADER: &str = "x-squid-protocol";
const CAPABILITIES_HEADER: &str = "x-squid-renderer-capabilities";
const INSTANCE_HEADER: &str = "x-squid-client-instance";
const REQUEST_ID_HEADER: &str = "x-request-id";
const IDEMPOTENCY_HEADER: &str = "idempotency-key";

/// Supported request methods; retry behavior can classify these without parsing strings.
#[derive(Clone, Copy, Debug, Eq, PartialEq, serde::Deserialize, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum ApiMethod {
    Get,
    Post,
    Put,
    Patch,
    Delete,
}

impl ApiMethod {
    const fn as_reqwest(self) -> reqwest::Method {
        match self {
            Self::Get => reqwest::Method::GET,
            Self::Post => reqwest::Method::POST,
            Self::Put => reqwest::Method::PUT,
            Self::Patch => reqwest::Method::PATCH,
            Self::Delete => reqwest::Method::DELETE,
        }
    }

    /// Whether RFC semantics make a retry safe without an idempotency key.
    #[must_use]
    pub const fn is_idempotent(self) -> bool {
        matches!(self, Self::Get | Self::Put | Self::Delete)
    }
}

/// A validated UUID identifying one CLI installation.
#[derive(Clone, Copy, Debug, Eq, PartialEq, serde::Deserialize, Serialize)]
#[serde(transparent)]
pub struct ClientInstanceId(Uuid);

impl ClientInstanceId {
    /// Generate a new opaque installation identifier.
    #[must_use]
    pub fn generate() -> Self {
        Self(Uuid::new_v4())
    }

    /// Validate a persisted installation identifier.
    pub fn parse(value: &str) -> Result<Self, TransportError> {
        Uuid::parse_str(value)
            .map(Self)
            .map_err(|_error| TransportError::InvalidInstanceId)
    }

    /// Hyphenated lowercase value used in request headers.
    #[must_use]
    pub fn as_string(self) -> String {
        self.0.hyphenated().to_string()
    }
}

/// One API request whose path is constrained to the same public origin.
pub struct ApiRequest {
    method: ApiMethod,
    path: String,
    body: Option<Vec<u8>>,
    idempotency_key: Option<Uuid>,
}

impl ApiRequest {
    /// Construct a body-free request.
    #[must_use]
    pub fn new(method: ApiMethod, path: impl Into<String>) -> Self {
        Self {
            method,
            path: path.into(),
            body: None,
            idempotency_key: None,
        }
    }

    /// Serialize a bounded JSON request body.
    pub fn with_json(mut self, value: &impl Serialize) -> Result<Self, TransportError> {
        let body = serde_json::to_vec(value).map_err(TransportError::SerializeJson)?;
        if body.len() > MAXIMUM_REQUEST_BYTES {
            return Err(TransportError::RequestTooLarge);
        }
        self.body = Some(body);
        Ok(self)
    }

    /// Attach an idempotency key generated or persisted by the recovery layer.
    #[must_use]
    pub const fn with_idempotency_key(mut self, value: Uuid) -> Self {
        self.idempotency_key = Some(value);
        self
    }

    /// Validate the same-origin routing and body limits before persistence or sending.
    pub fn validate(&self) -> Result<(), TransportError> {
        validate_endpoint_path(&self.path)?;
        if self
            .body
            .as_ref()
            .is_some_and(|body| body.len() > MAXIMUM_REQUEST_BYTES)
        {
            return Err(TransportError::RequestTooLarge);
        }
        Ok(())
    }
}

impl std::fmt::Debug for ApiRequest {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("ApiRequest")
            .field("method", &self.method)
            .field("path", &self.path)
            .field(
                "body",
                &self
                    .body
                    .as_ref()
                    .map(|body| format!("[{} bytes]", body.len())),
            )
            .field("idempotency_key", &self.idempotency_key)
            .finish()
    }
}

/// Successful response metadata plus the decoded JSON body.
#[derive(Debug)]
pub struct ApiResponse<T> {
    pub status: u16,
    pub data: T,
    pub request_id: Option<String>,
}

/// Stable problem shape authored by the backend.
#[derive(Clone, Debug, serde::Deserialize, Eq, PartialEq)]
pub struct ApiProblem {
    pub code: String,
    pub message: String,
    #[serde(default)]
    pub field_errors: BTreeMap<String, String>,
    #[serde(default)]
    pub retryable: bool,
    #[serde(default)]
    pub suggested_action: Option<String>,
}

#[derive(Debug, serde::Deserialize)]
struct ProblemEnvelope {
    error: ApiProblem,
    #[serde(default)]
    request_id: Option<String>,
}

/// Immutable client tied to one normalized origin, protocol, locale, and instance.
#[derive(Clone, Debug)]
pub struct ApiClient {
    origin: ApiOrigin,
    client: Client,
    default_headers: HeaderMap,
}

impl ApiClient {
    /// Build a redirect-free client only from an explicitly trusted, validated profile.
    pub fn for_profile(
        profile: &Profile,
        locale: Locale,
        instance: ClientInstanceId,
        renderer_capabilities: &RendererCapabilities,
    ) -> Result<Self, TransportError> {
        profile.validate().map_err(TransportError::InvalidProfile)?;
        Self::new(
            profile.origin.clone(),
            locale,
            instance,
            renderer_capabilities,
            profile.ca_certificate.as_deref(),
        )
    }

    fn new(
        origin: ApiOrigin,
        locale: Locale,
        instance: ClientInstanceId,
        renderer_capabilities: &RendererCapabilities,
        custom_ca: Option<&Path>,
    ) -> Result<Self, TransportError> {
        let version = VersionInfo::current();
        let mut default_headers = HeaderMap::new();
        default_headers.insert(ACCEPT, HeaderValue::from_static("application/json"));
        default_headers.insert(
            ACCEPT_LANGUAGE,
            HeaderValue::from_str(locale.code()).map_err(|_error| TransportError::InvalidHeader)?,
        );
        default_headers.insert(
            USER_AGENT,
            HeaderValue::from_str(&format!("squid/{}", version.cli_version))
                .map_err(|_error| TransportError::InvalidHeader)?,
        );
        default_headers.insert(
            PROTOCOL_HEADER,
            HeaderValue::from_str(&version.maximum_protocol.to_string())
                .map_err(|_error| TransportError::InvalidHeader)?,
        );
        default_headers.insert(
            CAPABILITIES_HEADER,
            HeaderValue::from_str(&renderer_capabilities.header_value())
                .map_err(|_error| TransportError::InvalidHeader)?,
        );
        default_headers.insert(
            INSTANCE_HEADER,
            HeaderValue::from_str(&instance.as_string())
                .map_err(|_error| TransportError::InvalidHeader)?,
        );

        let mut builder = Client::builder()
            .default_headers(default_headers.clone())
            .redirect(Policy::none())
            .no_proxy()
            .connect_timeout(DEFAULT_CONNECT_TIMEOUT)
            .timeout(DEFAULT_REQUEST_TIMEOUT)
            .min_tls_version(Version::TLS_1_2);
        if origin.scheme() == "https" {
            builder = builder.https_only(true);
        }
        if let Some(path) = custom_ca {
            builder = builder.add_root_certificate(read_ca_certificate(path)?);
        }
        let client = builder.build().map_err(TransportError::BuildClient)?;
        Ok(Self {
            origin,
            client,
            default_headers,
        })
    }

    /// Send and decode a JSON response without following redirects.
    pub fn send_json<T: DeserializeOwned>(
        &self,
        request: ApiRequest,
        bearer_token: Option<&SecretBytes>,
    ) -> Result<ApiResponse<T>, TransportError> {
        request.validate()?;
        let url = format!("{}{}", self.origin.as_str(), request.path);
        let mut builder = self.client.request(request.method.as_reqwest(), url);
        if let Some(body) = request.body {
            builder = builder
                .header(CONTENT_TYPE, HeaderValue::from_static("application/json"))
                .body(body);
        }
        if let Some(key) = request.idempotency_key {
            builder = builder.header(IDEMPOTENCY_HEADER, key.hyphenated().to_string());
        }
        if let Some(token) = bearer_token {
            let mut combined = Zeroizing::new(b"Bearer ".to_vec());
            combined.extend_from_slice(token.expose());
            let mut combined = HeaderValue::from_bytes(&combined)
                .map_err(|_error| TransportError::InvalidBearerToken)?;
            combined.set_sensitive(true);
            builder = builder.header(AUTHORIZATION, combined);
        }
        let response = builder.send().map_err(TransportError::Request)?;
        self.decode_response(response)
    }

    /// Headers fixed at construction, exposed only for contract tests and diagnostics.
    #[must_use]
    pub const fn default_headers(&self) -> &HeaderMap {
        &self.default_headers
    }

    fn decode_response<T: DeserializeOwned>(
        &self,
        response: Response,
    ) -> Result<ApiResponse<T>, TransportError> {
        if response.url().scheme() != self.origin.scheme()
            || response.url().host_str() != origin_host(&self.origin)
            || response.url().port_or_known_default() != Some(self.origin.port())
        {
            return Err(TransportError::CrossOriginResponse);
        }
        let status = response.status();
        let request_id = response
            .headers()
            .get(REQUEST_ID_HEADER)
            .and_then(|value| value.to_str().ok())
            .map(String::from);
        let retry_after = response
            .headers()
            .get(RETRY_AFTER)
            .and_then(|value| value.to_str().ok())
            .map(String::from);
        let content_type = response
            .headers()
            .get(CONTENT_TYPE)
            .and_then(|value| value.to_str().ok())
            .map(String::from);
        let body = read_bounded(response)?;
        if !body.is_empty() && !content_type.as_deref().is_some_and(is_json_content_type) {
            return Err(TransportError::InvalidContentType);
        }
        if !status.is_success() {
            let envelope = serde_json::from_slice::<ProblemEnvelope>(&body).ok();
            return Err(TransportError::Http {
                status: status.as_u16(),
                problem: envelope.as_ref().map(|value| value.error.clone()),
                request_id: envelope.and_then(|value| value.request_id).or(request_id),
                retry_after,
            });
        }
        let data = serde_json::from_slice(&body).map_err(TransportError::InvalidJson)?;
        Ok(ApiResponse {
            status: status.as_u16(),
            data,
            request_id,
        })
    }
}

fn origin_host(origin: &ApiOrigin) -> Option<&str> {
    origin
        .as_str()
        .split_once("://")
        .map(|(_scheme, authority)| authority)
        .and_then(|authority| authority.rsplit_once(':').map(|(host, _port)| host))
        .map(|host| host.trim_matches(['[', ']']))
}

fn validate_endpoint_path(path: &str) -> Result<(), TransportError> {
    if !path.starts_with("/api/v1/")
        || path.contains("//")
        || path.contains('\\')
        || path.contains('?')
        || path.contains('#')
        || path
            .split('/')
            .any(|segment| segment == "." || segment == "..")
        || !path
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'/' | b'-' | b'_' | b'.'))
    {
        return Err(TransportError::InvalidEndpointPath);
    }
    Ok(())
}

fn is_json_content_type(value: &str) -> bool {
    let essence = value
        .split_once(';')
        .map_or(value, |(essence, _parameters)| essence)
        .trim();
    essence.eq_ignore_ascii_case("application/json")
        || essence.eq_ignore_ascii_case("application/problem+json")
}

fn read_ca_certificate(path: &Path) -> Result<reqwest::Certificate, TransportError> {
    let metadata = fs::symlink_metadata(path).map_err(TransportError::Io)?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(TransportError::InvalidCaCertificate);
    }
    if metadata.len() > MAXIMUM_CA_BYTES {
        return Err(TransportError::CaCertificateTooLarge);
    }
    let file = fs::File::open(path).map_err(TransportError::Io)?;
    let mut bytes = Vec::new();
    file.take(MAXIMUM_CA_BYTES + 1)
        .read_to_end(&mut bytes)
        .map_err(TransportError::Io)?;
    if bytes.len() as u64 > MAXIMUM_CA_BYTES {
        return Err(TransportError::CaCertificateTooLarge);
    }
    reqwest::Certificate::from_pem(&bytes).map_err(TransportError::ParseCaCertificate)
}

fn read_bounded(response: Response) -> Result<Vec<u8>, TransportError> {
    if response
        .content_length()
        .is_some_and(|length| length > MAXIMUM_RESPONSE_BYTES)
    {
        return Err(TransportError::ResponseTooLarge);
    }
    let mut body = Vec::new();
    response
        .take(MAXIMUM_RESPONSE_BYTES + 1)
        .read_to_end(&mut body)
        .map_err(TransportError::Io)?;
    if body.len() as u64 > MAXIMUM_RESPONSE_BYTES {
        return Err(TransportError::ResponseTooLarge);
    }
    Ok(body)
}

/// Request construction, TLS, network, bounded-response, or backend HTTP failure.
#[derive(Debug, Error)]
pub enum TransportError {
    #[error("client instance ID must be a UUID")]
    InvalidInstanceId,
    #[error("API profile is not trusted or valid: {0}")]
    InvalidProfile(#[source] ProfileError),
    #[error("endpoint must be a normalized /api/v1/ path without a query or fragment")]
    InvalidEndpointPath,
    #[error("request header value is invalid")]
    InvalidHeader,
    #[error("bearer token cannot be represented safely in an HTTP header")]
    InvalidBearerToken,
    #[error("JSON request exceeded eight MiB")]
    RequestTooLarge,
    #[error("custom CA certificate must be a regular non-symlink file")]
    InvalidCaCertificate,
    #[error("custom CA certificate exceeds one MiB")]
    CaCertificateTooLarge,
    #[error("custom CA certificate could not be parsed: {0}")]
    ParseCaCertificate(#[source] reqwest::Error),
    #[error("HTTP client could not be configured: {0}")]
    BuildClient(#[source] reqwest::Error),
    #[error("HTTP request failed: {0}")]
    Request(#[source] reqwest::Error),
    #[error("HTTP response came from a different origin")]
    CrossOriginResponse,
    #[error("HTTP response exceeded eight MiB")]
    ResponseTooLarge,
    #[error("non-empty HTTP response did not use a JSON content type")]
    InvalidContentType,
    #[error("HTTP response was not valid JSON: {0}")]
    InvalidJson(#[source] serde_json::Error),
    #[error("JSON request could not be serialized: {0}")]
    SerializeJson(#[source] serde_json::Error),
    #[error("backend returned HTTP {status}")]
    Http {
        status: u16,
        problem: Option<ApiProblem>,
        request_id: Option<String>,
        retry_after: Option<String>,
    },
    #[error("local transport input or output failed: {0}")]
    Io(#[source] io::Error),
}

/// Map an HTTP status to the CLI's stable error class without interpreting localized text.
#[must_use]
pub const fn status_class(status: StatusCode) -> crate::exit::ExitStatus {
    match status.as_u16() {
        401 => crate::exit::ExitStatus::Authentication,
        403 => crate::exit::ExitStatus::Authorization,
        409 => crate::exit::ExitStatus::Conflict,
        429 => crate::exit::ExitStatus::RateLimited,
        500..=599 => crate::exit::ExitStatus::Unavailable,
        _ => crate::exit::ExitStatus::ServerRejection,
    }
}

#[cfg(test)]
mod tests {
    use std::error::Error;
    use std::io;
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::thread;

    use serde::Deserialize;
    use serde_json::Value;

    use super::{
        ApiClient, ApiMethod, ApiRequest, ClientInstanceId, TransportError, status_class,
        validate_endpoint_path,
    };
    use crate::exit::ExitStatus;
    use crate::form::RendererCapabilities;
    use crate::locale::Locale;
    use crate::origin::ApiOrigin;
    use crate::profile::Profile;

    #[derive(Debug, Deserialize, Eq, PartialEq)]
    struct ResponseBody {
        ok: bool,
    }

    #[test]
    fn rejects_paths_that_could_change_origin_or_routing() {
        assert!(validate_endpoint_path("/api/v1/drafts").is_ok());
        for invalid in [
            "https://evil.example/api/v1/drafts",
            "/api/v1/../admin",
            "/api/v1/drafts?token=x",
            "/api/v1//drafts",
            "/v1/drafts",
        ] {
            assert!(
                validate_endpoint_path(invalid).is_err(),
                "accepted {invalid}"
            );
        }
    }

    #[test]
    fn refuses_untrusted_profiles_and_redacts_request_bodies() -> Result<(), Box<dyn Error>> {
        let mut profile = Profile::new(ApiOrigin::parse("https://example.com")?);
        profile.trusted = false;
        assert!(matches!(
            ApiClient::for_profile(
                &profile,
                Locale::En,
                ClientInstanceId::generate(),
                &RendererCapabilities::prompt(false),
            ),
            Err(TransportError::InvalidProfile(_)),
        ));
        let request = ApiRequest::new(ApiMethod::Post, "/api/v1/drafts")
            .with_json(&serde_json::json!({"private": "do-not-print"}))?;
        let debug = format!("{request:?}");
        assert!(!debug.contains("do-not-print"));
        assert!(debug.contains("[26 bytes]"));
        Ok(())
    }

    #[test]
    fn maps_http_statuses_to_stable_exit_classes() {
        assert_eq!(
            status_class(reqwest::StatusCode::UNAUTHORIZED),
            ExitStatus::Authentication
        );
        assert_eq!(
            status_class(reqwest::StatusCode::FORBIDDEN),
            ExitStatus::Authorization
        );
        assert_eq!(
            status_class(reqwest::StatusCode::CONFLICT),
            ExitStatus::Conflict
        );
        assert_eq!(
            status_class(reqwest::StatusCode::TOO_MANY_REQUESTS),
            ExitStatus::RateLimited
        );
        assert_eq!(
            status_class(reqwest::StatusCode::SERVICE_UNAVAILABLE),
            ExitStatus::Unavailable,
        );
    }

    #[test]
    fn sends_required_headers_and_decodes_json() -> Result<(), Box<dyn Error>> {
        let listener = TcpListener::bind("127.0.0.1:0")?;
        let address = listener.local_addr()?;
        let server = thread::spawn(move || -> io::Result<String> {
            let (mut stream, _peer) = listener.accept()?;
            let mut request = vec![0_u8; 4096];
            let length = stream.read(&mut request)?;
            let request = String::from_utf8_lossy(&request[..length]).into_owned();
            stream.write_all(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nX-Request-Id: req-7\r\nContent-Length: 11\r\nConnection: close\r\n\r\n{\"ok\":true}",
            )?;
            Ok(request)
        });
        let origin = ApiOrigin::parse(&format!("http://{address}"))?;
        let profile = Profile::new(origin);
        let client = ApiClient::for_profile(
            &profile,
            Locale::En,
            ClientInstanceId::generate(),
            &RendererCapabilities::prompt(false),
        )?;
        let response = client.send_json::<ResponseBody>(
            ApiRequest::new(ApiMethod::Get, "/api/v1/capabilities"),
            None,
        )?;
        assert_eq!(response.data, ResponseBody { ok: true });
        assert_eq!(response.request_id.as_deref(), Some("req-7"));
        let request = server
            .join()
            .map_err(|_error| io::Error::other("test server panicked"))??;
        let lowercase = request.to_ascii_lowercase();
        assert!(lowercase.contains("x-squid-protocol:"));
        assert!(lowercase.contains("x-squid-renderer-capabilities:"));
        assert!(lowercase.contains("cli.control.text.v1"));
        assert!(!lowercase.contains("cli.handoff.v1"));
        assert!(lowercase.contains("x-squid-client-instance:"));
        assert!(lowercase.contains("accept-language: en"));
        Ok(())
    }

    #[test]
    fn does_not_follow_redirects() -> Result<(), Box<dyn Error>> {
        let listener = TcpListener::bind("127.0.0.1:0")?;
        let address = listener.local_addr()?;
        let server = thread::spawn(move || -> io::Result<()> {
            let (mut stream, _peer) = listener.accept()?;
            let mut request = [0_u8; 1024];
            let _length = stream.read(&mut request)?;
            stream.write_all(
                b"HTTP/1.1 302 Found\r\nLocation: https://evil.example/\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
            )
        });
        let origin = ApiOrigin::parse(&format!("http://{address}"))?;
        let profile = Profile::new(origin);
        let client = ApiClient::for_profile(
            &profile,
            Locale::En,
            ClientInstanceId::generate(),
            &RendererCapabilities::prompt(false),
        )?;
        let result = client.send_json::<Value>(
            ApiRequest::new(ApiMethod::Get, "/api/v1/capabilities"),
            None,
        );
        assert!(matches!(
            result,
            Err(TransportError::Http { status: 302, .. })
        ));
        server
            .join()
            .map_err(|_error| io::Error::other("test server panicked"))??;
        Ok(())
    }
}
