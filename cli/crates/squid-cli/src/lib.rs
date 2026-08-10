//! Command parsing and dispatch for the `squid` executable.

use std::collections::BTreeMap;
use std::env;
use std::io::{self, BufRead, IsTerminal, Write};
use std::path::PathBuf;
use std::process::ExitCode;
use std::str::FromStr;

use clap::{CommandFactory, Parser, Subcommand, ValueEnum};
use clap_complete::{Shell, generate};
use serde::Serialize;
use squid_cli_core::exit::ExitStatus;
use squid_cli_core::locale::{Locale, LocalizedMessage, MessageKey, format_message};
use squid_cli_core::origin::{ApiOrigin, OriginError};
use squid_cli_core::output::{FailureEnvelope, SuccessEnvelope, write_json};
use squid_cli_core::profile::{
    EditorPreference, Profile, ProfileConfig, ProfileError, ProfileName, ProfileStore,
};
use squid_cli_core::terminal::sanitize_terminal_text;
use squid_cli_core::version::VersionInfo;

/// Parsed top-level command line.
#[derive(Debug, Parser)]
#[command(name = "squid", version, about = "Redstone Squid command-line client")]
pub struct Cli {
    /// Select the output contract used by this invocation.
    #[arg(long, global = true, value_enum, default_value_t = OutputFormat::Human)]
    output: OutputFormat,

    /// Select human-facing messages independently of machine field names.
    #[arg(long, global = true, value_name = "LOCALE")]
    locale: Option<String>,

    #[command(subcommand)]
    command: Command,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, ValueEnum)]
enum OutputFormat {
    Human,
    Json,
}

#[derive(Debug, Subcommand)]
enum Command {
    /// Generate a shell completion script.
    Completion {
        #[command(subcommand)]
        command: CompletionCommand,
    },
    /// Manage isolated API origins and local preferences.
    Profile {
        #[command(subcommand)]
        command: ProfileCommand,
    },
    /// Report CLI and submission-protocol compatibility.
    Version,
}

#[derive(Debug, Subcommand)]
enum CompletionCommand {
    /// Write a completion script to stdout.
    Generate {
        /// Shell whose completion syntax should be generated.
        #[arg(value_enum)]
        shell: Shell,
    },
}

#[derive(Debug, Subcommand)]
enum ProfileCommand {
    /// Add and explicitly trust one API origin.
    Add {
        /// Lowercase local profile name.
        name: String,
        /// Exact API origin; paths, queries, and credentials are rejected.
        #[arg(long)]
        origin: String,
        /// Confirm that this origin may receive profile-scoped credentials.
        #[arg(long)]
        trust: bool,
        /// PEM certificate used in addition to the system trust store.
        #[arg(long, value_name = "PATH")]
        ca_certificate: Option<PathBuf>,
        /// Default locale for connected commands using this profile.
        #[arg(long, value_name = "LOCALE")]
        default_locale: Option<String>,
        /// Preferred interactive form renderer.
        #[arg(long, value_enum, default_value_t = EditorArgument::Tui)]
        editor: EditorArgument,
        /// Disable notification-only release checks for this profile.
        #[arg(long)]
        no_update_checks: bool,
    },
    /// List configured profiles in deterministic name order.
    List,
    /// Show one profile, or the active profile when omitted.
    Show {
        /// Profile to show instead of the active profile.
        name: Option<String>,
    },
    /// Select the profile used by connected commands.
    Use {
        /// Existing profile name.
        name: String,
    },
    /// Remove local profile configuration.
    Remove {
        /// Existing profile name.
        name: String,
        /// Skip the interactive confirmation after reviewing the name.
        #[arg(long)]
        yes: bool,
    },
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, ValueEnum)]
enum EditorArgument {
    Tui,
    Prompt,
}

impl From<EditorArgument> for EditorPreference {
    fn from(value: EditorArgument) -> Self {
        match value {
            EditorArgument::Tui => Self::Tui,
            EditorArgument::Prompt => Self::Prompt,
        }
    }
}

#[derive(Debug)]
struct CommandFailure {
    status: ExitStatus,
    code: &'static str,
    message: LocalizedMessage,
    suggested_action: Option<LocalizedMessage>,
    field_errors: BTreeMap<String, String>,
}

