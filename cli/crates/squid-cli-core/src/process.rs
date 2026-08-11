//! Shell-free child-process supervision and bounded external text editing.

use std::ffi::{OsStr, OsString};
use std::fmt;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
#[cfg(unix)]
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::Path;
use std::process::{Command, ExitStatus, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use tempfile::TempDir;
use thiserror::Error;

const MAXIMUM_COMMAND_BYTES: usize = 16 * 1024;
const MAXIMUM_ARGUMENTS: usize = 128;
const DEFAULT_MAXIMUM_DOCUMENT_BYTES: u64 = 1024 * 1024;
const POLL_INTERVAL: Duration = Duration::from_millis(20);

/// An executable and arguments parsed without shell evaluation.
#[derive(Clone, Eq, PartialEq)]
pub struct CommandSpec {
    program: OsString,
    arguments: Vec<OsString>,
}

impl CommandSpec {
    /// Construct a command from an explicit executable path or name.
    pub fn new(program: impl Into<OsString>) -> Result<Self, ProcessError> {
        let program = program.into();
        if program.is_empty() {
            return Err(ProcessError::EmptyCommand);
        }
        Ok(Self {
            program,
            arguments: Vec::new(),
        })
    }

    /// Parse a conventional `VISUAL` or `EDITOR` value without expansions or execution by a shell.
    pub fn parse(value: &str) -> Result<Self, ProcessError> {
        if value.len() > MAXIMUM_COMMAND_BYTES {
            return Err(ProcessError::CommandTooLarge);
        }
        let mut words = shell_words::split(value).map_err(ProcessError::InvalidCommand)?;
        if words.is_empty() {
            return Err(ProcessError::EmptyCommand);
        }
        if words.len() > MAXIMUM_ARGUMENTS + 1 {
            return Err(ProcessError::TooManyArguments);
        }
        let arguments = words.split_off(1).into_iter().map(OsString::from).collect();
        Ok(Self {
            program: OsString::from(&words[0]),
            arguments,
        })
    }

    /// Append one literal argument.
    #[must_use]
    pub fn with_argument(mut self, argument: impl Into<OsString>) -> Self {
        self.arguments.push(argument.into());
        self
    }

    /// Executable selected by this command.
    #[must_use]
    pub fn program(&self) -> &OsStr {
        &self.program
    }

    /// Literal argument vector passed directly to the executable.
    #[must_use]
    pub fn arguments(&self) -> &[OsString] {
        &self.arguments
    }
}

impl fmt::Debug for CommandSpec {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("CommandSpec")
            .field("program", &self.program)
            .field("argument_count", &self.arguments.len())
            .finish()
    }
}

/// Child standard-I/O behavior chosen by the caller.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ChildStdio {
    /// Connect the child to this process's terminal streams.
    Inherit,
    /// Disconnect the child from terminal streams.
    Null,
}

/// Result of a child that was successfully spawned and reaped.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ChildOutcome {
    /// The child returned a successful status.
    Success,
    /// The child returned a non-success status.
    Failed {
        /// Portable numeric exit code, when the platform reported one.
        code: Option<i32>,
    },
    /// The deadline elapsed and the child was terminated and reaped.
    TimedOut,
}

/// Run one command directly and always wait for its process to be reaped.
pub fn run_supervised(
    specification: &CommandSpec,
    timeout: Option<Duration>,
    stdio: ChildStdio,
) -> Result<ChildOutcome, ProcessError> {
    let mut command = Command::new(&specification.program);
    command.args(&specification.arguments);
    match stdio {
        ChildStdio::Inherit => {
            command
                .stdin(Stdio::inherit())
                .stdout(Stdio::inherit())
                .stderr(Stdio::inherit());
        }
        ChildStdio::Null => {
            command
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null());
        }
    }

    let mut child = command.spawn().map_err(ProcessError::Spawn)?;
    let Some(timeout) = timeout else {
        return child
            .wait()
            .map(classify_status)
            .map_err(ProcessError::Wait);
    };
    let deadline = Instant::now().checked_add(timeout);
    loop {
        if let Some(status) = child.try_wait().map_err(ProcessError::Wait)? {
            return Ok(classify_status(status));
        }
        if deadline.is_none_or(|deadline| Instant::now() >= deadline) {
            if let Err(error) = child.kill() {
                if error.kind() != io::ErrorKind::InvalidInput {
                    let _reaped = child.wait();
                    return Err(ProcessError::Terminate(error));
                }
            }
            child.wait().map_err(ProcessError::Wait)?;
            return Ok(ChildOutcome::TimedOut);
        }
        let remaining = deadline
            .and_then(|deadline| deadline.checked_duration_since(Instant::now()))
            .unwrap_or_default();
        thread::sleep(POLL_INTERVAL.min(remaining));
    }
}

