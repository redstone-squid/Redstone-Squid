//! Origin-isolated device credentials with native storage and explicit file fallback.

use std::fmt;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
#[cfg(unix)]
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};

use base64::Engine;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use ed25519_dalek::{Signature, Signer, SigningKey};
use fs2::FileExt;
use rand_core::{OsRng, RngCore};
use serde::{Deserialize, Serialize};
use tempfile::NamedTempFile;
use thiserror::Error;
use zeroize::Zeroizing;

use crate::origin::ApiOrigin;
use crate::profile::ProfilePaths;

const CREDENTIAL_MARKER_SCHEMA_VERSION: u32 = 1;
const MAXIMUM_SECRET_BYTES: u64 = 64 * 1024;
const SYSTEM_CREDENTIAL_SERVICE: &str = "org.redstonesquid.squid";

/// Fixed secret coordinates; arbitrary filenames and keyring names are never accepted.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CredentialKind {
    DeviceSigningKey,
    DraftCacheKey,
}

impl CredentialKind {
    const ALL: [Self; 2] = [Self::DeviceSigningKey, Self::DraftCacheKey];

    const fn name(self) -> &'static str {
        match self {
            Self::DeviceSigningKey => "device-signing-key",
            Self::DraftCacheKey => "draft-cache-key",
        }
    }

    fn filename(self) -> String {
        format!("{}.secret", self.name())
    }
}

/// Secret bytes that zero memory on drop and never reveal their value through `Debug`.
pub struct SecretBytes(Zeroizing<Vec<u8>>);

impl SecretBytes {
    /// Take ownership of secret material.
    #[must_use]
    pub fn new(value: Vec<u8>) -> Self {
        Self(Zeroizing::new(value))
    }

    /// Borrow the secret only for a narrowly scoped cryptographic or storage operation.
    #[must_use]
    pub fn expose(&self) -> &[u8] {
        &self.0
    }
}

impl fmt::Debug for SecretBytes {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("SecretBytes([REDACTED])")
    }
}

/// Backend that currently owns every credential for one exact API origin.
#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CredentialBackend {
    System,
    OwnerFile,
}

/// Minimal secret-store port used by the fallback router and deterministic tests.
pub trait CredentialStore {
    fn get(&self, kind: CredentialKind) -> Result<Option<SecretBytes>, CredentialError>;
    fn set(&self, kind: CredentialKind, secret: &SecretBytes) -> Result<(), CredentialError>;
    fn delete(&self, kind: CredentialKind) -> Result<(), CredentialError>;
}

/// Windows Credential Manager, macOS Keychain, or Linux Secret Service storage.
#[derive(Clone, Debug)]
pub struct SystemCredentialStore {
    account_prefix: String,
}

impl SystemCredentialStore {
    /// Namespace entries by an exact normalized API origin hash.
    #[must_use]
    pub fn new(origin: &ApiOrigin) -> Self {
        Self {
            account_prefix: String::from(origin.storage_key()),
        }
    }

    fn entry(&self, kind: CredentialKind) -> Result<keyring::Entry, CredentialError> {
        let account = format!("{}:{}", self.account_prefix, kind.name());
        keyring::Entry::new(SYSTEM_CREDENTIAL_SERVICE, &account).map_err(CredentialError::System)
    }
}

impl CredentialStore for SystemCredentialStore {
    fn get(&self, kind: CredentialKind) -> Result<Option<SecretBytes>, CredentialError> {
        match self.entry(kind)?.get_secret() {
            Ok(secret) => Ok(Some(SecretBytes::new(secret))),
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(error) => Err(CredentialError::System(error)),
        }
    }

    fn set(&self, kind: CredentialKind, secret: &SecretBytes) -> Result<(), CredentialError> {
        self.entry(kind)?
            .set_secret(secret.expose())
            .map_err(CredentialError::System)
    }

    fn delete(&self, kind: CredentialKind) -> Result<(), CredentialError> {
        match self.entry(kind)?.delete_credential() {
            Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
            Err(error) => Err(CredentialError::System(error)),
        }
    }
}

/// Owner-readable raw files, selected only after an explicit fallback decision.
#[derive(Clone, Debug)]
pub struct OwnerFileCredentialStore {
    directory: PathBuf,
}

