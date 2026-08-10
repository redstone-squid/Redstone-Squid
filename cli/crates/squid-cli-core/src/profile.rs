//! Schema-versioned named profiles with locked atomic persistence.

use std::collections::BTreeMap;
use std::env;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
#[cfg(unix)]
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};

use directories::ProjectDirs;
use fs2::FileExt;
use serde::{Deserialize, Serialize};
use tempfile::NamedTempFile;
use thiserror::Error;

use crate::locale::Locale;
use crate::origin::{ApiOrigin, OriginError};

const PROFILE_SCHEMA_VERSION: u32 = 1;
const MAXIMUM_CONFIG_BYTES: u64 = 1024 * 1024;

/// Preferred interactive form renderer for one profile.
#[derive(Clone, Copy, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum EditorPreference {
    #[default]
    Tui,
    Prompt,
}

/// One trusted API origin and its non-secret user preferences.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Profile {
    pub origin: ApiOrigin,
    pub trusted: bool,
    pub ca_certificate: Option<PathBuf>,
    pub locale: Option<String>,
    pub editor: EditorPreference,
    pub update_checks: bool,
}

impl Profile {
    /// Construct a trusted profile after the caller has performed the explicit trust action.
    #[must_use]
    pub fn new(origin: ApiOrigin) -> Self {
        Self {
            origin,
            trusted: true,
            ca_certificate: None,
            locale: None,
            editor: EditorPreference::Tui,
            update_checks: true,
        }
    }

    /// Validate fields whose invariants involve the local filesystem or supported catalogs.
    pub fn validate(&self) -> Result<(), ProfileError> {
        if !self.trusted {
            return Err(ProfileError::TrustRequired);
        }
        if let Some(locale) = &self.locale {
            locale
                .parse::<Locale>()
                .map_err(|_error| ProfileError::UnsupportedLocale)?;
        }
        if let Some(path) = &self.ca_certificate {
            let metadata = fs::symlink_metadata(path).map_err(ProfileError::Io)?;
            if metadata.file_type().is_symlink() || !metadata.is_file() {
                return Err(ProfileError::InvalidCaCertificate);
            }
        }
        Ok(())
    }
}

/// Validated profile identifier used in configuration and command arguments.
#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct ProfileName(String);

impl ProfileName {
    /// Accept lowercase ASCII names safe for logs and keyring coordinates.
    pub fn parse(value: &str) -> Result<Self, ProfileError> {
        let valid = (1..=32).contains(&value.len())
            && value.bytes().enumerate().all(|(index, byte)| {
                byte.is_ascii_lowercase()
                    || (index > 0 && (byte.is_ascii_digit() || byte == b'_' || byte == b'-'))
            });
        if !valid {
            return Err(ProfileError::InvalidName);
        }
        Ok(Self(String::from(value)))
    }

    /// Validated name value.
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// Complete non-secret CLI configuration document.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ProfileConfig {
    pub active_profile: Option<String>,
    pub profiles: BTreeMap<String, Profile>,
}

impl ProfileConfig {
    /// Return the selected profile or an explicit name.
    pub fn resolve(&self, name: Option<&ProfileName>) -> Result<(&str, &Profile), ProfileError> {
        let resolved_name = name.map_or_else(
            || {
                self.active_profile
                    .as_deref()
                    .ok_or(ProfileError::NoActiveProfile)
            },
            |name| Ok(name.as_str()),
        )?;
        self.profiles
            .get_key_value(resolved_name)
            .map(|(stored_name, profile)| (stored_name.as_str(), profile))
            .ok_or(ProfileError::NotFound)
    }
}

/// Filesystem locations used by one CLI installation.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ProfilePaths {
    pub config_file: PathBuf,
    pub state_directory: PathBuf,
    pub cache_directory: PathBuf,
}

