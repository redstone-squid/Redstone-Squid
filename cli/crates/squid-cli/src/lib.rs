//! Command parsing and dispatch for the `squid` executable.

use std::collections::BTreeMap;
use std::env;
use std::io::{self, Write};
use std::process::ExitCode;
use std::str::FromStr;

use clap::{CommandFactory, Parser, Subcommand, ValueEnum};
use clap_complete::{Shell, generate};
use squid_cli_core::exit::ExitStatus;
use squid_cli_core::locale::{Locale, MessageKey, format_message};
use squid_cli_core::output::{FailureEnvelope, SuccessEnvelope, write_json};
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

#[derive(Debug)]
struct CommandFailure {
    status: ExitStatus,
    code: &'static str,
    message: MessageKey,
    suggested_action: Option<MessageKey>,
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
            let failure = CommandFailure {
                status: ExitStatus::LocalState,
                code: "local_io_failed",
                message: MessageKey::LocalIoFailed,
                suggested_action: Some(MessageKey::SuggestedCheckFilesystem),
            };
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
                return Err(CommandFailure {
                    status: ExitStatus::Usage,
                    code: "raw_output_required",
                    message: MessageKey::CompletionRequiresHumanOutput,
                    suggested_action: None,
                }
                .into());
            }
            generate(shell, &mut Cli::command(), "squid", output);
            Ok(())
        }
        Command::Version => write_version(cli.output, locale, output).map_err(RunError::Io),
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
    let message = locale.message(failure.message);
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
            if let Some(action) = failure.suggested_action {
                writeln!(stderr, "  {}", locale.message(action))?;
            }
            Ok(())
        }
        OutputFormat::Json => {
            let mut envelope = FailureEnvelope::new(command, failure.code, message);
            envelope.error.suggested_action = failure
                .suggested_action
                .map(|action| String::from(locale.message(action)));
            if let Some(source) = source {
                envelope.error.field_errors = BTreeMap::from([(
                    String::from("local_io"),
                    squid_cli_core::terminal::sanitize_terminal_text(&source.to_string()),
                )]);
            }
            write_json(&envelope, &mut io::stdout().lock())
        }
    }
}

fn resolve_locale(explicit: Option<&str>) -> Result<Locale, CommandFailure> {
    if let Some(value) = explicit {
        return Locale::from_str(value).map_err(|_error| CommandFailure {
            status: ExitStatus::Usage,
            code: "unsupported_locale",
            message: MessageKey::UnsupportedLocale,
            suggested_action: None,
        });
    }
    if let Ok(value) = env::var("SQUID_LOCALE") {
        return Locale::from_str(&value).map_err(|_error| CommandFailure {
            status: ExitStatus::LocalState,
            code: "unsupported_locale",
            message: MessageKey::UnsupportedLocale,
            suggested_action: None,
        });
    }
    Ok(Locale::from_environment())
}

const fn command_name(command: &Command) -> &'static str {
    match command {
        Command::Completion { .. } => "completion.generate",
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

#[cfg(test)]
mod tests {
    use clap::Parser;

    use super::{Cli, Locale, resolve_locale, run};

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
}