impl OwnerFileCredentialStore {
    /// Isolate files under the exact origin hash.
    #[must_use]
    pub fn new(paths: &ProfilePaths, origin: &ApiOrigin) -> Self {
        Self {
            directory: paths
                .state_directory
                .join("credentials")
                .join(origin.storage_key()),
        }
    }

    fn path(&self, kind: CredentialKind) -> PathBuf {
        self.directory.join(kind.filename())
    }

    fn get_unlocked(&self, kind: CredentialKind) -> Result<Option<SecretBytes>, CredentialError> {
        let path = self.path(kind);
        reject_symlink(&path)?;
        let file = match File::open(path) {
            Ok(file) => file,
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(CredentialError::Io(error)),
        };
        if file.metadata().map_err(CredentialError::Io)?.len() > MAXIMUM_SECRET_BYTES {
            return Err(CredentialError::SecretTooLarge);
        }
        let mut secret = Vec::new();
        file.take(MAXIMUM_SECRET_BYTES + 1)
            .read_to_end(&mut secret)
            .map_err(CredentialError::Io)?;
        if secret.is_empty() || secret.len() as u64 > MAXIMUM_SECRET_BYTES {
            return Err(CredentialError::InvalidSecretFile);
        }
        Ok(Some(SecretBytes::new(secret)))
    }

    fn set_unlocked(
        &self,
        kind: CredentialKind,
        secret: &SecretBytes,
    ) -> Result<(), CredentialError> {
        if secret.expose().is_empty() || secret.expose().len() as u64 > MAXIMUM_SECRET_BYTES {
            return Err(CredentialError::InvalidSecretLength);
        }
        secure_directory(&self.directory)?;
        let path = self.path(kind);
        reject_symlink(&path)?;
        atomic_write(&path, secret.expose())
    }

    fn delete_unlocked(&self, kind: CredentialKind) -> Result<(), CredentialError> {
        let path = self.path(kind);
        reject_symlink(&path)?;
        match fs::remove_file(path) {
            Ok(()) => sync_directory(&self.directory),
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
            Err(error) => Err(CredentialError::Io(error)),
        }
    }
}

impl CredentialStore for OwnerFileCredentialStore {
    fn get(&self, kind: CredentialKind) -> Result<Option<SecretBytes>, CredentialError> {
        secure_directory(&self.directory)?;
        let lock = open_lock(&self.directory)?;
        FileExt::lock_shared(&lock).map_err(CredentialError::Io)?;
        let result = self.get_unlocked(kind);
        FileExt::unlock(&lock).map_err(CredentialError::Io)?;
        result
    }

    fn set(&self, kind: CredentialKind, secret: &SecretBytes) -> Result<(), CredentialError> {
        secure_directory(&self.directory)?;
        let lock = open_lock(&self.directory)?;
        FileExt::lock_exclusive(&lock).map_err(CredentialError::Io)?;
        let result = self.set_unlocked(kind, secret);
        FileExt::unlock(&lock).map_err(CredentialError::Io)?;
        result
    }

    fn delete(&self, kind: CredentialKind) -> Result<(), CredentialError> {
        secure_directory(&self.directory)?;
        let lock = open_lock(&self.directory)?;
        FileExt::lock_exclusive(&lock).map_err(CredentialError::Io)?;
        let result = self.delete_unlocked(kind);
        FileExt::unlock(&lock).map_err(CredentialError::Io)?;
        result
    }
}

/// Routes one origin to native storage, or to a persistently marked explicit fallback.
#[derive(Clone, Debug)]
pub struct CredentialVault<N: CredentialStore = SystemCredentialStore> {
    native: N,
    fallback: OwnerFileCredentialStore,
    marker_path: PathBuf,
}

impl CredentialVault<SystemCredentialStore> {
    /// Build the production credential router for one profile and origin.
    #[must_use]
    pub fn system(paths: &ProfilePaths, origin: &ApiOrigin) -> Self {
        Self::new(SystemCredentialStore::new(origin), paths, origin)
    }
}