impl ProfilePaths {
    /// Resolve standard platform paths, permitting a test/operator-owned config override.
    pub fn discover() -> Result<Self, ProfileError> {
        if let Some(root) = env::var_os("SQUID_CONFIG_HOME") {
            let root = PathBuf::from(root);
            return Ok(Self {
                config_file: root.join("config.toml"),
                state_directory: root.join("state"),
                cache_directory: root.join("cache"),
            });
        }
        let project = ProjectDirs::from("org", "Redstone Squid", "squid")
            .ok_or(ProfileError::NoProjectDirectory)?;
        Ok(Self {
            config_file: project.config_dir().join("config.toml"),
            state_directory: project
                .state_dir()
                .unwrap_or_else(|| project.data_local_dir())
                .to_path_buf(),
            cache_directory: project.cache_dir().to_path_buf(),
        })
    }

    /// Construct isolated paths rooted under one directory.
    #[must_use]
    pub fn under(root: impl AsRef<Path>) -> Self {
        let root = root.as_ref();
        Self {
            config_file: root.join("config.toml"),
            state_directory: root.join("state"),
            cache_directory: root.join("cache"),
        }
    }
}

/// Locked reader and mutator for the named-profile document.
#[derive(Clone, Debug)]
pub struct ProfileStore {
    paths: ProfilePaths,
}

impl ProfileStore {
    /// Open the standard per-user profile store.
    pub fn discover() -> Result<Self, ProfileError> {
        Ok(Self {
            paths: ProfilePaths::discover()?,
        })
    }

    /// Open an explicitly located profile store.
    #[must_use]
    pub const fn new(paths: ProfilePaths) -> Self {
        Self { paths }
    }

    /// Non-secret paths owned by this store.
    #[must_use]
    pub const fn paths(&self) -> &ProfilePaths {
        &self.paths
    }

    /// Load one consistent snapshot while holding a shared process lock.
    pub fn load(&self) -> Result<ProfileConfig, ProfileError> {
        self.prepare_parent()?;
        let lock = self.open_lock()?;
        FileExt::lock_shared(&lock).map_err(ProfileError::Io)?;
        let result = self.load_unlocked();
        FileExt::unlock(&lock).map_err(ProfileError::Io)?;
        result
    }

    /// Add a trusted profile and select it when it is the first entry.
    pub fn add(&self, name: &ProfileName, profile: Profile) -> Result<ProfileConfig, ProfileError> {
        profile.validate()?;
        self.update(|config| {
            if config.profiles.contains_key(name.as_str()) {
                return Err(ProfileError::AlreadyExists);
            }
            config.profiles.insert(String::from(name.as_str()), profile);
            if config.active_profile.is_none() {
                config.active_profile = Some(String::from(name.as_str()));
            }
            Ok(())
        })
    }

    /// Select an existing profile.
    pub fn select(&self, name: &ProfileName) -> Result<ProfileConfig, ProfileError> {
        self.update(|config| {
            if !config.profiles.contains_key(name.as_str()) {
                return Err(ProfileError::NotFound);
            }
            config.active_profile = Some(String::from(name.as_str()));
            Ok(())
        })
    }

    /// Remove one local profile and deterministically select another entry.
    pub fn remove(&self, name: &ProfileName) -> Result<ProfileConfig, ProfileError> {
        self.update(|config| {
            if config.profiles.remove(name.as_str()).is_none() {
                return Err(ProfileError::NotFound);
            }
            if config.active_profile.as_deref() == Some(name.as_str()) {
                config.active_profile = config.profiles.keys().next().cloned();
            }
            Ok(())
        })
    }

    fn update(
        &self,
        mutation: impl FnOnce(&mut ProfileConfig) -> Result<(), ProfileError>,
    ) -> Result<ProfileConfig, ProfileError> {
        self.prepare_parent()?;
        let lock = self.open_lock()?;
        FileExt::lock_exclusive(&lock).map_err(ProfileError::Io)?;
        let result = (|| {
            let mut config = self.load_unlocked()?;
            mutation(&mut config)?;
            self.write_unlocked(&config)?;
            Ok(config)
        })();
        FileExt::unlock(&lock).map_err(ProfileError::Io)?;
        result
    }

