//! Authenticated, origin-bound local state encrypted by a credential-store key.

use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
#[cfg(unix)]
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};

use chacha20poly1305::aead::{Aead, KeyInit, Payload};
use chacha20poly1305::{XChaCha20Poly1305, XNonce};
use fs2::FileExt;
use rand_core::{OsRng, RngCore};
use serde::Serialize;
use serde::de::DeserializeOwned;
use tempfile::NamedTempFile;
use thiserror::Error;
use zeroize::Zeroizing;

use crate::credential::SecretBytes;
use crate::origin::ApiOrigin;
use crate::profile::ProfilePaths;

const ENVELOPE_MAGIC: &[u8; 8] = b"SQUIDST\0";
const ENVELOPE_VERSION: u16 = 1;
const NONCE_BYTES: usize = 24;
const HEADER_BYTES: usize = ENVELOPE_MAGIC.len() + size_of::<u16>() + NONCE_BYTES;
const MAXIMUM_PLAINTEXT_BYTES: usize = 16 * 1024 * 1024;
const MAXIMUM_ENCRYPTED_BYTES: u64 = (MAXIMUM_PLAINTEXT_BYTES + HEADER_BYTES + 16) as u64;

/// Fixed state coordinates; arbitrary filenames are never accepted.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum StateKind {
    DraftCache,
    PendingOperations,
    Session,
}

impl StateKind {
    const ALL: [Self; 3] = [Self::DraftCache, Self::PendingOperations, Self::Session];

    const fn name(self) -> &'static str {
        match self {
            Self::DraftCache => "draft-cache",
            Self::PendingOperations => "pending-operations",
            Self::Session => "session",
        }
    }

    fn filename(self) -> String {
        format!("{}.state", self.name())
    }
}

/// Locked encrypted JSON state for one exact API origin.
#[derive(Clone, Debug)]
pub struct EncryptedStateStore {
    directory: PathBuf,
    associated_data_prefix: Vec<u8>,
}

impl EncryptedStateStore {
    /// Isolate state by the same normalized-origin hash used for credentials.
    #[must_use]
    pub fn new(paths: &ProfilePaths, origin: &ApiOrigin) -> Self {
        let mut associated_data_prefix = b"squid-cli-state-v1\0".to_vec();
        associated_data_prefix.extend_from_slice(origin.as_str().as_bytes());
        associated_data_prefix.push(0);
        Self {
            directory: paths
                .state_directory
                .join("encrypted")
                .join(origin.storage_key()),
            associated_data_prefix,
        }
    }

    /// Read and authenticate one JSON value, returning `None` only when no file exists.
    pub fn read<T: DeserializeOwned>(
        &self,
        kind: StateKind,
        key: &SecretBytes,
    ) -> Result<Option<T>, EncryptedStateError> {
        secure_directory(&self.directory)?;
        let lock = open_lock(&self.directory)?;
        FileExt::lock_shared(&lock).map_err(EncryptedStateError::Io)?;
        let result = self.read_unlocked(kind, key);
        FileExt::unlock(&lock).map_err(EncryptedStateError::Io)?;
        result
    }

    /// Serialize, encrypt, and atomically replace one JSON value.
    pub fn write<T: Serialize>(
        &self,
        kind: StateKind,
        key: &SecretBytes,
        value: &T,
    ) -> Result<(), EncryptedStateError> {
        secure_directory(&self.directory)?;
        let lock = open_lock(&self.directory)?;
        FileExt::lock_exclusive(&lock).map_err(EncryptedStateError::Io)?;
        let result = self.write_unlocked(kind, key, value);
        FileExt::unlock(&lock).map_err(EncryptedStateError::Io)?;
        result
    }

    /// Delete one encrypted state value without affecting credentials.
    pub fn delete(&self, kind: StateKind) -> Result<(), EncryptedStateError> {
        secure_directory(&self.directory)?;
        let lock = open_lock(&self.directory)?;
        FileExt::lock_exclusive(&lock).map_err(EncryptedStateError::Io)?;
        let result = remove_file_if_present(&self.path(kind), &self.directory);
        FileExt::unlock(&lock).map_err(EncryptedStateError::Io)?;
        result
    }