impl<N: CredentialStore> CredentialVault<N> {
    /// Build a router with an injectable native backend.
    #[must_use]
    pub fn new(native: N, paths: &ProfilePaths, origin: &ApiOrigin) -> Self {
        let fallback = OwnerFileCredentialStore::new(paths, origin);
        let marker_path = fallback.directory.join("backend.toml");
        Self {
            native,
            fallback,
            marker_path,
        }
    }

    /// Report the persistent backend selection without probing or exposing a secret.
    pub fn backend(&self) -> Result<CredentialBackend, CredentialError> {
        Ok(read_backend_marker(&self.marker_path)?.unwrap_or(CredentialBackend::System))
    }

    /// Retrieve a credential from the persistently selected backend.
    pub fn get(&self, kind: CredentialKind) -> Result<Option<SecretBytes>, CredentialError> {
        match self.backend()? {
            CredentialBackend::System => self.native.get(kind),
            CredentialBackend::OwnerFile => self.fallback.get(kind),
        }
    }

    /// Store a credential, falling back only when the caller explicitly permits it.
    pub fn set(
        &self,
        kind: CredentialKind,
        secret: &SecretBytes,
        allow_file_fallback: bool,
    ) -> Result<CredentialBackend, CredentialError> {
        match read_backend_marker(&self.marker_path)? {
            Some(CredentialBackend::OwnerFile) => {
                self.fallback.set(kind, secret)?;
                Ok(CredentialBackend::OwnerFile)
            }
            Some(CredentialBackend::System) => {
                self.native.set(kind, secret)?;
                Ok(CredentialBackend::System)
            }
            None => match self.native.set(kind, secret) {
                Ok(()) => {
                    write_backend_marker(&self.marker_path, CredentialBackend::System)?;
                    Ok(CredentialBackend::System)
                }
                Err(_error) if allow_file_fallback => {
                    write_backend_marker(&self.marker_path, CredentialBackend::OwnerFile)?;
                    self.fallback.set(kind, secret)?;
                    Ok(CredentialBackend::OwnerFile)
                }
                Err(error) => Err(CredentialError::FallbackNotAllowed(Box::new(error))),
            },
        }
    }

    /// Delete all known local credentials from the selected backend.
    pub fn purge(&self) -> Result<CredentialBackend, CredentialError> {
        let backend = self.backend()?;
        match backend {
            CredentialBackend::System => {
                for kind in CredentialKind::ALL {
                    self.native.delete(kind)?;
                }
                remove_marker(&self.marker_path)?;
            }
            CredentialBackend::OwnerFile => {
                for kind in CredentialKind::ALL {
                    self.fallback.delete(kind)?;
                }
                remove_marker(&self.marker_path)?;
            }
        }
        Ok(backend)
    }
}

/// Ed25519 device identity whose private seed is suitable for a `CredentialStore`.
pub struct DeviceIdentity {
    signing_key: SigningKey,
}

impl DeviceIdentity {
    /// Generate a new device signing identity using the operating system RNG.
    #[must_use]
    pub fn generate() -> Self {
        Self {
            signing_key: SigningKey::generate(&mut OsRng),
        }
    }

    /// Restore a device identity from exactly one Ed25519 private seed.
    pub fn from_secret(secret: &SecretBytes) -> Result<Self, CredentialError> {
        let seed = Zeroizing::new(
            secret
                .expose()
                .try_into()
                .map_err(|_error| CredentialError::InvalidDeviceKey)?,
        );
        Ok(Self {
            signing_key: SigningKey::from_bytes(&seed),
        })
    }

    /// Copy the private seed into a zeroizing value for immediate storage.
    #[must_use]
    pub fn to_secret(&self) -> SecretBytes {
        let seed = Zeroizing::new(self.signing_key.to_bytes());
        SecretBytes::new(seed.to_vec())
    }

    /// URL-safe, unpadded public key sent during device enrollment.
    #[must_use]
    pub fn public_key(&self) -> String {
        URL_SAFE_NO_PAD.encode(self.signing_key.verifying_key().as_bytes())
    }

    /// Sign an enrollment or request challenge and encode the detached signature.
    #[must_use]
    pub fn sign(&self, message: &[u8]) -> String {
        let signature: Signature = self.signing_key.sign(message);
        URL_SAFE_NO_PAD.encode(signature.to_bytes())
    }
}