    fn prepare_parent(&self) -> Result<(), ProfileError> {
        let parent = self
            .paths
            .config_file
            .parent()
            .ok_or(ProfileError::InvalidConfigPath)?;
        fs::create_dir_all(parent).map_err(ProfileError::Io)?;
        #[cfg(unix)]
        fs::set_permissions(parent, fs::Permissions::from_mode(0o700)).map_err(ProfileError::Io)?;
        Ok(())
    }

    fn open_lock(&self) -> Result<File, ProfileError> {
        let path = self.paths.config_file.with_extension("lock");
        reject_symlink(&path)?;
        let mut options = OpenOptions::new();
        options.create(true).read(true).write(true);
        #[cfg(unix)]
        options.mode(0o600);
        options.open(path).map_err(ProfileError::Io)
    }

    fn load_unlocked(&self) -> Result<ProfileConfig, ProfileError> {
        reject_symlink(&self.paths.config_file)?;
        let file = match File::open(&self.paths.config_file) {
            Ok(file) => file,
            Err(error) if error.kind() == io::ErrorKind::NotFound => {
                return Ok(ProfileConfig::default());
            }
            Err(error) => return Err(ProfileError::Io(error)),
        };
        if file.metadata().map_err(ProfileError::Io)?.len() > MAXIMUM_CONFIG_BYTES {
            return Err(ProfileError::ConfigTooLarge);
        }
        let mut contents = String::new();
        file.take(MAXIMUM_CONFIG_BYTES + 1)
            .read_to_string(&mut contents)
            .map_err(ProfileError::Io)?;
        let document =
            toml::from_str::<ProfileDocument>(&contents).map_err(ProfileError::InvalidDocument)?;
        if document.schema_version != PROFILE_SCHEMA_VERSION {
            return Err(ProfileError::UnsupportedSchema(document.schema_version));
        }
        for profile in document.profiles.values() {
            profile.validate()?;
        }
        if let Some(active) = &document.active_profile {
            ProfileName::parse(active)?;
            if !document.profiles.contains_key(active) {
                return Err(ProfileError::InvalidActiveProfile);
            }
        }
        for name in document.profiles.keys() {
            ProfileName::parse(name)?;
        }
        Ok(ProfileConfig {
            active_profile: document.active_profile,
            profiles: document.profiles,
        })
    }