impl CommandFailure {
    fn new(status: ExitStatus, code: &'static str, message: MessageKey) -> Self {
        Self {
            status,
            code,
            message: LocalizedMessage::new(message),
            suggested_action: None,
            field_errors: BTreeMap::new(),
        }
    }

    fn with_suggested_action(mut self, message: MessageKey) -> Self {
        self.suggested_action = Some(LocalizedMessage::new(message));
        self
    }

    fn with_message_value(mut self, key: impl Into<String>, value: impl Into<String>) -> Self {
        self.message = self.message.with(key, value);
        self
    }

    fn with_field_error(mut self, field: impl Into<String>, detail: impl Into<String>) -> Self {
        self.field_errors.insert(field.into(), detail.into());
        self
    }
}

/// Parse process arguments, run one command, and return its stable exit status.
#[must_use]
pub fn main_entry() -> ExitCode {
    let cli = Cli::parse();
    let format = cli.output;
    let command_name = command_name(&cli.command);
    let locale = match resolve_locale(cli.locale.as_deref()) {
        Ok(locale) => locale,
        Err(failure) => {
            let fallback = Locale::from_environment();
            let _ignored = write_failure(format, fallback, command_name, &failure, None);
            return failure.status.into();
        }
    };
    match run(cli, locale, &mut io::stdout()) {
        Ok(()) => ExitStatus::Success.into(),
        Err(RunError::Io(error)) if error.kind() == io::ErrorKind::BrokenPipe => {
            ExitStatus::Success.into()
        }
        Err(RunError::Io(error)) => {
            let failure = CommandFailure::new(
                ExitStatus::LocalState,
                "local_io_failed",
                MessageKey::LocalIoFailed,
            )
            .with_suggested_action(MessageKey::SuggestedCheckFilesystem);
            let _ignored = write_failure(format, locale, command_name, &failure, Some(&error));
            failure.status.into()
        }
        Err(RunError::Command(failure)) => {
            let _ignored = write_failure(format, locale, command_name, &failure, None);
            failure.status.into()
        }
    }
}

fn run(cli: Cli, locale: Locale, output: &mut impl Write) -> Result<(), RunError> {
    match cli.command {
        Command::Completion {
            command: CompletionCommand::Generate { shell },
        } => {
            if cli.output == OutputFormat::Json {
                return Err(CommandFailure::new(
                    ExitStatus::Usage,
                    "raw_output_required",
                    MessageKey::CompletionRequiresHumanOutput,
                )
                .into());
            }
            generate(shell, &mut Cli::command(), "squid", output);
            Ok(())
        }
        Command::Profile { command } => {
            let store = ProfileStore::discover().map_err(|error| profile_failure(error, None))?;
            run_profile(command, cli.output, locale, &store, output)
        }
        Command::Version => write_version(cli.output, locale, output).map_err(RunError::Io),
    }
}