impl fmt::Debug for DeviceIdentity {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("DeviceIdentity")
            .field("public_key", &self.public_key())
            .field("signing_key", &"[REDACTED]")
            .finish()
    }
}

/// Load an existing device identity or generate and persist a new one.
pub fn load_or_create_device_identity<N: CredentialStore>(
    vault: &CredentialVault<N>,
    allow_file_fallback: bool,
) -> Result<(DeviceIdentity, bool, CredentialBackend), CredentialError> {
    if let Some(secret) = vault.get(CredentialKind::DeviceSigningKey)? {
        let backend = vault.backend()?;
        return Ok((DeviceIdentity::from_secret(&secret)?, false, backend));
    }
    let identity = DeviceIdentity::generate();
    let backend = vault.set(
        CredentialKind::DeviceSigningKey,
        &identity.to_secret(),
        allow_file_fallback,
    )?;
    Ok((identity, true, backend))
}

/// Load or create the 256-bit key used to encrypt synchronized draft cache state.
pub fn load_or_create_draft_cache_key<N: CredentialStore>(
    vault: &CredentialVault<N>,
    allow_file_fallback: bool,
) -> Result<(SecretBytes, bool, CredentialBackend), CredentialError> {
    if let Some(secret) = vault.get(CredentialKind::DraftCacheKey)? {
        if secret.expose().len() != 32 {
            return Err(CredentialError::InvalidDraftCacheKey);
        }
        let backend = vault.backend()?;
        return Ok((secret, false, backend));
    }
    let mut key = Zeroizing::new(vec![0_u8; 32]);
    OsRng.fill_bytes(&mut key);
    let secret = SecretBytes::new(key.to_vec());
    let backend = vault.set(CredentialKind::DraftCacheKey, &secret, allow_file_fallback)?;
    Ok((secret, true, backend))
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct BackendMarker {
    schema_version: u32,
    backend: CredentialBackend,
}

fn read_backend_marker(path: &Path) -> Result<Option<CredentialBackend>, CredentialError> {
    reject_symlink(path)?;
    let contents = match fs::read_to_string(path) {
        Ok(contents) => contents,
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            return Ok(None);
        }
        Err(error) => return Err(CredentialError::Io(error)),
    };
    let marker = toml::from_str::<BackendMarker>(&contents)
        .map_err(|_error| CredentialError::InvalidBackendMarker)?;
    if marker.schema_version != CREDENTIAL_MARKER_SCHEMA_VERSION {
        return Err(CredentialError::InvalidBackendMarker);
    }
    Ok(Some(marker.backend))
}

fn write_backend_marker(path: &Path, backend: CredentialBackend) -> Result<(), CredentialError> {
    let parent = path.parent().ok_or(CredentialError::InvalidStatePath)?;
    secure_directory(parent)?;
    reject_symlink(path)?;
    let marker = BackendMarker {
        schema_version: CREDENTIAL_MARKER_SCHEMA_VERSION,
        backend,
    };
    let serialized =
        toml::to_string(&marker).map_err(|_error| CredentialError::InvalidBackendMarker)?;
    atomic_write(path, serialized.as_bytes())
}

fn remove_marker(path: &Path) -> Result<(), CredentialError> {
    reject_symlink(path)?;
    match fs::remove_file(path) {
        Ok(()) => {
            let parent = path.parent().ok_or(CredentialError::InvalidStatePath)?;
            sync_directory(parent)
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(CredentialError::Io(error)),
    }
}

fn secure_directory(path: &Path) -> Result<(), CredentialError> {
    fs::create_dir_all(path).map_err(CredentialError::Io)?;
    let metadata = fs::symlink_metadata(path).map_err(CredentialError::Io)?;
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        return Err(CredentialError::SymlinkNotAllowed);
    }
    #[cfg(unix)]
    fs::set_permissions(path, fs::Permissions::from_mode(0o700)).map_err(CredentialError::Io)?;
    Ok(())
}

fn open_lock(directory: &Path) -> Result<File, CredentialError> {
    let path = directory.join("credentials.lock");
    reject_symlink(&path)?;
    let mut options = OpenOptions::new();
    options.create(true).read(true).write(true);
    #[cfg(unix)]
    options.mode(0o600);
    options.open(path).map_err(CredentialError::Io)
}

