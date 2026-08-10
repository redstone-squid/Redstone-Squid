//! Cryptographically verified, notification-only release metadata.

use std::collections::BTreeMap;
use std::io::{self, Read};

use base64::Engine;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use ed25519_dalek::{Signature, VerifyingKey};
use semver::Version;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use thiserror::Error;
use url::Url;

const UPDATE_SCHEMA_VERSION: u32 = 1;
const MAXIMUM_ENVELOPE_BYTES: usize = 1024 * 1024;
const MAXIMUM_PAYLOAD_BYTES: usize = 512 * 1024;
const MAXIMUM_ARTIFACT_BYTES: u64 = 512 * 1024 * 1024;

/// Signed channel authored by the Redstone Squid release process.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ReleaseChannel {
    Stable,
    Beta,
}

/// One checksummed native artifact in a signed release manifest.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct UpdateArtifact {
    pub url: String,
    pub sha256: String,
    pub size_bytes: u64,
}

/// Exact payload covered by the detached Ed25519 signature.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct UpdateManifest {
    pub schema_version: u32,
    pub channel: ReleaseChannel,
    pub version: String,
    pub published_at_unix_seconds: u64,
    pub minimum_protocol: u32,
    pub maximum_protocol: u32,
    pub artifacts: BTreeMap<String, UpdateArtifact>,
}

/// Base64url envelope; `payload` is verified byte-for-byte before JSON parsing.
#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct SignedManifestEnvelope {
    payload: String,
    signature: String,
}

/// Result of comparing verified release metadata with the running CLI.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum UpdateDecision {
    UpToDate,
    Available {
        version: Version,
        artifact: UpdateArtifact,
    },
    ProtocolIncompatible {
        version: Version,
        minimum_protocol: u32,
        maximum_protocol: u32,
    },
}

/// Verifier initialized from a pinned release-signing public key.
#[derive(Clone, Debug)]
pub struct UpdateVerifier {
    key: VerifyingKey,
}

impl UpdateVerifier {
    /// Parse one URL-safe base64 Ed25519 public key supplied by release configuration.
    pub fn new(public_key: &str) -> Result<Self, UpdateError> {
        let decoded = URL_SAFE_NO_PAD
            .decode(public_key)
            .map_err(|_error| UpdateError::InvalidPublicKey)?;
        let bytes: [u8; 32] = decoded
            .try_into()
            .map_err(|_error| UpdateError::InvalidPublicKey)?;
        let key =
            VerifyingKey::from_bytes(&bytes).map_err(|_error| UpdateError::InvalidPublicKey)?;
        Ok(Self { key })
    }

    /// Authenticate a bounded envelope, then parse and semantically validate its payload.
    pub fn verify_manifest(&self, envelope: &[u8]) -> Result<UpdateManifest, UpdateError> {
        if envelope.len() > MAXIMUM_ENVELOPE_BYTES {
            return Err(UpdateError::EnvelopeTooLarge);
        }
        let envelope = serde_json::from_slice::<SignedManifestEnvelope>(envelope)
            .map_err(UpdateError::InvalidEnvelope)?;
        let payload = URL_SAFE_NO_PAD
            .decode(envelope.payload)
            .map_err(|_error| UpdateError::InvalidEnvelopeEncoding)?;
        if payload.len() > MAXIMUM_PAYLOAD_BYTES {
            return Err(UpdateError::PayloadTooLarge);
        }
        let signature = URL_SAFE_NO_PAD
            .decode(envelope.signature)
            .map_err(|_error| UpdateError::InvalidEnvelopeEncoding)?;
        let signature = Signature::from_slice(&signature)
            .map_err(|_error| UpdateError::InvalidSignatureEncoding)?;
        self.key
            .verify_strict(&payload, &signature)
            .map_err(|_error| UpdateError::SignatureRejected)?;
        let manifest = serde_json::from_slice::<UpdateManifest>(&payload)
            .map_err(UpdateError::InvalidPayload)?;
        validate_manifest(&manifest)?;
        Ok(manifest)
    }

    /// Decide whether to notify, without downloading or replacing the executable.
    pub fn decide(
        &self,
        manifest: &UpdateManifest,
        running_version: &str,
        running_protocol: u32,
        target: &str,
    ) -> Result<UpdateDecision, UpdateError> {
        validate_manifest(manifest)?;
        let available = Version::parse(&manifest.version).map_err(UpdateError::InvalidVersion)?;
        let running = Version::parse(running_version).map_err(UpdateError::InvalidVersion)?;
        if available <= running {
            return Ok(UpdateDecision::UpToDate);
        }
        if !(manifest.minimum_protocol..=manifest.maximum_protocol).contains(&running_protocol) {
            return Ok(UpdateDecision::ProtocolIncompatible {
                version: available,
                minimum_protocol: manifest.minimum_protocol,
                maximum_protocol: manifest.maximum_protocol,
            });
        }
        let artifact = manifest
            .artifacts
            .get(target)
            .cloned()
            .ok_or(UpdateError::TargetMissing)?;
        Ok(UpdateDecision::Available {
            version: available,
            artifact,
        })
    }
}