    /// Delete every known encrypted state value for this origin.
    pub fn purge(&self) -> Result<(), EncryptedStateError> {
        secure_directory(&self.directory)?;
        let lock = open_lock(&self.directory)?;
        FileExt::lock_exclusive(&lock).map_err(EncryptedStateError::Io)?;
        let result = (|| {
            for kind in StateKind::ALL {
                remove_file_if_present(&self.path(kind), &self.directory)?;
            }
            Ok(())
        })();
        FileExt::unlock(&lock).map_err(EncryptedStateError::Io)?;
        result
    }

    fn path(&self, kind: StateKind) -> PathBuf {
        self.directory.join(kind.filename())
    }

    fn associated_data(&self, kind: StateKind) -> Vec<u8> {
        let mut value = self.associated_data_prefix.clone();
        value.extend_from_slice(kind.name().as_bytes());
        value
    }

    fn read_unlocked<T: DeserializeOwned>(
        &self,
        kind: StateKind,
        key: &SecretBytes,
    ) -> Result<Option<T>, EncryptedStateError> {
        let path = self.path(kind);
        reject_symlink(&path)?;
        let file = match File::open(path) {
            Ok(file) => file,
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(EncryptedStateError::Io(error)),
        };
        let length = file.metadata().map_err(EncryptedStateError::Io)?.len();
        if length > MAXIMUM_ENCRYPTED_BYTES {
            return Err(EncryptedStateError::StateTooLarge);
        }
        let mut envelope = Vec::new();
        file.take(MAXIMUM_ENCRYPTED_BYTES + 1)
            .read_to_end(&mut envelope)
            .map_err(EncryptedStateError::Io)?;
        if envelope.len() < HEADER_BYTES + 16 || envelope.len() as u64 > MAXIMUM_ENCRYPTED_BYTES {
            return Err(EncryptedStateError::InvalidEnvelope);
        }
        if &envelope[..ENVELOPE_MAGIC.len()] != ENVELOPE_MAGIC {
            return Err(EncryptedStateError::InvalidEnvelope);
        }
        let version_start = ENVELOPE_MAGIC.len();
        let version = u16::from_be_bytes([envelope[version_start], envelope[version_start + 1]]);
        if version != ENVELOPE_VERSION {
            return Err(EncryptedStateError::UnsupportedEnvelope(version));
        }
        let nonce_start = ENVELOPE_MAGIC.len() + size_of::<u16>();
        let nonce_end = nonce_start + NONCE_BYTES;
        let cipher = cipher(key)?;
        let plaintext = cipher
            .decrypt(
                XNonce::from_slice(&envelope[nonce_start..nonce_end]),
                Payload {
                    msg: &envelope[nonce_end..],
                    aad: &self.associated_data(kind),
                },
            )
            .map_err(|_error| EncryptedStateError::AuthenticationFailed)?;
        let plaintext = Zeroizing::new(plaintext);
        if plaintext.len() > MAXIMUM_PLAINTEXT_BYTES {
            return Err(EncryptedStateError::StateTooLarge);
        }
        serde_json::from_slice(&plaintext)
            .map(Some)
            .map_err(EncryptedStateError::InvalidJson)
    }

    fn write_unlocked<T: Serialize>(
        &self,
        kind: StateKind,
        key: &SecretBytes,
        value: &T,
    ) -> Result<(), EncryptedStateError> {
        let plaintext =
            Zeroizing::new(serde_json::to_vec(value).map_err(EncryptedStateError::SerializeJson)?);
        if plaintext.len() > MAXIMUM_PLAINTEXT_BYTES {
            return Err(EncryptedStateError::StateTooLarge);
        }
        let mut nonce = Zeroizing::new([0_u8; NONCE_BYTES]);
        OsRng.fill_bytes(nonce.as_mut());
        let ciphertext = cipher(key)?
            .encrypt(
                XNonce::from_slice(nonce.as_ref()),
                Payload {
                    msg: &plaintext,
                    aad: &self.associated_data(kind),
                },
            )
            .map_err(|_error| EncryptedStateError::EncryptionFailed)?;
        let mut envelope = Vec::with_capacity(HEADER_BYTES + ciphertext.len());
        envelope.extend_from_slice(ENVELOPE_MAGIC);
        envelope.extend_from_slice(&ENVELOPE_VERSION.to_be_bytes());
        envelope.extend_from_slice(nonce.as_ref());
        envelope.extend_from_slice(&ciphertext);
        atomic_write(&self.path(kind), &envelope)
    }
}