fn atomic_write(path: &Path, contents: &[u8]) -> Result<(), CredentialError> {
    let parent = path.parent().ok_or(CredentialError::InvalidStatePath)?;
    let mut temporary = NamedTempFile::new_in(parent).map_err(CredentialError::Io)?;
    #[cfg(unix)]
    temporary
        .as_file()
        .set_permissions(fs::Permissions::from_mode(0o600))
        .map_err(CredentialError::Io)?;
    temporary.write_all(contents).map_err(CredentialError::Io)?;
    temporary
        .as_file()
        .sync_all()
        .map_err(CredentialError::Io)?;
    reject_symlink(path)?;
    temporary
        .persist(path)
        .map_err(|error| CredentialError::Io(error.error))?;
    sync_directory(parent)
}

fn reject_symlink(path: &Path) -> Result<(), CredentialError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_symlink() => {
            Err(CredentialError::SymlinkNotAllowed)
        }
        Ok(_) => Ok(()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(CredentialError::Io(error)),
    }
}

#[cfg(unix)]
fn sync_directory(path: &Path) -> Result<(), CredentialError> {
    File::open(path)
        .and_then(|directory| directory.sync_all())
        .map_err(CredentialError::Io)
}

#[cfg(not(unix))]
fn sync_directory(_path: &Path) -> Result<(), CredentialError> {
    Ok(())
}