/// Stream and verify a downloaded artifact without trusting its filename or metadata.
pub fn verify_artifact(
    mut reader: impl Read,
    expected: &UpdateArtifact,
) -> Result<(), UpdateError> {
    validate_artifact(expected)?;
    let mut digest = Sha256::new();
    let mut total = 0_u64;
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        let read = reader.read(&mut buffer).map_err(UpdateError::Io)?;
        if read == 0 {
            break;
        }
        total = total
            .checked_add(read as u64)
            .ok_or(UpdateError::ArtifactTooLarge)?;
        if total > expected.size_bytes || total > MAXIMUM_ARTIFACT_BYTES {
            return Err(UpdateError::ArtifactSizeMismatch);
        }
        digest.update(&buffer[..read]);
    }
    if total != expected.size_bytes {
        return Err(UpdateError::ArtifactSizeMismatch);
    }
    if hex::encode(digest.finalize()) != expected.sha256 {
        return Err(UpdateError::ArtifactHashMismatch);
    }
    Ok(())
}

fn validate_manifest(manifest: &UpdateManifest) -> Result<(), UpdateError> {
    if manifest.schema_version != UPDATE_SCHEMA_VERSION {
        return Err(UpdateError::UnsupportedSchema(manifest.schema_version));
    }
    Version::parse(&manifest.version).map_err(UpdateError::InvalidVersion)?;
    if manifest.published_at_unix_seconds == 0
        || manifest.minimum_protocol == 0
        || manifest.minimum_protocol > manifest.maximum_protocol
        || manifest.artifacts.is_empty()
    {
        return Err(UpdateError::InvalidManifest);
    }
    for (target, artifact) in &manifest.artifacts {
        if !valid_target(target) {
            return Err(UpdateError::InvalidTarget);
        }
        validate_artifact(artifact)?;
    }
    Ok(())
}

fn validate_artifact(artifact: &UpdateArtifact) -> Result<(), UpdateError> {
    if artifact.size_bytes == 0 || artifact.size_bytes > MAXIMUM_ARTIFACT_BYTES {
        return Err(UpdateError::ArtifactTooLarge);
    }
    if artifact.sha256.len() != 64
        || !artifact
            .sha256
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(UpdateError::InvalidArtifactHash);
    }
    let url = Url::parse(&artifact.url).map_err(|_error| UpdateError::InvalidArtifactUrl)?;
    if url.scheme() != "https"
        || url.host().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.fragment().is_some()
    {
        return Err(UpdateError::InvalidArtifactUrl);
    }
    Ok(())
}

fn valid_target(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.'))
}