    fn write_unlocked(&self, config: &ProfileConfig) -> Result<(), ProfileError> {
        let parent = self
            .paths
            .config_file
            .parent()
            .ok_or(ProfileError::InvalidConfigPath)?;
        let document = ProfileDocument {
            schema_version: PROFILE_SCHEMA_VERSION,
            active_profile: config.active_profile.clone(),
            profiles: config.profiles.clone(),
        };
        let serialized = toml::to_string_pretty(&document).map_err(ProfileError::Serialize)?;
        let mut temporary = NamedTempFile::new_in(parent).map_err(ProfileError::Io)?;
        #[cfg(unix)]
        temporary
            .as_file()
            .set_permissions(fs::Permissions::from_mode(0o600))
            .map_err(ProfileError::Io)?;
        temporary
            .write_all(serialized.as_bytes())
            .map_err(ProfileError::Io)?;
        temporary.as_file().sync_all().map_err(ProfileError::Io)?;
        reject_symlink(&self.paths.config_file)?;
        temporary
            .persist(&self.paths.config_file)
            .map_err(|error| ProfileError::Io(error.error))?;
        sync_directory(parent)?;
        Ok(())
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct ProfileDocument {
    schema_version: u32,
    active_profile: Option<String>,
    profiles: BTreeMap<String, Profile>,
}

fn reject_symlink(path: &Path) -> Result<(), ProfileError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_symlink() => Err(ProfileError::SymlinkNotAllowed),
        Ok(_) => Ok(()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(ProfileError::Io(error)),
    }
}

#[cfg(unix)]
fn sync_directory(path: &Path) -> Result<(), ProfileError> {
    File::open(path)
        .and_then(|directory| directory.sync_all())
        .map_err(ProfileError::Io)
}

#[cfg(not(unix))]
fn sync_directory(_path: &Path) -> Result<(), ProfileError> {
    Ok(())
}

/// Invalid profile input, unsafe storage, or profile-store failure.
#[derive(Debug, Error)]
pub enum ProfileError {
    #[error(
        "profile name must start with a lowercase letter and contain at most 32 lowercase letters, digits, '-' or '_'"
    )]
    InvalidName,
    #[error("profile already exists")]
    AlreadyExists,
    #[error("profile not found")]
    NotFound,
    #[error("no active profile is configured")]
    NoActiveProfile,
    #[error("profile origin has not been explicitly trusted")]
    TrustRequired,
    #[error("profile locale is not supported")]
    UnsupportedLocale,
    #[error("custom CA certificate must be a regular non-symlink file")]
    InvalidCaCertificate,
    #[error("profile configuration path is invalid")]
    InvalidConfigPath,
    #[error("profile configuration directory is unavailable")]
    NoProjectDirectory,
    #[error("profile configuration is larger than one MiB")]
    ConfigTooLarge,
    #[error("profile configuration schema {0} is not supported")]
    UnsupportedSchema(u32),
    #[error("active profile does not name a stored profile")]
    InvalidActiveProfile,
    #[error("symbolic links are not allowed for CLI state files")]
    SymlinkNotAllowed,
    #[error("invalid API origin: {0}")]
    Origin(#[from] OriginError),
    #[error("profile configuration could not be read or written: {0}")]
    Io(#[source] io::Error),
    #[error("profile configuration is invalid: {0}")]
    InvalidDocument(#[source] toml::de::Error),
    #[error("profile configuration could not be serialized: {0}")]
    Serialize(#[source] toml::ser::Error),
}

#[cfg(test)]
mod tests {
    use std::error::Error;
    use std::fs;

    use tempfile::tempdir;

    use super::{Profile, ProfileError, ProfileName, ProfilePaths, ProfileStore};
    use crate::origin::ApiOrigin;

    fn profile() -> Result<Profile, Box<dyn Error>> {
        Ok(Profile::new(ApiOrigin::parse("https://example.com")?))
    }

    #[test]
    fn validates_profile_names() {
        assert!(ProfileName::parse("main-1").is_ok());
        assert!(ProfileName::parse("Main").is_err());
        assert!(ProfileName::parse("1main").is_err());
        assert!(ProfileName::parse("").is_err());
    }

    #[test]
    fn round_trips_and_selects_profiles() -> Result<(), Box<dyn Error>> {
        let directory = tempdir()?;
        let store = ProfileStore::new(ProfilePaths::under(directory.path()));
        let main = ProfileName::parse("main")?;
        let staging = ProfileName::parse("staging")?;
        store.add(&main, profile()?)?;
        store.add(&staging, profile()?)?;
        store.select(&staging)?;

        let loaded = store.load()?;
        assert_eq!(loaded.active_profile.as_deref(), Some("staging"));
        assert_eq!(loaded.profiles.len(), 2);
        Ok(())
    }

    #[test]
    fn removing_active_profile_selects_sorted_remainder() -> Result<(), Box<dyn Error>> {
        let directory = tempdir()?;
        let store = ProfileStore::new(ProfilePaths::under(directory.path()));
        let first = ProfileName::parse("first")?;
        let second = ProfileName::parse("second")?;
        store.add(&second, profile()?)?;
        store.add(&first, profile()?)?;
        let removed = store.remove(&second)?;
        assert_eq!(removed.active_profile.as_deref(), Some("first"));
        Ok(())
    }

    #[cfg(unix)]
    #[test]
    fn rejects_symlinked_configuration() -> Result<(), Box<dyn Error>> {
        use std::os::unix::fs::symlink;

        let directory = tempdir()?;
        let paths = ProfilePaths::under(directory.path());
        let target = directory.path().join("target.toml");
        fs::write(&target, "schema_version = 1\n")?;
        symlink(&target, &paths.config_file)?;
        let store = ProfileStore::new(paths);
        assert!(matches!(store.load(), Err(ProfileError::SymlinkNotAllowed)));
        Ok(())
    }
}