/// Credential-store, integrity, or explicit-fallback failure.
#[derive(Debug, Error)]
pub enum CredentialError {
    #[error("system credential store failed: {0}")]
    System(#[source] keyring::Error),
    #[error("system credential store failed and owner-file fallback was not explicitly allowed")]
    FallbackNotAllowed(#[source] Box<CredentialError>),
    #[error("credential state could not be read or written: {0}")]
    Io(#[source] io::Error),
    #[error("symbolic links are not allowed for credential state")]
    SymlinkNotAllowed,
    #[error("credential state path is invalid")]
    InvalidStatePath,
    #[error("credential backend marker is invalid or unsupported")]
    InvalidBackendMarker,
    #[error("credential secret is empty or exceeds the local size limit")]
    InvalidSecretLength,
    #[error("credential file is empty or malformed")]
    InvalidSecretFile,
    #[error("credential file exceeds the local size limit")]
    SecretTooLarge,
    #[error("stored Ed25519 device key is invalid")]
    InvalidDeviceKey,
    #[error("stored draft-cache key is invalid")]
    InvalidDraftCacheKey,
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;
    use std::error::Error;
    use std::fs;
    use std::io;
    use std::path::Path;
    use std::sync::Mutex;

    use tempfile::tempdir;

    use super::{
        CredentialBackend, CredentialError, CredentialKind, CredentialStore, CredentialVault,
        SecretBytes, load_or_create_device_identity, load_or_create_draft_cache_key,
    };
    use crate::origin::ApiOrigin;
    use crate::profile::ProfilePaths;

    #[derive(Debug, Default)]
    struct MemoryStore {
        values: Mutex<BTreeMap<&'static str, Vec<u8>>>,
        fail_writes: bool,
    }

    impl MemoryStore {
        fn failing() -> Self {
            Self {
                values: Mutex::default(),
                fail_writes: true,
            }
        }
    }

    impl CredentialStore for MemoryStore {
        fn get(&self, kind: CredentialKind) -> Result<Option<SecretBytes>, CredentialError> {
            let values = self.values.lock().map_err(|_error| {
                CredentialError::Io(io::Error::other("memory store lock poisoned"))
            })?;
            Ok(values
                .get(kind.name())
                .map(|value| SecretBytes::new(value.clone())))
        }

        fn set(&self, kind: CredentialKind, secret: &SecretBytes) -> Result<(), CredentialError> {
            if self.fail_writes {
                return Err(CredentialError::Io(io::Error::other(
                    "injected native failure",
                )));
            }
            self.values
                .lock()
                .map_err(|_error| {
                    CredentialError::Io(io::Error::other("memory store lock poisoned"))
                })?
                .insert(kind.name(), secret.expose().to_vec());
            Ok(())
        }

        fn delete(&self, kind: CredentialKind) -> Result<(), CredentialError> {
            self.values
                .lock()
                .map_err(|_error| {
                    CredentialError::Io(io::Error::other("memory store lock poisoned"))
                })?
                .remove(kind.name());
            Ok(())
        }
    }

    fn vault(
        store: MemoryStore,
        directory: &Path,
    ) -> Result<CredentialVault<MemoryStore>, Box<dyn Error>> {
        let origin = ApiOrigin::parse("https://example.com")?;
        Ok(CredentialVault::new(
            store,
            &ProfilePaths::under(directory),
            &origin,
        ))
    }

    #[test]
    fn native_failure_needs_explicit_fallback() -> Result<(), Box<dyn Error>> {
        let directory = tempdir()?;
        let vault = vault(MemoryStore::failing(), directory.path())?;
        let secret = SecretBytes::new(vec![7; 32]);
        let denied = vault.set(CredentialKind::DraftCacheKey, &secret, false);
        assert!(matches!(
            denied,
            Err(CredentialError::FallbackNotAllowed(_))
        ));
        assert_eq!(vault.backend()?, CredentialBackend::System);
        Ok(())
    }

    #[test]
    fn fallback_choice_persists_and_purges() -> Result<(), Box<dyn Error>> {
        let directory = tempdir()?;
        let vault = vault(MemoryStore::failing(), directory.path())?;
        let secret = SecretBytes::new(vec![7; 32]);
        assert_eq!(
            vault.set(CredentialKind::DraftCacheKey, &secret, true)?,
            CredentialBackend::OwnerFile,
        );
        assert_eq!(vault.backend()?, CredentialBackend::OwnerFile);
        assert_eq!(
            vault
                .get(CredentialKind::DraftCacheKey)?
                .map(|value| value.expose().to_vec()),
            Some(vec![7; 32]),
        );
        assert_eq!(vault.purge()?, CredentialBackend::OwnerFile);
        assert_eq!(vault.backend()?, CredentialBackend::System);
        Ok(())
    }

    #[cfg(unix)]
    #[test]
    fn fallback_files_are_owner_only() -> Result<(), Box<dyn Error>> {
        use std::os::unix::fs::PermissionsExt;

        let directory = tempdir()?;
        let origin = ApiOrigin::parse("https://example.com")?;
        let paths = ProfilePaths::under(directory.path());
        let vault = CredentialVault::new(MemoryStore::failing(), &paths, &origin);
        vault.set(
            CredentialKind::DraftCacheKey,
            &SecretBytes::new(vec![7; 32]),
            true,
        )?;
        let credential_directory = paths
            .state_directory
            .join("credentials")
            .join(origin.storage_key());
        let secret_path = credential_directory.join("draft-cache-key.secret");
        assert_eq!(
            fs::metadata(credential_directory)?.permissions().mode() & 0o777,
            0o700
        );
        assert_eq!(
            fs::metadata(secret_path)?.permissions().mode() & 0o777,
            0o600
        );
        Ok(())
    }

    #[test]
    fn device_identity_round_trips_without_exposing_private_key() -> Result<(), Box<dyn Error>> {
        let directory = tempdir()?;
        let vault = vault(MemoryStore::default(), directory.path())?;
        let (first, created, backend) = load_or_create_device_identity(&vault, false)?;
        assert!(created);
        assert_eq!(backend, CredentialBackend::System);
        let (second, created, backend) = load_or_create_device_identity(&vault, false)?;
        assert!(!created);
        assert_eq!(backend, CredentialBackend::System);
        assert_eq!(first.public_key(), second.public_key());
        assert_eq!(first.sign(b"challenge"), second.sign(b"challenge"));
        assert!(format!("{first:?}").contains("[REDACTED]"));
        Ok(())
    }

    #[test]
    fn draft_cache_key_is_exactly_256_bits() -> Result<(), Box<dyn Error>> {
        let directory = tempdir()?;
        let vault = vault(MemoryStore::default(), directory.path())?;
        let (key, created, backend) = load_or_create_draft_cache_key(&vault, false)?;
        assert!(created);
        assert_eq!(backend, CredentialBackend::System);
        assert_eq!(key.expose().len(), 32);
        Ok(())
    }
}