fn run_profile(
    command: ProfileCommand,
    format: OutputFormat,
    locale: Locale,
    store: &ProfileStore,
    output: &mut impl Write,
) -> Result<(), RunError> {
    match command {
        ProfileCommand::Add {
            name,
            origin,
            trust,
            ca_certificate,
            default_locale,
            editor,
            no_update_checks,
        } => {
            let name = parse_profile_name(&name)?;
            if !trust {
                return Err(CommandFailure::new(
                    ExitStatus::Usage,
                    "profile_trust_required",
                    MessageKey::ProfileTrustRequired,
                )
                .into());
            }
            let origin = ApiOrigin::parse(&origin).map_err(origin_failure)?;
            let default_locale = default_locale
                .map(|value| {
                    Locale::from_str(&value)
                        .map(|parsed| String::from(parsed.code()))
                        .map_err(|_error| {
                            CommandFailure::new(
                                ExitStatus::Usage,
                                "invalid_profile_locale",
                                MessageKey::ProfileLocaleInvalid,
                            )
                            .with_field_error("default_locale", value)
                        })
                })
                .transpose()?;
            let mut profile = Profile::new(origin);
            profile.ca_certificate = ca_certificate;
            profile.locale = default_locale;
            profile.editor = editor.into();
            profile.update_checks = !no_update_checks;
            let config = store
                .add(&name, profile)
                .map_err(|error| profile_failure(error, Some(name.as_str())))?;
            let (_stored_name, stored) = config
                .resolve(Some(&name))
                .map_err(|error| profile_failure(error, Some(name.as_str())))?;
            let view = ProfileView::from_profile(
                name.as_str(),
                config.active_profile.as_deref() == Some(name.as_str()),
                stored,
            );
            write_profile_result(
                "profile.add",
                format,
                locale,
                LocalizedMessage::new(MessageKey::ProfileAdded)
                    .with("name", name.as_str())
                    .with("origin", stored.origin.as_str()),
                view,
                output,
            )?;
            Ok(())
        }
        ProfileCommand::List => {
            let config = store.load().map_err(|error| profile_failure(error, None))?;
            write_profile_list(format, locale, &config, output)?;
            Ok(())
        }
        ProfileCommand::Show { name } => {
            let parsed_name = name.as_deref().map(parse_profile_name).transpose()?;
            let config = store
                .load()
                .map_err(|error| profile_failure(error, name.as_deref()))?;
            let (stored_name, profile) = config
                .resolve(parsed_name.as_ref())
                .map_err(|error| profile_failure(error, name.as_deref()))?;
            let view = ProfileView::from_profile(
                stored_name,
                config.active_profile.as_deref() == Some(stored_name),
                profile,
            );
            write_profile_view("profile.show", format, locale, &view, output)?;
            Ok(())
        }
        ProfileCommand::Use { name } => {
            let name = parse_profile_name(&name)?;
            store
                .select(&name)
                .map_err(|error| profile_failure(error, Some(name.as_str())))?;
            write_profile_result(
                "profile.use",
                format,
                locale,
                LocalizedMessage::new(MessageKey::ProfileSelected).with("name", name.as_str()),
                ProfileMutation {
                    name: String::from(name.as_str()),
                    active_profile: Some(String::from(name.as_str())),
                    removed: false,
                },
                output,
            )?;
            Ok(())
        }
        ProfileCommand::Remove { name, yes } => {
            let name = parse_profile_name(&name)?;
            let should_remove = if yes {
                true
            } else {
                confirm_profile_removal(locale, name.as_str())?
            };
            if !should_remove {
                write_profile_result(
                    "profile.remove",
                    format,
                    locale,
                    LocalizedMessage::new(MessageKey::ProfileRemovalCancelled)
                        .with("name", name.as_str()),
                    ProfileMutation {
                        name: String::from(name.as_str()),
                        active_profile: store
                            .load()
                            .map_err(|error| profile_failure(error, Some(name.as_str())))?
                            .active_profile,
                        removed: false,
                    },
                    output,
                )?;
                return Ok(());
            }
            let config = store
                .remove(&name)
                .map_err(|error| profile_failure(error, Some(name.as_str())))?;
            write_profile_result(
                "profile.remove",
                format,
                locale,
                LocalizedMessage::new(MessageKey::ProfileRemoved).with("name", name.as_str()),
                ProfileMutation {
                    name: String::from(name.as_str()),
                    active_profile: config.active_profile,
                    removed: true,
                },
                output,
            )?;
            Ok(())
        }
    }
}

#[derive(Debug, Serialize)]
struct ProfileView {
    name: String,
    active: bool,
    origin: String,
    trusted: bool,
    ca_certificate: Option<String>,
    locale: Option<String>,
    editor: &'static str,
    update_checks: bool,
}

impl ProfileView {
    fn from_profile(name: &str, active: bool, profile: &Profile) -> Self {
        Self {
            name: String::from(name),
            active,
            origin: String::from(profile.origin.as_str()),
            trusted: profile.trusted,
            ca_certificate: profile
                .ca_certificate
                .as_ref()
                .map(|path| path.to_string_lossy().into_owned()),
            locale: profile.locale.clone(),
            editor: match profile.editor {
                EditorPreference::Tui => "tui",
                EditorPreference::Prompt => "prompt",
            },
            update_checks: profile.update_checks,
        }
    }
}

#[derive(Debug, Serialize)]
struct ProfileList {
    active_profile: Option<String>,
    profiles: Vec<ProfileView>,
}

#[derive(Debug, Serialize)]
struct ProfileMutation {
    name: String,
    active_profile: Option<String>,
    removed: bool,
}