fn classify_status(status: ExitStatus) -> ChildOutcome {
    if status.success() {
        ChildOutcome::Success
    } else {
        ChildOutcome::Failed {
            code: status.code(),
        }
    }
}

/// Bounded external editor that exchanges text through an owner-readable temporary file.
#[derive(Clone, Debug)]
pub struct ExternalTextEditor {
    command: CommandSpec,
    maximum_document_bytes: u64,
}

impl ExternalTextEditor {
    /// Construct an editor with a one-MiB document limit.
    #[must_use]
    pub const fn new(command: CommandSpec) -> Self {
        Self {
            command,
            maximum_document_bytes: DEFAULT_MAXIMUM_DOCUMENT_BYTES,
        }
    }

    /// Override the document limit for a known server field or a test.
    #[must_use]
    pub const fn with_maximum_document_bytes(mut self, maximum: u64) -> Self {
        self.maximum_document_bytes = maximum;
        self
    }

    /// Edit one UTF-8 document, rejecting replacement with a symlink or oversized output.
    pub fn edit(&self, initial: &str, timeout: Option<Duration>) -> Result<String, EditorError> {
        if initial.len() as u64 > self.maximum_document_bytes {
            return Err(EditorError::DocumentTooLarge);
        }
        let directory = TempDir::new().map_err(EditorError::Io)?;
        let path = directory.path().join("submission.txt");
        write_initial_document(&path, initial)?;
        let command = self.command.clone().with_argument(path.as_os_str());
        match run_supervised(&command, timeout, ChildStdio::Inherit)? {
            ChildOutcome::Success => read_edited_document(&path, self.maximum_document_bytes),
            ChildOutcome::Failed { code } => Err(EditorError::ChildFailed(code)),
            ChildOutcome::TimedOut => Err(EditorError::TimedOut),
        }
    }
}

fn write_initial_document(path: &Path, initial: &str) -> Result<(), EditorError> {
    let mut options = OpenOptions::new();
    options.create_new(true).write(true);
    #[cfg(unix)]
    options.mode(0o600);
    let mut file = options.open(path).map_err(EditorError::Io)?;
    file.write_all(initial.as_bytes())
        .map_err(EditorError::Io)?;
    file.flush().map_err(EditorError::Io)?;
    #[cfg(unix)]
    file.set_permissions(fs::Permissions::from_mode(0o600))
        .map_err(EditorError::Io)?;
    Ok(())
}

fn read_edited_document(path: &Path, maximum: u64) -> Result<String, EditorError> {
    let metadata = fs::symlink_metadata(path).map_err(EditorError::Io)?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(EditorError::UnsafeDocument);
    }
    if metadata.len() > maximum {
        return Err(EditorError::DocumentTooLarge);
    }
    let mut bytes = Vec::with_capacity(usize::try_from(metadata.len()).unwrap_or(0));
    File::open(path)
        .map_err(EditorError::Io)?
        .take(maximum.saturating_add(1))
        .read_to_end(&mut bytes)
        .map_err(EditorError::Io)?;
    if bytes.len() as u64 > maximum {
        return Err(EditorError::DocumentTooLarge);
    }
    String::from_utf8(bytes).map_err(|_error| EditorError::InvalidUtf8)
}

/// Failures before or while supervising a local child process.
#[derive(Debug, Error)]
pub enum ProcessError {
    #[error("the command is empty")]
    EmptyCommand,
    #[error("the command string is too large")]
    CommandTooLarge,
    #[error("the command contains too many arguments")]
    TooManyArguments,
    #[error("the command has invalid quoting: {0}")]
    InvalidCommand(shell_words::ParseError),
    #[error("the child process could not be started: {0}")]
    Spawn(io::Error),
    #[error("the child process could not be waited on: {0}")]
    Wait(io::Error),
    #[error("the child process could not be terminated: {0}")]
    Terminate(io::Error),
}

