//! Exact API-origin normalization and trust boundaries.

use std::fmt;
use std::net::IpAddr;
use std::str::FromStr;

use serde::{Deserialize, Deserializer, Serialize, Serializer};
use sha2::{Digest, Sha256};
use thiserror::Error;
use url::{Host, Url};

/// One normalized API origin with no path, user information, query, or fragment.
#[derive(Clone, Debug, Eq, Hash, PartialEq)]
pub struct ApiOrigin {
    normalized: String,
    storage_key: String,
    scheme: OriginScheme,
    host: OriginHost,
    port: u16,
}

impl ApiOrigin {
    /// Parse and normalize an API origin, enforcing HTTPS outside literal loopback.
    pub fn parse(value: &str) -> Result<Self, OriginError> {
        let parsed = Url::parse(value).map_err(|_error| OriginError::InvalidUrl)?;
        if !parsed.username().is_empty() || parsed.password().is_some() {
            return Err(OriginError::CredentialsNotAllowed);
        }
        if parsed.path() != "/" || parsed.query().is_some() || parsed.fragment().is_some() {
            return Err(OriginError::OriginOnly);
        }
        let scheme = OriginScheme::from_str(parsed.scheme())?;
        let host = parsed
            .host()
            .ok_or(OriginError::MissingHost)
            .map(OriginHost::from)?;
        let port = parsed
            .port_or_known_default()
            .ok_or(OriginError::MissingPort)?;
        if scheme == OriginScheme::Http && !host.is_loopback() {
            return Err(OriginError::HttpsRequired);
        }
        let normalized = format!("{}://{}:{port}", scheme.as_str(), host.render());
        let storage_key = hex::encode(Sha256::digest(normalized.as_bytes()));
        Ok(Self {
            normalized,
            storage_key,
            scheme,
            host,
            port,
        })
    }

    /// Exact normalized origin used for request authorization checks.
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.normalized
    }

    /// SHA-256 namespace used for local credential and cache keys.
    #[must_use]
    pub fn storage_key(&self) -> &str {
        &self.storage_key
    }

    /// Return whether this is a literal loopback development origin.
    #[must_use]
    pub fn is_loopback(&self) -> bool {
        self.host.is_loopback()
    }

    /// Return the normalized TCP port.
    #[must_use]
    pub const fn port(&self) -> u16 {
        self.port
    }

    /// Return the normalized scheme.
    #[must_use]
    pub const fn scheme(&self) -> &'static str {
        self.scheme.as_str()
    }
}

impl fmt::Display for ApiOrigin {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.as_str())
    }
}

impl FromStr for ApiOrigin {
    type Err = OriginError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        Self::parse(value)
    }
}

impl Serialize for ApiOrigin {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        serializer.serialize_str(self.as_str())
    }
}

impl<'de> Deserialize<'de> for ApiOrigin {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        let value = String::deserialize(deserializer)?;
        Self::parse(&value).map_err(serde::de::Error::custom)
    }
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
enum OriginScheme {
    Http,
    Https,
}

impl OriginScheme {
    const fn as_str(self) -> &'static str {
        match self {
            Self::Http => "http",
            Self::Https => "https",
        }
    }
}

impl FromStr for OriginScheme {
    type Err = OriginError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        match value {
            "http" => Ok(Self::Http),
            "https" => Ok(Self::Https),
            _ => Err(OriginError::UnsupportedScheme),
        }
    }
}

#[derive(Clone, Debug, Eq, Hash, PartialEq)]
enum OriginHost {
    Domain(String),
    Ip(IpAddr),
}

impl OriginHost {
    fn is_loopback(&self) -> bool {
        matches!(self, Self::Ip(address) if address.is_loopback())
    }

    fn render(&self) -> String {
        match self {
            Self::Domain(value) => value.clone(),
            Self::Ip(IpAddr::V4(value)) => value.to_string(),
            Self::Ip(IpAddr::V6(value)) => format!("[{value}]"),
        }
    }
}

impl From<Host<&str>> for OriginHost {
    fn from(value: Host<&str>) -> Self {
        match value {
            Host::Domain(domain) => Self::Domain(domain.to_ascii_lowercase()),
            Host::Ipv4(address) => Self::Ip(IpAddr::V4(address)),
            Host::Ipv6(address) => Self::Ip(IpAddr::V6(address)),
        }
    }
}

/// Invalid or unsafe API origin.
#[derive(Clone, Copy, Debug, Eq, Error, PartialEq)]
pub enum OriginError {
    #[error("origin is not a valid absolute URL")]
    InvalidUrl,
    #[error("origin scheme must be http or https")]
    UnsupportedScheme,
    #[error("origin must not contain a username or password")]
    CredentialsNotAllowed,
    #[error("origin must not contain a path, query, or fragment")]
    OriginOnly,
    #[error("origin must contain a host")]
    MissingHost,
    #[error("origin port could not be resolved")]
    MissingPort,
    #[error("HTTPS is required outside literal loopback development origins")]
    HttpsRequired,
}

#[cfg(test)]
mod tests {
    use super::{ApiOrigin, OriginError};

    #[test]
    fn normalizes_default_ports_and_idna() {
        let origin = ApiOrigin::parse("https://BÜCHER.example");
        assert!(origin.is_ok(), "origin failed: {origin:?}");
        if let Ok(origin) = origin {
            assert_eq!(origin.as_str(), "https://xn--bcher-kva.example:443");
            assert_eq!(origin.storage_key().len(), 64);
        }
    }

    #[test]
    fn permits_literal_loopback_http() {
        let ipv4 = ApiOrigin::parse("http://127.0.0.1:8000");
        assert!(ipv4.is_ok(), "IPv4 loopback failed: {ipv4:?}");
        let ipv6 = ApiOrigin::parse("http://[::1]:8000");
        assert!(ipv6.is_ok(), "IPv6 loopback failed: {ipv6:?}");
    }

    #[test]
    fn rejects_hostname_http_even_when_named_localhost() {
        assert_eq!(
            ApiOrigin::parse("http://localhost:8000"),
            Err(OriginError::HttpsRequired)
        );
        assert_eq!(
            ApiOrigin::parse("http://example.com"),
            Err(OriginError::HttpsRequired)
        );
    }

    #[test]
    fn rejects_non_origin_components() {
        assert_eq!(
            ApiOrigin::parse("https://user@example.com"),
            Err(OriginError::CredentialsNotAllowed),
        );
        assert_eq!(
            ApiOrigin::parse("https://example.com/api"),
            Err(OriginError::OriginOnly),
        );
        assert_eq!(
            ApiOrigin::parse("https://example.com?token=x"),
            Err(OriginError::OriginOnly),
        );
    }

    #[test]
    fn storage_key_is_origin_specific() {
        let first = ApiOrigin::parse("https://example.com");
        let second = ApiOrigin::parse("https://example.com:8443");
        assert!(first.is_ok() && second.is_ok());
        if let (Ok(first), Ok(second)) = (first, second) {
            assert_ne!(first.storage_key(), second.storage_key());
        }
    }
}