fn cipher(key: &SecretBytes) -> Result<XChaCha20Poly1305, EncryptedStateError> {
    XChaCha20Poly1305::new_from_slice(key.expose())
        .map_err(|_error| EncryptedStateError::InvalidKey)
}

fn secure_directory(path: &Path) -> Result<(), EncryptedStateError> {
    fs::create_dir_all(path).map_err(EncryptedStateError::Io)?;
    let metadata = fs::symlink_metadata(path).map_err(EncryptedStateError::Io)?;
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        return Err(EncryptedStateError::SymlinkNotAllowed);
    }
    #[cfg(unix)]
    fs::set_permissions(path, fs::Permissions::from_mode(0o700))
        .map_err(EncryptedStateError::Io)?;
    Ok(())
}

fn open_lock(directory: &Path) -> Result<File, EncryptedStateError> {
    let path = directory.join("encrypted-state.lock");
    reject_symlink(&path)?;
    let mut options = OpenOptions::new();
    options.create(true).read(true).write(true);
    #[cfg(unix)]
    options.mode(0o600);
    options.open(path).map_err(EncryptedStateError::Io)
}

fn atomic_write(path: &Path, contents: &[u8]) -> Result<(), EncryptedStateError> {
    let parent = path.parent().ok_or(EncryptedStateError::InvalidStatePath)?;
    reject_symlink(path)?;
    let mut temporary = NamedTempFile::new_in(parent).map_err(EncryptedStateError::Io)?;
    #[cfg(unix)]
    temporary
        .as_file()
        .set_permissions(fs::Permissions::from_mode(0o600))
        .map_err(EncryptedStateError::Io)?;
    temporary
        .write_all(contents)
        .map_err(EncryptedStateError::Io)?;
    temporary
        .as_file()
        .sync_all()
        .map_err(EncryptedStateError::Io)?;
    reject_symlink(path)?;
    temporary
        .persist(path)
        .map_err(|error| EncryptedStateError::Io(error.error))?;
    sync_directory(parent)
}

fn remove_file_if_present(path: &Path, parent: &Path) -> Result<(), EncryptedStateError> {
    reject_symlink(path)?;
    match fs::remove_file(path) {
        Ok(()) => sync_directory(parent),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(EncryptedStateError::Io(error)),
    }
}

fn reject_symlink(path: &Path) -> Result<(), EncryptedStateError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_symlink() => {
            Err(EncryptedStateError::SymlinkNotAllowed)
        }
        Ok(_) => Ok(()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(EncryptedStateError::Io(error)),
    }
}

#[cfg(unix)]
fn sync_directory(path: &Path) -> Result<(), EncryptedStateError> {
    File::open(path)
        .and_then(|directory| directory.sync_all())
        .map_err(EncryptedStateError::Io)
}

#[cfg(not(unix))]
fn sync_directory(_path: &Path) -> Result<(), EncryptedStateError> {
    Ok(())
}

