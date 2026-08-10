//! `squid` command-line entry point.

use std::io::{self, Write};
use std::process::ExitCode;

use clap::{Parser, Subcommand, ValueEnum};
use squid_cli_core::version::VersionInfo;

#[derive(Debug, Parser)]
#[command(name = "squid", version, about = "Redstone Squid command-line client")]
struct Cli {
    /// Select the output contract used by this invocation.
    #[arg(long, global = true, value_enum, default_value_t = OutputFormat::Human)]
    output: OutputFormat,

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
    /// Report CLI and submission-protocol compatibility.
    Version,
}

fn main() -> ExitCode {
    let cli = Cli::parse();
    match run(cli, &mut io::stdout()) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) if error.kind() == io::ErrorKind::BrokenPipe => ExitCode::SUCCESS,
        Err(error) => {
            let _ignored = writeln!(io::stderr(), "squid: {error}");
            ExitCode::from(3)
        }
    }
}

fn run(cli: Cli, output: &mut impl Write) -> io::Result<()> {
    match cli.command {
        Command::Version => write_version(cli.output, output),
    }
}

fn write_version(format: OutputFormat, output: &mut impl Write) -> io::Result<()> {
    let version = VersionInfo::current();
    match format {
        OutputFormat::Human => writeln!(
            output,
            "squid {} ({}; submission protocol {}..={})",
            version.cli_version, version.target, version.minimum_protocol, version.maximum_protocol,
        ),
        OutputFormat::Json => serde_json::to_writer(&mut *output, &version)
            .map_err(io::Error::other)
            .and_then(|()| writeln!(output)),
    }
}

#[cfg(test)]
mod tests {
    use super::{Cli, Command, OutputFormat, run};

    #[test]
    fn version_has_human_output() {
        let mut output = Vec::new();
        let result = run(
            Cli {
                output: OutputFormat::Human,
                command: Command::Version,
            },
            &mut output,
        );
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

    #[test]
    fn version_has_json_output() {
        let mut output = Vec::new();
        let result = run(
            Cli {
                output: OutputFormat::Json,
                command: Command::Version,
            },
            &mut output,
        );
        assert!(result.is_ok(), "version command failed: {result:?}");

        let value = serde_json::from_slice::<serde_json::Value>(&output);
        assert!(value.is_ok(), "version output was not JSON: {value:?}");
        assert_eq!(
            value
                .as_ref()
                .ok()
                .and_then(|item| item.get("minimum_protocol"))
                .and_then(serde_json::Value::as_u64),
            Some(1),
        );
        assert_eq!(
            value
                .as_ref()
                .ok()
                .and_then(|item| item.get("maximum_protocol"))
                .and_then(serde_json::Value::as_u64),
            Some(1),
        );
    }
}