/// Signed update metadata or artifact-integrity failure.
#[derive(Debug, Error)]
pub enum UpdateError {
    #[error("release-signing public key is invalid")]
    InvalidPublicKey,
    #[error("signed update envelope exceeds one MiB")]
    EnvelopeTooLarge,
    #[error("signed update envelope is invalid: {0}")]
    InvalidEnvelope(#[source] serde_json::Error),
    #[error("signed update envelope contains invalid base64url")]
    InvalidEnvelopeEncoding,
    #[error("signed update payload exceeds 512 KiB")]
    PayloadTooLarge,
    #[error("update signature encoding is invalid")]
    InvalidSignatureEncoding,
    #[error("update signature was rejected")]
    SignatureRejected,
    #[error("signed update payload is invalid: {0}")]
    InvalidPayload(#[source] serde_json::Error),
    #[error("update manifest schema {0} is unsupported")]
    UnsupportedSchema(u32),
    #[error("update manifest version is invalid: {0}")]
    InvalidVersion(#[source] semver::Error),
    #[error("update manifest fields are invalid")]
    InvalidManifest,
    #[error("update artifact target is invalid")]
    InvalidTarget,
    #[error("update manifest does not include this target")]
    TargetMissing,
    #[error("update artifact URL must be HTTPS without credentials or a fragment")]
    InvalidArtifactUrl,
    #[error("update artifact SHA-256 must be lowercase hexadecimal")]
    InvalidArtifactHash,
    #[error("update artifact exceeds the supported size")]
    ArtifactTooLarge,
    #[error("update artifact size does not match the signed manifest")]
    ArtifactSizeMismatch,
    #[error("update artifact SHA-256 does not match the signed manifest")]
    ArtifactHashMismatch,
    #[error("update artifact could not be read: {0}")]
    Io(#[source] io::Error),
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;
    use std::error::Error;

    use base64::Engine;
    use base64::engine::general_purpose::URL_SAFE_NO_PAD;
    use ed25519_dalek::{Signer, SigningKey};
    use sha2::{Digest, Sha256};

    use super::{
        ReleaseChannel, SignedManifestEnvelope, UpdateArtifact, UpdateDecision, UpdateError,
        UpdateManifest, UpdateVerifier, verify_artifact,
    };

    fn signing_key() -> SigningKey {
        SigningKey::from_bytes(&[7; 32])
    }

    fn artifact(contents: &[u8]) -> UpdateArtifact {
        UpdateArtifact {
            url: String::from("https://github.com/redstone-squid/releases/download/squid"),
            sha256: hex::encode(Sha256::digest(contents)),
            size_bytes: contents.len() as u64,
        }
    }

    fn signed_manifest() -> Result<(UpdateVerifier, Vec<u8>, UpdateManifest), Box<dyn Error>> {
        let manifest = UpdateManifest {
            schema_version: 1,
            channel: ReleaseChannel::Stable,
            version: String::from("1.2.0"),
            published_at_unix_seconds: 1_800_000_000,
            minimum_protocol: 1,
            maximum_protocol: 2,
            artifacts: BTreeMap::from([(String::from("test-target"), artifact(b"binary"))]),
        };
        let payload = serde_json::to_vec(&manifest)?;
        let signing_key = signing_key();
        let signature = signing_key.sign(&payload);
        let envelope = serde_json::to_vec(&SignedManifestEnvelope {
            payload: URL_SAFE_NO_PAD.encode(&payload),
            signature: URL_SAFE_NO_PAD.encode(signature.to_bytes()),
        })?;
        let verifier =
            UpdateVerifier::new(&URL_SAFE_NO_PAD.encode(signing_key.verifying_key().as_bytes()))?;
        Ok((verifier, envelope, manifest))
    }

    #[test]
    fn authenticates_manifest_before_deciding() -> Result<(), Box<dyn Error>> {
        let (verifier, envelope, manifest) = signed_manifest()?;
        assert_eq!(verifier.verify_manifest(&envelope)?, manifest);
        assert_eq!(
            verifier.decide(&manifest, "1.1.0", 1, "test-target")?,
            UpdateDecision::Available {
                version: semver::Version::parse("1.2.0")?,
                artifact: artifact(b"binary"),
            },
        );
        Ok(())
    }

    #[test]
    fn rejects_any_signed_payload_tampering() -> Result<(), Box<dyn Error>> {
        let (verifier, envelope, _manifest) = signed_manifest()?;
        let mut envelope = serde_json::from_slice::<SignedManifestEnvelope>(&envelope)?;
        let mut payload = URL_SAFE_NO_PAD.decode(&envelope.payload)?;
        let last = payload
            .last_mut()
            .ok_or_else(|| std::io::Error::other("test payload was empty"))?;
        *last ^= 1;
        envelope.payload = URL_SAFE_NO_PAD.encode(payload);
        assert!(matches!(
            verifier.verify_manifest(&serde_json::to_vec(&envelope)?),
            Err(UpdateError::SignatureRejected),
        ));
        Ok(())
    }

    #[test]
    fn verifies_artifact_size_and_hash() -> Result<(), Box<dyn Error>> {
        let expected = artifact(b"binary");
        verify_artifact(&b"binary"[..], &expected)?;
        assert!(matches!(
            verify_artifact(&b"tamper"[..], &expected),
            Err(UpdateError::ArtifactHashMismatch),
        ));
        assert!(matches!(
            verify_artifact(&b"too long"[..], &expected),
            Err(UpdateError::ArtifactSizeMismatch),
        ));
        Ok(())
    }

    #[test]
    fn reports_protocol_incompatibility_without_downloading() -> Result<(), Box<dyn Error>> {
        let (verifier, _envelope, mut manifest) = signed_manifest()?;
        manifest.minimum_protocol = 7;
        manifest.maximum_protocol = 8;
        assert_eq!(
            verifier.decide(&manifest, "1.1.0", 1, "test-target")?,
            UpdateDecision::ProtocolIncompatible {
                version: semver::Version::parse("1.2.0")?,
                minimum_protocol: 7,
                maximum_protocol: 8,
            },
        );
        Ok(())
    }
}