fn write_profile_result(
    command: &'static str,
    format: OutputFormat,
    locale: Locale,
    message: LocalizedMessage,
    data: impl Serialize,
    output: &mut impl Write,
) -> io::Result<()> {
    match format {
        OutputFormat::Human => writeln!(output, "{}", message.render(locale)),
        OutputFormat::Json => write_json(&SuccessEnvelope::new(command, data), output),
    }
}

fn write_profile_list(
    format: OutputFormat,
    locale: Locale,
    config: &ProfileConfig,
    output: &mut impl Write,
) -> io::Result<()> {
    let profiles = config
        .profiles
        .iter()
        .map(|(name, profile)| {
            ProfileView::from_profile(
                name,
                config.active_profile.as_deref() == Some(name.as_str()),
                profile,
            )
        })
        .collect::<Vec<_>>();
    if format == OutputFormat::Human {
        if profiles.is_empty() {
            return writeln!(output, "{}", locale.message(MessageKey::ProfileListEmpty));
        }
        for profile in &profiles {
            write_human_profile(locale, profile, output)?;
        }
        return Ok(());
    }
    write_json(
        &SuccessEnvelope::new(
            "profile.list",
            ProfileList {
                active_profile: config.active_profile.clone(),
                profiles,
            },
        ),
        output,
    )
}

fn write_profile_view(
    command: &'static str,
    format: OutputFormat,
    locale: Locale,
    profile: &ProfileView,
    output: &mut impl Write,
) -> io::Result<()> {
    match format {
        OutputFormat::Human => write_human_profile(locale, profile, output),
        OutputFormat::Json => write_json(&SuccessEnvelope::new(command, profile), output),
    }
}

fn write_human_profile(
    locale: Locale,
    profile: &ProfileView,
    output: &mut impl Write,
) -> io::Result<()> {
    let active = if profile.active {
        MessageKey::ProfileActiveMarker
    } else {
        MessageKey::ProfileInactiveMarker
    };
    let profile_locale = profile.locale.as_deref().map_or_else(
        || locale.message(MessageKey::ProfileDefaultLocale),
        |value| value,
    );
    let editor = match profile.editor {
        "tui" => locale.message(MessageKey::EditorTui),
        _ => locale.message(MessageKey::EditorPrompt),
    };
    let update_checks = if profile.update_checks {
        MessageKey::Enabled
    } else {
        MessageKey::Disabled
    };
    let ca_certificate = profile.ca_certificate.as_deref().map_or_else(
        || String::from(locale.message(MessageKey::ProfileNoCustomCa)),
        sanitize_terminal_text,
    );
    writeln!(
        output,
        "{}",
        LocalizedMessage::new(MessageKey::ProfileDetails)
            .with("active", locale.message(active))
            .with("name", sanitize_terminal_text(&profile.name))
            .with("origin", sanitize_terminal_text(&profile.origin))
            .with("locale", sanitize_terminal_text(profile_locale))
            .with("editor", editor)
            .with("update_checks", locale.message(update_checks))
            .with("ca_certificate", ca_certificate)
            .render(locale),
    )
}

fn confirm_profile_removal(locale: Locale, name: &str) -> Result<bool, RunError> {
    let stdin = io::stdin();
    if !stdin.is_terminal() {
        return Err(CommandFailure::new(
            ExitStatus::Usage,
            "confirmation_required",
            MessageKey::ProfileConfirmationRequired,
        )
        .with_suggested_action(MessageKey::SuggestedUseYes)
        .into());
    }
    let prompt = LocalizedMessage::new(MessageKey::ConfirmProfileRemoval)
        .with("name", sanitize_terminal_text(name))
        .render(locale);
    let mut stderr = io::stderr().lock();
    write!(stderr, "{prompt}")?;
    stderr.flush()?;
    let mut response = String::new();
    stdin.lock().read_line(&mut response)?;
    Ok(response.trim().eq_ignore_ascii_case("yes"))
}

fn parse_profile_name(value: &str) -> Result<ProfileName, CommandFailure> {
    ProfileName::parse(value).map_err(|_error| {
        CommandFailure::new(
            ExitStatus::Usage,
            "invalid_profile_name",
            MessageKey::InvalidProfileName,
        )
        .with_field_error("name", sanitize_terminal_text(value))
    })
}