/// Failures from the external text editor boundary.
#[derive(Debug, Error)]
pub enum EditorError {
    #[error("the editor document exceeds the configured size limit")]
    DocumentTooLarge,
    #[error("the editor replaced the document with an unsafe file type")]
    UnsafeDocument,
    #[error("the editor document is not valid UTF-8")]
    InvalidUtf8,
    #[error("the editor exited unsuccessfully with code {0:?}")]
    ChildFailed(Option<i32>),
    #[error("the editor exceeded its deadline")]
    TimedOut,
    #[error("editor file access failed: {0}")]
    Io(io::Error),
    #[error(transparent)]
    Process(#[from] ProcessError),
}

#[cfg(test)]
mod tests {
    use std::ffi::{OsStr, OsString};
    use std::io;
    use std::time::Duration;

    use super::{
        ChildOutcome, ChildStdio, CommandSpec, ExternalTextEditor, ProcessError, run_supervised,
    };

    #[test]
    fn parses_editor_arguments_without_shell_expansion() {
        let command = CommandSpec::parse(r#"code --wait "a file" '$(touch nope)'"#);
        assert!(command.is_ok(), "command should parse: {command:?}");
        let command = match command {
            Ok(command) => command,
            Err(_) => return,
        };
        assert_eq!(command.program(), OsStr::new("code"));
        assert_eq!(
            command.arguments(),
            ["--wait", "a file", "$(touch nope)"].map(OsString::from),
        );
    }

    #[test]
    fn rejects_invalid_or_excessive_commands() {
        assert!(matches!(
            CommandSpec::parse("'unterminated"),
            Err(ProcessError::InvalidCommand(_))
        ));
        assert!(matches!(
            CommandSpec::parse(&"x ".repeat(130)),
            Err(ProcessError::TooManyArguments)
        ));
    }

    #[test]
    fn reports_successful_child() {
        let executable = std::env::current_exe();
        assert!(
            executable.is_ok(),
            "test executable should resolve: {executable:?}"
        );
        let command = match executable.and_then(|path| {
            CommandSpec::new(path).map_err(|error| io::Error::other(error.to_string()))
        }) {
            Ok(command) => command.with_argument("--list"),
            Err(_) => return,
        };
        let outcome = run_supervised(&command, Some(Duration::from_secs(10)), ChildStdio::Null);
        assert_eq!(outcome.ok(), Some(ChildOutcome::Success));
    }

    #[test]
    fn terminates_and_reaps_timed_out_child() {
        let executable = std::env::current_exe();
        assert!(
            executable.is_ok(),
            "test executable should resolve: {executable:?}"
        );
        let command = match executable.and_then(|path| {
            CommandSpec::new(path).map_err(|error| io::Error::other(error.to_string()))
        }) {
            Ok(command) => command
                .with_argument("--ignored")
                .with_argument("--exact")
                .with_argument("process::tests::slow_child_helper"),
            Err(_) => return,
        };
        let outcome = run_supervised(&command, Some(Duration::from_millis(50)), ChildStdio::Null);
        assert_eq!(outcome.ok(), Some(ChildOutcome::TimedOut));
    }

    #[test]
    #[ignore = "spawned by terminates_and_reaps_timed_out_child"]
    fn slow_child_helper() {
        std::thread::sleep(Duration::from_secs(5));
    }

    #[cfg(unix)]
    #[test]
    fn external_editor_returns_bounded_utf8() {
        let command = CommandSpec::new("/bin/sh")
            .map(|command| {
                command
                    .with_argument("-c")
                    .with_argument("printf edited > \"$1\"")
                    .with_argument("squid-editor")
            })
            .map(ExternalTextEditor::new);
        assert!(command.is_ok(), "editor command should build: {command:?}");
        let result = match command {
            Ok(editor) => editor.edit("initial", Some(Duration::from_secs(5))),
            Err(_) => return,
        };
        assert_eq!(result.ok().as_deref(), Some("edited"));
    }
}