/// Encrypted-state storage, authentication, or schema failure.
#[derive(Debug, Error)]
pub enum EncryptedStateError {
    #[error("encrypted state key must be exactly 256 bits")]
    InvalidKey,
    #[error("encrypted state could not be encrypted")]
    EncryptionFailed,
    #[error("encrypted state authentication failed")]
    AuthenticationFailed,
    #[error("encrypted state envelope is malformed")]
    InvalidEnvelope,
    #[error("encrypted state envelope version {0} is unsupported")]
    UnsupportedEnvelope(u16),
    #[error("encrypted state exceeds the local size limit")]
    StateTooLarge,
    #[error("encrypted state contains invalid JSON: {0}")]
    InvalidJson(#[source] serde_json::Error),
    #[error("local state could not be serialized: {0}")]
    SerializeJson(#[source] serde_json::Error),
    #[error("encrypted state path is invalid")]
    InvalidStatePath,
    #[error("symbolic links are not allowed for encrypted state")]
    SymlinkNotAllowed,
    #[error("encrypted state could not be read or written: {0}")]
    Io(#[source] io::Error),
}

#[cfg(test)]
mod tests {
    use std::error::Error;
    use std::fs;
    use std::io;

    use serde::{Deserialize, Serialize};
    use tempfile::tempdir;

    use super::{EncryptedStateError, EncryptedStateStore, StateKind};
    use crate::credential::SecretBytes;
    use crate::origin::ApiOrigin;
    use crate::profile::ProfilePaths;

    #[derive(Debug, Deserialize, Eq, PartialEq, Serialize)]
    struct DraftCache {
        revision: u64,
        private_answer: String,
    }

    fn key(value: u8) -> SecretBytes {
        SecretBytes::new(vec![value; 32])
    }

    #[test]
    fn round_trips_authenticated_json() -> Result<(), Box<dyn Error>> {
        let directory = tempdir()?;
        let origin = ApiOrigin::parse("https://example.com")?;
        let store = EncryptedStateStore::new(&ProfilePaths::under(directory.path()), &origin);
        let expected = DraftCache {
            revision: 7,
            private_answer: String::from("hidden"),
        };
        store.write(StateKind::DraftCache, &key(3), &expected)?;
        let actual = store.read::<DraftCache>(StateKind::DraftCache, &key(3))?;
        assert_eq!(actual, Some(expected));
        Ok(())
    }

    #[test]
    fn rejects_wrong_key_and_wrong_origin() -> Result<(), Box<dyn Error>> {
        let directory = tempdir()?;
        let paths = ProfilePaths::under(directory.path());
        let first_origin = ApiOrigin::parse("https://example.com")?;
        let second_origin = ApiOrigin::parse("https://example.net")?;
        let first = EncryptedStateStore::new(&paths, &first_origin);
        first.write(StateKind::Session, &key(3), &"token")?;
        assert!(matches!(
            first.read::<String>(StateKind::Session, &key(4)),
            Err(EncryptedStateError::AuthenticationFailed),
        ));

        let second = EncryptedStateStore::new(&paths, &second_origin);
        let first_path = paths
            .state_directory
            .join("encrypted")
            .join(first_origin.storage_key())
            .join("session.state");
        let second_directory = paths
            .state_directory
            .join("encrypted")
            .join(second_origin.storage_key());
        fs::create_dir_all(&second_directory)?;
        fs::copy(first_path, second_directory.join("session.state"))?;
        assert!(matches!(
            second.read::<String>(StateKind::Session, &key(3)),
            Err(EncryptedStateError::AuthenticationFailed),
        ));
        Ok(())
    }

    #[test]
    fn detects_ciphertext_tampering() -> Result<(), Box<dyn Error>> {
        let directory = tempdir()?;
        let paths = ProfilePaths::under(directory.path());
        let origin = ApiOrigin::parse("https://example.com")?;
        let store = EncryptedStateStore::new(&paths, &origin);
        store.write(StateKind::PendingOperations, &key(8), &vec!["one"])?;
        let path = paths
            .state_directory
            .join("encrypted")
            .join(origin.storage_key())
            .join("pending-operations.state");
        let mut contents = fs::read(&path)?;
        let last = contents
            .last_mut()
            .ok_or_else(|| io::Error::other("encrypted test file was empty"))?;
        *last ^= 1;
        fs::write(path, contents)?;
        assert!(matches!(
            store.read::<Vec<String>>(StateKind::PendingOperations, &key(8)),
            Err(EncryptedStateError::AuthenticationFailed),
        ));
        Ok(())
    }

    #[cfg(unix)]
    #[test]
    fn encrypted_files_are_owner_only() -> Result<(), Box<dyn Error>> {
        use std::os::unix::fs::PermissionsExt;

        let directory = tempdir()?;
        let paths = ProfilePaths::under(directory.path());
        let origin = ApiOrigin::parse("https://example.com")?;
        let store = EncryptedStateStore::new(&paths, &origin);
        store.write(StateKind::Session, &key(9), &"token")?;
        let state_directory = paths
            .state_directory
            .join("encrypted")
            .join(origin.storage_key());
        assert_eq!(
            fs::metadata(&state_directory)?.permissions().mode() & 0o777,
            0o700
        );
        assert_eq!(
            fs::metadata(state_directory.join("session.state"))?
                .permissions()
                .mode()
                & 0o777,
            0o600,
        );
        Ok(())
    }
}