fn origin_failure(error: OriginError) -> CommandFailure {
    let message = match error {
        OriginError::InvalidUrl => MessageKey::OriginInvalidUrl,
        OriginError::UnsupportedScheme => MessageKey::OriginSchemeInvalid,
        OriginError::CredentialsNotAllowed => MessageKey::OriginCredentialsRejected,
        OriginError::OriginOnly => MessageKey::OriginComponentsRejected,
        OriginError::MissingHost => MessageKey::OriginHostMissing,
        OriginError::MissingPort => MessageKey::OriginPortMissing,
        OriginError::HttpsRequired => MessageKey::OriginHttpsRequired,
    };
    CommandFailure::new(ExitStatus::Usage, "invalid_api_origin", message)
}

fn profile_failure(error: ProfileError, name: Option<&str>) -> CommandFailure {
    match error {
        ProfileError::InvalidName => CommandFailure::new(
            ExitStatus::Usage,
            "invalid_profile_name",
            MessageKey::InvalidProfileName,
        ),
        ProfileError::AlreadyExists => CommandFailure::new(
            ExitStatus::LocalState,
            "profile_already_exists",
            MessageKey::ProfileAlreadyExists,
        )
        .with_message_value("name", name.unwrap_or("")),
        ProfileError::NotFound => CommandFailure::new(
            ExitStatus::LocalState,
            "profile_not_found",
            MessageKey::ProfileNotFound,
        )
        .with_message_value("name", name.unwrap_or("")),
        ProfileError::NoActiveProfile => CommandFailure::new(
            ExitStatus::LocalState,
            "no_active_profile",
            MessageKey::NoActiveProfile,
        ),
        ProfileError::TrustRequired => CommandFailure::new(
            ExitStatus::Usage,
            "profile_trust_required",
            MessageKey::ProfileTrustRequired,
        ),
        ProfileError::UnsupportedLocale => CommandFailure::new(
            ExitStatus::Usage,
            "invalid_profile_locale",
            MessageKey::ProfileLocaleInvalid,
        ),
        ProfileError::InvalidCaCertificate => CommandFailure::new(
            ExitStatus::Usage,
            "invalid_ca_certificate",
            MessageKey::ProfileCaInvalid,
        ),
        ProfileError::SymlinkNotAllowed => CommandFailure::new(
            ExitStatus::Security,
            "profile_symlink_rejected",
            MessageKey::ProfileSymlinkRejected,
        ),
        ProfileError::Origin(error) => origin_failure(error),
        ProfileError::Io(error) => CommandFailure::new(
            ExitStatus::LocalState,
            "profile_storage_failed",
            MessageKey::ProfileStorageFailed,
        )
        .with_suggested_action(MessageKey::SuggestedCheckFilesystem)
        .with_field_error("profile_store", sanitize_terminal_text(&error.to_string())),
        other => CommandFailure::new(
            ExitStatus::LocalState,
            "invalid_profile_config",
            MessageKey::ProfileConfigInvalid,
        )
        .with_field_error("profile_store", sanitize_terminal_text(&other.to_string())),
    }
}

fn write_version(format: OutputFormat, locale: Locale, output: &mut impl Write) -> io::Result<()> {
    let version = VersionInfo::current();
    match format {
        OutputFormat::Human => {
            let minimum = version.minimum_protocol.to_string();
            let maximum = version.maximum_protocol.to_string();
            writeln!(
                output,
                "{}",
                format_message(
                    locale.message(MessageKey::VersionLine),
                    &[
                        ("version", version.cli_version),
                        ("target", version.target),
                        ("minimum", &minimum),
                        ("maximum", &maximum),
                    ],
                ),
            )
        }
        OutputFormat::Json => write_json(&SuccessEnvelope::new("version", version), output),
    }
}

fn write_failure(
    format: OutputFormat,
    locale: Locale,
    command: &str,
    failure: &CommandFailure,
    source: Option<&io::Error>,
) -> io::Result<()> {
    let message = failure.message.render(locale);
    match format {
        OutputFormat::Human => {
            let mut stderr = io::stderr().lock();
            writeln!(stderr, "squid: {message}")?;
            if let Some(source) = source {
                writeln!(
                    stderr,
                    "  {}",
                    squid_cli_core::terminal::sanitize_terminal_text(&source.to_string()),
                )?;
            }
            for detail in failure.field_errors.values() {
                writeln!(stderr, "  {}", sanitize_terminal_text(detail))?;
            }
            if let Some(action) = &failure.suggested_action {
                writeln!(stderr, "  {}", action.render(locale))?;
            }
            Ok(())
        }
        OutputFormat::Json => {
            let mut envelope = FailureEnvelope::new(command, failure.code, message);
            envelope.error.suggested_action = failure
                .suggested_action
                .as_ref()
                .map(|action| action.render(locale));
            envelope.error.field_errors = failure.field_errors.clone();
            if let Some(source) = source {
                envelope.error.field_errors.insert(
                    String::from("local_io"),
                    squid_cli_core::terminal::sanitize_terminal_text(&source.to_string()),
                );
            }
            write_json(&envelope, &mut io::stdout().lock())
        }
    }
}

fn resolve_locale(explicit: Option<&str>) -> Result<Locale, CommandFailure> {
    if let Some(value) = explicit {
        return Locale::from_str(value).map_err(|_error| {
            CommandFailure::new(
                ExitStatus::Usage,
                "unsupported_locale",
                MessageKey::UnsupportedLocale,
            )
        });
    }
    if let Ok(value) = env::var("SQUID_LOCALE") {
        return Locale::from_str(&value).map_err(|_error| {
            CommandFailure::new(
                ExitStatus::LocalState,
                "unsupported_locale",
                MessageKey::UnsupportedLocale,
            )
        });
    }
    Ok(Locale::from_environment())
}

const fn command_name(command: &Command) -> &'static str {
    match command {
        Command::Completion { .. } => "completion.generate",
        Command::Profile { command } => match command {
            ProfileCommand::Add { .. } => "profile.add",
            ProfileCommand::List => "profile.list",
            ProfileCommand::Show { .. } => "profile.show",
            ProfileCommand::Use { .. } => "profile.use",
            ProfileCommand::Remove { .. } => "profile.remove",
        },
        Command::Version => "version",
    }
}

#[derive(Debug)]
enum RunError {
    Io(io::Error),
    Command(CommandFailure),
}

impl From<CommandFailure> for RunError {
    fn from(value: CommandFailure) -> Self {
        Self::Command(value)
    }
}

impl From<io::Error> for RunError {
    fn from(value: io::Error) -> Self {
        Self::Io(value)
    }
}

#[cfg(test)]
mod tests {
    use clap::Parser;
    use tempfile::tempdir;

    use super::{Cli, Command, Locale, OutputFormat, resolve_locale, run, run_profile};
    use squid_cli_core::profile::{ProfilePaths, ProfileStore};

    #[test]
    fn version_has_human_output() {
        let cli = Cli::try_parse_from(["squid", "version"]);
        assert!(cli.is_ok(), "command did not parse: {cli:?}");
        if let Ok(cli) = cli {
            let mut output = Vec::new();
            let result = run(cli, Locale::En, &mut output);
            assert!(result.is_ok(), "version command failed: {result:?}");
            let rendered = String::from_utf8(output);
            assert!(
                rendered.is_ok(),
                "version output was not UTF-8: {rendered:?}"
            );
            if let Ok(rendered) = rendered {
                assert!(rendered.starts_with("squid "));
                assert!(rendered.contains("submission protocol"));
            }
        }
    }

    #[test]
    fn version_has_json_envelope() {
        let cli = Cli::try_parse_from(["squid", "--output", "json", "version"]);
        assert!(cli.is_ok(), "command did not parse: {cli:?}");
        if let Ok(cli) = cli {
            let mut output = Vec::new();
            let result = run(cli, Locale::ZhCn, &mut output);
            assert!(result.is_ok(), "version command failed: {result:?}");
            let value = serde_json::from_slice::<serde_json::Value>(&output);
            assert!(value.is_ok(), "version output was not JSON: {value:?}");
            assert_eq!(
                value
                    .as_ref()
                    .ok()
                    .and_then(|item| item.get("schema_version"))
                    .and_then(serde_json::Value::as_u64),
                Some(1),
            );
            assert_eq!(
                value
                    .as_ref()
                    .ok()
                    .and_then(|item| item.get("command"))
                    .and_then(serde_json::Value::as_str),
                Some("version"),
            );
        }
    }

    #[test]
    fn completion_rejects_json_wrapping() {
        let cli = Cli::try_parse_from([
            "squid",
            "--output",
            "json",
            "completion",
            "generate",
            "bash",
        ]);
        assert!(cli.is_ok(), "command did not parse: {cli:?}");
        if let Ok(cli) = cli {
            let result = run(cli, Locale::En, &mut Vec::new());
            assert!(result.is_err());
        }
    }

    #[test]
    fn explicit_unsupported_locale_is_rejected() {
        let result = resolve_locale(Some("is-IS"));
        assert!(result.is_err());
    }

    #[test]
    fn profile_lifecycle_has_stable_json() {
        let directory = tempdir();
        assert!(
            directory.is_ok(),
            "temporary directory failed: {directory:?}"
        );
        if let Ok(directory) = directory {
            let store = ProfileStore::new(ProfilePaths::under(directory.path()));
            let add = Cli::try_parse_from([
                "squid",
                "--output",
                "json",
                "profile",
                "add",
                "local",
                "--origin",
                "http://127.0.0.1:8000",
                "--trust",
                "--default-locale",
                "zh-CN",
                "--editor",
                "prompt",
                "--no-update-checks",
            ]);
            assert!(add.is_ok(), "profile add did not parse: {add:?}");
            if let Ok(add) = add {
                if let Command::Profile { command } = add.command {
                    let mut output = Vec::new();
                    let result =
                        run_profile(command, OutputFormat::Json, Locale::En, &store, &mut output);
                    assert!(result.is_ok(), "profile add failed: {result:?}");
                    let value = serde_json::from_slice::<serde_json::Value>(&output);
                    assert!(value.is_ok(), "profile add was not JSON: {value:?}");
                    if let Ok(value) = value {
                        assert_eq!(value["command"], "profile.add");
                        assert_eq!(value["data"]["name"], "local");
                        assert_eq!(value["data"]["origin"], "http://127.0.0.1:8000");
                        assert_eq!(value["data"]["locale"], "zh-CN");
                        assert_eq!(value["data"]["editor"], "prompt");
                        assert_eq!(value["data"]["update_checks"], false);
                    }
                }
            }

            let list = Cli::try_parse_from(["squid", "profile", "list"]);
            assert!(list.is_ok(), "profile list did not parse: {list:?}");
            if let Ok(list) = list {
                if let Command::Profile { command } = list.command {
                    let mut output = Vec::new();
                    let result = run_profile(
                        command,
                        OutputFormat::Human,
                        Locale::ZhCn,
                        &store,
                        &mut output,
                    );
                    assert!(result.is_ok(), "profile list failed: {result:?}");
                    let rendered = String::from_utf8(output);
                    assert!(rendered.is_ok(), "profile list was not UTF-8: {rendered:?}");
                    if let Ok(rendered) = rendered {
                        assert!(rendered.contains("* local"));
                        assert!(rendered.contains("来源：http://127.0.0.1:8000"));
                    }
                }
            }
        }
    }

    #[test]
    fn adding_profile_requires_explicit_trust() {
        let directory = tempdir();
        assert!(
            directory.is_ok(),
            "temporary directory failed: {directory:?}"
        );
        if let Ok(directory) = directory {
            let store = ProfileStore::new(ProfilePaths::under(directory.path()));
            let cli = Cli::try_parse_from([
                "squid",
                "profile",
                "add",
                "main",
                "--origin",
                "https://example.com",
            ]);
            assert!(cli.is_ok(), "profile add did not parse: {cli:?}");
            if let Ok(cli) = cli {
                if let Command::Profile { command } = cli.command {
                    let result = run_profile(
                        command,
                        OutputFormat::Human,
                        Locale::En,
                        &store,
                        &mut Vec::new(),
                    );
                    assert!(result.is_err());
                    let loaded = store.load();
                    assert!(loaded.is_ok(), "profile store failed: {loaded:?}");
                    if let Ok(loaded) = loaded {
                        assert!(loaded.profiles.is_empty());
                    }
                }
            }
        }
    }
}
